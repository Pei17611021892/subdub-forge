from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .media_service import _resolve_tool


TTS_WORDS_PER_SECOND = 2.45
TTS_SYLLABLES_PER_SECOND = 4.2
MIN_TTS_UNIT_DURATION_SEC = 0.75
SHORTS_MAX_DURATION_SEC = 179.0
MAX_SAFE_NARRATION_SPEED = 1.25


def estimate_tts_unit_duration(text: str) -> float:
    """Estimate English TTS duration from both word and syllable workload."""
    cleaned = text.strip()
    words = re.findall(r"\b[A-Za-z][A-Za-z'-]*\b", cleaned)
    word_count = max(1, len(words))
    syllable_count = max(1, sum(_estimate_english_syllables(word) for word in words))
    if cleaned.endswith((".", "!", "?", '"', "”", "’")):
        pause = 0.34
    elif cleaned.endswith((",", ";", ":")):
        pause = 0.20
    else:
        pause = 0.14
    articulation = max(
        word_count / TTS_WORDS_PER_SECOND,
        syllable_count / TTS_SYLLABLES_PER_SECOND,
    )
    return round(max(MIN_TTS_UNIT_DURATION_SEC, articulation + pause), 3)


def _estimate_english_syllables(word: str) -> int:
    """Small dependency-free English syllable heuristic for planning estimates."""
    value = re.sub(r"[^a-z]", "", word.casefold())
    if not value:
        return 1
    groups = len(re.findall(r"[aeiouy]+", value))
    if value.endswith("e") and not value.endswith(("le", "ye")) and groups > 1:
        groups -= 1
    if value.endswith("ed") and len(value) > 3 and value[-3] not in "dt" and groups > 1:
        groups -= 1
    return max(1, groups)


def prepare_tts_srt(story_json: Path, output_dir: Path) -> dict[str, Any]:
    story = json.loads(story_json.read_text(encoding="utf-8"))
    narration = [dict(item) for item in story.get("narration", []) if isinstance(item, dict)]
    if not narration:
        raise ValueError("故事稿中没有英文解说")
    output_dir.mkdir(parents=True, exist_ok=True)
    srt_path = output_dir / "gpt_sovits_reference.srt"

    srt_blocks = []
    cursor = 0.0
    subtitle_index = 1
    for item in narration:
        text = str(item.get("text_en", "")).strip()
        sentences = split_gpt_sovits_units(text)
        natural_durations = [estimate_tts_unit_duration(sentence) for sentence in sentences]
        saved_duration = float(item.get("estimated_duration_sec", 0) or 0)
        natural_total = sum(natural_durations)
        scale = max(1.0, saved_duration / natural_total) if natural_total else 1.0
        sentence_durations = [value * scale for value in natural_durations]
        duration = sum(sentence_durations)
        sentence_cursor = cursor
        for sentence, sentence_duration in zip(sentences, sentence_durations):
            srt_blocks.append(
                f"{subtitle_index}\n"
                f"{_srt_time(sentence_cursor)} --> {_srt_time(sentence_cursor + sentence_duration)}\n"
                f"{sentence}"
            )
            sentence_cursor += sentence_duration
            subtitle_index += 1
        cursor += duration

    srt_path.write_text("\n\n".join(srt_blocks) + "\n", encoding="utf-8")
    return {
        "reference_srt_path": str(srt_path),
        "subtitle_count": len(srt_blocks),
        "estimated_duration_sec": round(cursor, 3),
    }


def import_narration_audio(
    source_audio: Path,
    destination: Path,
    config: dict[str, Any],
    app_root: Path,
) -> dict[str, Any]:
    if not source_audio.exists():
        raise FileNotFoundError(f"找不到配音文件：{source_audio}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shared = config.get("shared", {})
    ffmpeg = _resolve_tool(str(shared.get("ffmpeg_bin", "ffmpeg")), app_root, "ffmpeg")
    if source_audio.resolve() != destination.resolve():
        if ffmpeg:
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(source_audio),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "48000",
                    "-c:a",
                    "pcm_s16le",
                    str(destination),
                ],
                capture_output=True,
                check=True,
            )
        else:
            if source_audio.suffix.lower() != ".wav":
                raise RuntimeError("导入非 WAV 配音需要 FFmpeg")
            shutil.copy2(source_audio, destination)
    duration = probe_audio_duration(destination, config, app_root)
    if duration <= 0:
        raise ValueError("无法读取英文配音时长")
    return {
        "path": str(destination),
        "duration_sec": round(duration, 3),
        "file_size": destination.stat().st_size,
        "source_name": source_audio.name,
    }


