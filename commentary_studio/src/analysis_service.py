from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from .media_service import _resolve_tool


ProgressCallback = Callable[[float, str], None]


def extract_analysis_audio(
    video: Path,
    audio: Path,
    config: dict[str, Any],
    app_root: Path,
) -> None:
    shared = config.get("shared", {})
    ffmpeg = _resolve_tool(str(shared.get("ffmpeg_bin", "ffmpeg")), app_root, "ffmpeg")
    if not ffmpeg:
        raise FileNotFoundError("找不到 FFmpeg，无法提取分析音频")
    audio.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(audio),
    ]
    result = subprocess.run(command, capture_output=True)
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace")[-2000:]
        raise RuntimeError(f"FFmpeg 音频提取失败：{error}")


def transcribe_analysis_audio(
    audio: Path,
    transcript_json: Path,
    transcript_srt: Path,
    duration: float,
    config: dict[str, Any],
    app_root: Path,
    progress: ProgressCallback,
) -> dict[str, Any]:
    from faster_whisper import WhisperModel

    analysis = config.get("analysis", {})
    model_name = str(analysis.get("asr_model", "large-v3"))
    model_dir_value = str(analysis.get("model_dir", "../models/faster-whisper"))
    model_dir = Path(model_dir_value)
    if not model_dir.is_absolute():
        model_dir = (app_root / model_dir).resolve()
    model_source = _find_model_source(model_dir, model_name)
    local_only = bool(analysis.get("local_files_only", True))
    device = _select_device(str(analysis.get("device", "auto")))
    compute_type = str(
        analysis.get("cpu_compute_type", "int8")
        if device == "cpu"
        else analysis.get("compute_type", "float16")
    )

    progress(0.12, f"正在加载 {model_name} 模型（{device}/{compute_type}）…")
    model = WhisperModel(
        model_source,
        device=device,
        compute_type=compute_type,
        download_root=str(model_dir),
        local_files_only=local_only,
    )
    progress(0.16, "模型已加载，正在识别原片语音…")
    segments_iterator, info = model.transcribe(
        str(audio),
        language=str(analysis.get("language", "zh")),
        vad_filter=bool(analysis.get("vad_filter", True)),
        beam_size=int(analysis.get("beam_size", 5)),
    )

    segments: list[dict[str, Any]] = []
    for segment in segments_iterator:
        text = str(segment.text or "").strip()
        if not text:
            continue
        item = {
            "index": len(segments) + 1,
            "start": round(float(segment.start), 3),
            "end": round(float(segment.end), 3),
            "text": text,
        }
        segments.append(item)
        ratio = min(1.0, item["end"] / duration) if duration > 0 else 0.0
        progress(0.16 + ratio * 0.82, f"已识别 {len(segments)} 段，到 {format_time(item['end'])}")

    payload = {
        "schema_version": 1,
        "language": str(getattr(info, "language", analysis.get("language", "zh"))),
        "language_probability": round(float(getattr(info, "language_probability", 0.0)), 4),
        "duration_sec": duration,
        "model": model_name,
        "device": device,
        "compute_type": compute_type,
        "segments": segments,
    }
    transcript_json.parent.mkdir(parents=True, exist_ok=True)
    transcript_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    transcript_srt.write_text(_segments_to_srt(segments), encoding="utf-8")
    progress(1.0, f"转录完成，共 {len(segments)} 段字幕")
    return payload