def import_synced_srt(source_srt: Path, destination: Path) -> dict[str, Any]:
    if not source_srt.exists():
        raise FileNotFoundError(f"找不到同步字幕：{source_srt}")
    text = source_srt.read_text(encoding="utf-8-sig")
    segments = parse_srt_timings(text)
    if not segments:
        raise ValueError("同步 SRT 中没有有效字幕")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    return {
        "path": str(destination),
        "segment_count": len(segments),
        "duration_sec": round(max(item["end"] for item in segments), 3),
        "segments": segments,
    }


def recommended_shorts_speed(
    duration_sec: float,
    target_sec: float = 178.5,
    max_speed: float = MAX_SAFE_NARRATION_SPEED,
) -> float | None:
    """Return the smallest 0.01x speed that fits, or None when speed alone is unsafe."""
    if duration_sec <= target_sec:
        return 1.0
    required = math.ceil((duration_sec / target_sec) * 100) / 100
    return required if required <= max_speed else None


def process_narration_speed(
    source_audio: Path,
    destination_audio: Path,
    speed: float,
    config: dict[str, Any],
    app_root: Path,
    source_srt: Path | None = None,
    destination_srt: Path | None = None,
) -> dict[str, Any]:
    """Create working narration files from untouched imports at one shared speed."""
    speed = round(float(speed), 2)
    if speed < 1.0 or speed > MAX_SAFE_NARRATION_SPEED:
        raise ValueError(f"配音速度仅允许 1.00x～{MAX_SAFE_NARRATION_SPEED:.2f}x")
    if not source_audio.exists():
        raise FileNotFoundError(f"找不到原始英文配音：{source_audio}")

    destination_audio.parent.mkdir(parents=True, exist_ok=True)
    temporary_audio = destination_audio.with_name(f"{destination_audio.stem}.speeding.wav")
    temporary_srt = (
        destination_srt.with_name(f"{destination_srt.stem}.speeding.srt")
        if destination_srt
        else None
    )
    segments: list[dict[str, Any]] = []
    try:
        if speed == 1.0:
            shutil.copy2(source_audio, temporary_audio)
        else:
            shared = config.get("shared", {})
            ffmpeg = _resolve_tool(str(shared.get("ffmpeg_bin", "ffmpeg")), app_root, "ffmpeg")
            if not ffmpeg:
                raise RuntimeError("调整英文配音速度需要 FFmpeg")
            result = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    str(source_audio),
                    "-vn",
                    "-filter:a",
                    f"atempo={speed:.2f}",
                    "-ac",
                    "1",
                    "-ar",
                    "48000",
                    "-c:a",
                    "pcm_s16le",
                    str(temporary_audio),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0 or not temporary_audio.exists():
                detail = (result.stderr or result.stdout or "FFmpeg 未生成处理后的配音").strip()
                raise RuntimeError(detail[-1200:])
        if source_srt and source_srt.exists() and destination_srt and temporary_srt:
            segments = scale_srt_timeline(source_srt, temporary_srt, speed)

        duration = probe_audio_duration(temporary_audio, config, app_root)
        if duration <= 0:
            raise ValueError("无法读取处理后的英文配音时长")
        temporary_audio.replace(destination_audio)
        if temporary_srt and temporary_srt.exists() and destination_srt:
            temporary_srt.replace(destination_srt)
        return {
            "path": str(destination_audio),
            "duration_sec": round(duration, 3),
            "speed": speed,
            "segments": segments,
        }
    finally:
        temporary_audio.unlink(missing_ok=True)
        if temporary_srt:
            temporary_srt.unlink(missing_ok=True)


def scale_srt_timeline(source_srt: Path, destination_srt: Path, speed: float) -> list[dict[str, Any]]:
    """Scale a synchronized SRT with the same factor used for its narration audio."""
    speed = float(speed)
    if speed <= 0:
        raise ValueError("字幕时间轴速度必须大于 0")
    segments = parse_srt_timings(source_srt.read_text(encoding="utf-8-sig"))
    if not segments:
        raise ValueError("同步 SRT 中没有有效字幕")
    destination_srt.parent.mkdir(parents=True, exist_ok=True)
    adjusted: list[dict[str, Any]] = []
    blocks: list[str] = []
    for index, segment in enumerate(segments, 1):
        start = float(segment["start"]) / speed
        end = float(segment["end"]) / speed
        text = str(segment.get("text", "")).strip()
        adjusted.append({"id": index, "start": start, "end": end, "text": text})
        blocks.append(f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}")
    destination_srt.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return adjusted


def probe_audio_duration(audio: Path, config: dict[str, Any], app_root: Path) -> float:
    shared = config.get("shared", {})
    ffprobe = _resolve_tool(str(shared.get("ffprobe_bin", "ffprobe")), app_root, "ffprobe")
    if ffprobe:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(audio),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        return float(result.stdout.strip() or 0)
    import av

    with av.open(str(audio)) as container:
        return float(container.duration / av.time_base) if container.duration else 0.0


def parse_srt_timings(text: str) -> list[dict[str, Any]]:
    normalized = text.replace("\r\n", "\n").strip()
    blocks = re.split(r"\n{2,}", normalized)
    result = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), -1)
        if timing_index < 0:
            continue
        left, right = [part.strip() for part in lines[timing_index].split("-->", 1)]
        result.append(
            {
                "id": len(result) + 1,
                "start": _parse_srt_time(left),
                "end": _parse_srt_time(right),
                "text": " ".join(lines[timing_index + 1 :]),
            }
        )
    return result


def _srt_time(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _parse_srt_time(value: str) -> float:
    match = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", value)
    if not match:
        raise ValueError(f"无效的 SRT 时间：{value}")
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / (10 ** len(match.group(4)))


def split_gpt_sovits_units(text: str) -> list[str]:
    """Match the punctuation-based sentence splitting used by the TTS workflow."""
    cleaned = text.strip()
    if not cleaned:
        return []
    punctuation = set(",.!?;:")
    closing_marks = set("\"'”’)]}")
    units: list[str] = []
    start = 0
    index = 0
    while index < len(cleaned):
        char = cleaned[index]
        if char not in punctuation:
            index += 1
            continue
        previous = cleaned[index - 1] if index > 0 else ""
        following = cleaned[index + 1] if index + 1 < len(cleaned) else ""
        if char in ".,:" and previous.isdigit() and following.isdigit():
            index += 1
            continue

        end = index + 1
        while end < len(cleaned) and cleaned[end] in punctuation:
            end += 1
        while end < len(cleaned) and cleaned[end] in closing_marks:
            end += 1
        unit = cleaned[start:end].strip()
        if unit:
            units.append(unit)
        while end < len(cleaned) and cleaned[end].isspace():
            end += 1
        start = end
        index = end

    tail = cleaned[start:].strip()
    if tail:
        units.append(tail)
    units = units or [cleaned]
    # GPT-SoVITS splits at punctuation, but isolated connectors such as
    # "Next," or "All right," sound unnatural and inflate the displayed line
    # count. Merge very short fragments into the following unit (or the
    # preceding one when they occur at the end).
    merged: list[str] = []
    pending: list[str] = []
    for unit in units:
        word_count = len(re.findall(r"\b[\w'-]+\b", unit))
        if word_count < 3:
            pending.append(unit)
            continue
        if pending:
            unit = " ".join([*pending, unit])
            pending = []
        merged.append(unit)
    if pending:
        tail = " ".join(pending)
        if merged:
            merged[-1] = f"{merged[-1]} {tail}"
        else:
            merged.append(tail)
    return merged