def detect_scenes_and_keyframes(
    video: Path,
    scenes_json: Path,
    keyframes_dir: Path,
    duration: float,
    config: dict[str, Any],
    app_root: Path,
    progress: ProgressCallback,
) -> dict[str, Any]:
    shared = config.get("shared", {})
    ffmpeg = _resolve_tool(str(shared.get("ffmpeg_bin", "ffmpeg")), app_root, "ffmpeg")
    if not ffmpeg:
        raise FileNotFoundError("找不到 FFmpeg，无法进行场景切分")

    analysis = config.get("analysis", {})
    threshold = float(analysis.get("scene_threshold", 0.32))
    min_scene_sec = max(0.0, float(analysis.get("min_scene_sec", 1.0)))
    keyframe_width = max(240, int(analysis.get("keyframe_width", 640)))
    keyframes_dir.mkdir(parents=True, exist_ok=True)
    output_pattern = keyframes_dir / "scene_%04d.jpg"
    video_filter = (
        f"select=eq(n\\,0)+gt(scene\\,{threshold}),"
        f"scale={keyframe_width}:-2,showinfo"
    )
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(video),
        "-vf",
        video_filter,
        "-fps_mode",
        "vfr",
        "-q:v",
        "4",
        str(output_pattern),
    ]

    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    timestamps: list[float] = []
    keyframe_names: list[str] = []
    detected_frame_number = 0
    assert process.stderr is not None
    pattern = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")
    for line in process.stderr:
        if "showinfo" not in line:
            continue
        match = pattern.search(line)
        if not match:
            continue
        detected_frame_number += 1
        timestamp = float(match.group(1))
        if timestamps and timestamp - timestamps[-1] < min_scene_sec:
            continue
        timestamps.append(timestamp)
        keyframe_names.append(f"scene_{detected_frame_number:04d}.jpg")
        ratio = min(1.0, timestamp / duration) if duration > 0 else 0.0
        progress(ratio, f"已检测 {len(timestamps)} 个场景，到 {format_time(timestamp)}")

    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"FFmpeg 场景检测失败，退出码 {return_code}")
    if not timestamps:
        timestamps = [0.0]
        keyframe_names = ["scene_0001.jpg"]
    elif timestamps[0] > 0.05:
        timestamps.insert(0, 0.0)
        keyframe_names.insert(0, "scene_0001.jpg")

    image_files = sorted(keyframes_dir.glob("scene_*.jpg"))
    available_names = {path.name for path in image_files}
    accepted = [
        (timestamp, name)
        for timestamp, name in zip(timestamps, keyframe_names)
        if name in available_names
    ]
    timestamps = [item[0] for item in accepted]
    keyframe_names = [item[1] for item in accepted]
    scene_count = len(timestamps)
    timestamps = timestamps[:scene_count]
    accepted_names = set(keyframe_names)
    for image_file in image_files:
        if image_file.name not in accepted_names:
            image_file.unlink(missing_ok=True)
    scenes: list[dict[str, Any]] = []
    for index, start in enumerate(timestamps):
        end = timestamps[index + 1] if index + 1 < len(timestamps) else duration
        if end <= start:
            continue
        scenes.append(
            {
                "id": index + 1,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(end - start, 3),
                "keyframe": f"keyframes/{keyframe_names[index]}",
            }
        )

    payload = {
        "schema_version": 1,
        "threshold": threshold,
        "min_scene_sec": min_scene_sec,
        "duration_sec": duration,
        "scene_count": len(scenes),
        "scenes": scenes,
    }
    scenes_json.parent.mkdir(parents=True, exist_ok=True)
    scenes_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    progress(1.0, f"场景切分完成，共 {len(scenes)} 个场景")
    return payload


def build_timeline_events(
    transcript_json: Path,
    scenes_json: Path,
    events_json: Path,
) -> dict[str, Any]:
    transcript = json.loads(transcript_json.read_text(encoding="utf-8"))
    scene_data = json.loads(scenes_json.read_text(encoding="utf-8"))
    segments = list(transcript.get("segments", []))
    events: list[dict[str, Any]] = []

    for scene in scene_data.get("scenes", []):
        start = float(scene.get("start", 0))
        end = float(scene.get("end", start))
        overlapping = [
            segment
            for segment in segments
            if float(segment.get("end", 0)) > start and float(segment.get("start", 0)) < end
        ]
        text = " ".join(str(segment.get("text", "")).strip() for segment in overlapping).strip()
        speech_duration = sum(
            max(0.0, min(end, float(segment.get("end", 0))) - max(start, float(segment.get("start", 0))))
            for segment in overlapping
        )
        events.append(
            {
                "id": int(scene.get("id", len(events) + 1)),
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(max(0.0, end - start), 3),
                "keyframe": str(scene.get("keyframe", "")),
                "transcript_indices": [int(segment.get("index", 0)) for segment in overlapping],
                "transcript": text,
                "has_speech": bool(text),
                "speech_duration": round(speech_duration, 3),
                "visual_description": "",
                "summary": "",
                "importance": None,
            }
        )

    payload = {
        "schema_version": 1,
        "duration_sec": float(scene_data.get("duration_sec", transcript.get("duration_sec", 0)) or 0),
        "event_count": len(events),
        "speech_event_count": sum(1 for event in events if event["has_speech"]),
        "events": events,
    }
    events_json.parent.mkdir(parents=True, exist_ok=True)
    events_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _find_model_source(model_dir: Path, model_name: str) -> str:
    manual_candidates = (
        model_dir,
        model_dir / model_name,
        model_dir / f"faster-whisper-{model_name}",
    )
    for candidate in manual_candidates:
        if (candidate / "model.bin").exists() and (candidate / "config.json").exists():
            return str(candidate)
    snapshots = model_dir / f"models--Systran--faster-whisper-{model_name}" / "snapshots"
    if snapshots.exists():
        candidates = sorted(snapshots.iterdir(), key=lambda path: path.stat().st_mtime, reverse=True)
        for candidate in candidates:
            if (candidate / "model.bin").exists() and (candidate / "config.json").exists():
                return str(candidate)
    raise FileNotFoundError(f"找不到本地 Faster-Whisper 模型：{model_name}（{model_dir}）")


def _select_device(configured: str) -> str:
    if configured.lower() != "auto":
        return configured.lower()
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            result = subprocess.run(
                [nvidia_smi, "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return "cuda"
        except (OSError, subprocess.SubprocessError):
            pass
    return "cpu"


def _segments_to_srt(segments: list[dict[str, Any]]) -> str:
    blocks = []
    for item in segments:
        blocks.append(
            f"{item['index']}\n{_srt_time(float(item['start']))} --> {_srt_time(float(item['end']))}\n{item['text']}"
        )
    return "\n\n".join(blocks).strip() + "\n"


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def format_time(seconds: float) -> str:
    total = max(0, round(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:d}:{secs:02d}"
