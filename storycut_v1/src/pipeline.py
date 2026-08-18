from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from subtitle_effects import build_effect_ass
from subtitle_cleanup import build_cleanup_graph
from config_manager import load_config as load_project_config


ROOT = Path(__file__).resolve().parents[1]
SHARED_ROOT = ROOT.parent


@dataclass
class SubtitleItem:
    index: int
    start: str
    end: str
    text: str

    def to_srt(self, index: int | None = None) -> str:
        idx = self.index if index is None else index
        return f"{idx}\n{self.start} --> {self.end}\n{self.text.strip()}\n"


def log(msg: str) -> None:
    print(f"[video-auto] {msg}", flush=True)


def load_yaml_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg


def load_dotenv_file(path: Path) -> dict[str, str]:
    """Minimal .env loader for KEY=VALUE lines. Does not overwrite existing environment variables."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
            os.environ.setdefault(key, value)
    return values


def load_runtime_env() -> dict[str, str]:
    # Both desktop applications share the repository-level API configuration.
    return load_dotenv_file(SHARED_ROOT / ".env")


def resolve_path(value: str | Path, base: Path = ROOT) -> Path:
    p = Path(value)
    if not p.is_absolute():
        p = (base / p).resolve()
    return p


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def run_cmd(cmd: list[str], cwd: Path | None = None) -> None:
    log("RUN: " + " ".join(map(str, cmd)))
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {proc.returncode}: {cmd}")


def run_cmd_logged(cmd: list[str], log_path: Path, cwd: Path | None = None) -> None:
    """Run a command with live output and retain the exact final diagnostics."""
    log("RUN: " + " ".join(map(str, cmd)))
    lines: list[str] = []
    proc = subprocess.Popen(
        cmd, cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        clean = line.rstrip()
        print(clean, flush=True)
        lines.append(clean)
    code = proc.wait()
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if code != 0:
        tail = next((line for line in reversed(lines) if line.strip()), f"exit code {code}")
        raise RuntimeError(f"FFmpeg failed ({code}): {tail}. 完整日志：{log_path}")


def check_bin(bin_name: str) -> str:
    if Path(bin_name).exists():
        return bin_name
    found = shutil.which(bin_name)
    if not found:
        raise FileNotFoundError(
            f"Cannot find executable: {bin_name}. 请安装并配置 PATH，或在 config.user.yaml 里填写绝对路径。"
        )
    return found


def srt_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    millis = int(round(seconds * 1000))
    h = millis // 3_600_000
    millis %= 3_600_000
    m = millis // 60_000
    millis %= 60_000
    s = millis // 1000
    ms = millis % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt_timestamp_to_seconds(value: str) -> float:
    m = re.match(r"^(\d+):(\d+):(\d+),(\d+)$", value.strip())
    if not m:
        raise ValueError(f"Invalid SRT timestamp: {value}")
    h, minute, sec, ms = map(int, m.groups())
    return h * 3600 + minute * 60 + sec + ms / 1000


def srt_items_duration(items: list[SubtitleItem]) -> float:
    if not items:
        return 0.0
    return max(srt_timestamp_to_seconds(item.end) for item in items)


def ffprobe_duration(cfg: dict[str, Any], media_path: Path) -> float:
    ffprobe = check_bin(cfg["ffmpeg"].get("ffprobe_bin", "ffprobe"))
    proc = subprocess.run(
        [
            ffprobe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {media_path}: {proc.stderr.strip()}")
    return float(proc.stdout.strip())


def ffprobe_video_size(cfg: dict[str, Any], media_path: Path) -> tuple[int, int] | None:
    ffprobe = check_bin(cfg["ffmpeg"].get("ffprobe_bin", "ffprobe"))
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(media_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    match = re.search(r"(\d+)x(\d+)", proc.stdout)
    return (int(match.group(1)), int(match.group(2))) if proc.returncode == 0 and match else None


def atempo_filter(speed: float) -> str:
    # FFmpeg atempo accepts 0.5..100 in recent versions, but chaining is safer and keeps quality stable.
    if speed <= 0:
        raise ValueError(f"Invalid audio speed: {speed}")
    factors: list[float] = []
    remaining = speed
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return ",".join(f"atempo={factor:.6f}" for factor in factors)


def scale_srt_file(src: Path, dst: Path, scale: float) -> None:
    items = parse_srt(src.read_text(encoding="utf-8"))
    scaled: list[SubtitleItem] = []
    for item in items:
        start = srt_timestamp(srt_timestamp_to_seconds(item.start) * scale)
        end = srt_timestamp(srt_timestamp_to_seconds(item.end) * scale)
        scaled.append(SubtitleItem(item.index, start, end, item.text))
    write_srt(scaled, dst, renumber=False)


def smart_segment_fit(
    cfg: dict[str, Any],
    input_video: Path,
    audio_path: Path,
    tts_srt_path: Path,
    zh_srt_path: Path,
    work_dir: Path,
) -> tuple[Path, Path]:
    """逐段自适应音频调速，解决英文配音比中文长导致的音画不同步问题。

    核心思路：
    - 以原始中文字幕的时间轴为视频时间基准
    - 对比每段英文配音的实际时长与视频分配的时间窗口
    - 仅对溢出的段落做音频加速，保持音画逐段对齐
    - 段落间自动填充静音或紧凑衔接

    返回: (调整后的音频路径, 调整后的字幕路径)
    """
    comp = cfg.get("compose", {})
    ssf = comp.get("smart_segment_fit", {})
    if not ssf.get("enabled", False):
        return audio_path, tts_srt_path

    # --- 1. 加载字幕 ---
    zh_items = parse_srt(zh_srt_path.read_text(encoding="utf-8"))
    tts_items = parse_srt(tts_srt_path.read_text(encoding="utf-8"))

    if not zh_items:
        log("smart_segment_fit: 中文字幕为空，跳过")
        return audio_path, tts_srt_path
    if not tts_items:
        log("smart_segment_fit: TTS字幕为空，跳过")
        return audio_path, tts_srt_path

    # --- 2. 参数 ---
    min_speed = float(ssf.get("min_speed", 0.80))
    max_speed = float(ssf.get("max_speed", 1.50))
    gap_fill_mode = ssf.get("gap_fill", "silence")  # "silence" 或 "tight"
    silence_db = float(ssf.get("silence_db", -40))
    segment_padding = float(ssf.get("segment_padding_sec", 0.05))

    # --- 3. 获取时长 ---
    ffmpeg_bin = check_bin(cfg["ffmpeg"].get("ffmpeg_bin", "ffmpeg"))
    ffprobe_bin = check_bin(cfg["ffmpeg"].get("ffprobe_bin", "ffprobe"))
    audio_duration = ffprobe_duration(cfg, audio_path)
    video_duration = ffprobe_duration(cfg, input_video)

    log(f"smart_segment_fit: 视频时长={video_duration:.2f}s, 音频时长={audio_duration:.2f}s")

    # --- 4. 构建视频时间窗口（基于中文字幕） ---
    # 每个中文字幕段落对应一个视频时间窗口
    video_windows: list[tuple[float, float]] = []
    for item in zh_items:
        start = srt_timestamp_to_seconds(item.start)
        end = srt_timestamp_to_seconds(item.end)
        video_windows.append((start, end))

    # --- 5. 构建音频段落（基于TTS字幕） ---
    # 每个TTS字幕段落对应一段英文配音
    audio_segments: list[tuple[float, float]] = []
    for item in tts_items:
        start = srt_timestamp_to_seconds(item.start)
        end = srt_timestamp_to_seconds(item.end)
        audio_segments.append((start, end))

    # --- 6. 逐段匹配与调速决策 ---
    n = min(len(video_windows), len(audio_segments))
    if len(video_windows) != len(audio_segments):
        log(f"smart_segment_fit: 字幕段数不匹配 zh={len(video_windows)} tts={len(audio_segments)}，按较少的{n}段处理")

    speed_plan: list[dict[str, Any]] = []
    total_overflow = 0.0
    segments_needing_speed = 0

    for i in range(n):
        v_start, v_end = video_windows[i]
        a_start, a_end = audio_segments[i]
        v_dur = v_end - v_start
        a_dur = a_end - a_start

        if a_dur <= 0 or v_dur <= 0:
            speed_plan.append({
                "index": i,
                "v_start": v_start, "v_end": v_end, "v_dur": v_dur,
                "a_start": a_start, "a_end": a_end, "a_dur": a_dur,
                "speed": 1.0, "action": "keep",
            })
            continue

        if a_dur <= v_dur:
            # 音频在窗口内，不需要调速
            speed_plan.append({
                "index": i,
                "v_start": v_start, "v_end": v_end, "v_dur": v_dur,
                "a_start": a_start, "a_end": a_end, "a_dur": a_dur,
                "speed": 1.0, "action": "keep",
            })
        else:
            # 音频溢出，需要计算加速倍率
            needed_speed = a_dur / v_dur
            overflow = a_dur - v_dur
            total_overflow += overflow
            segments_needing_speed += 1

            if needed_speed > max_speed:
                # 超过最大加速限制，使用最大速度并标记
                actual_speed = max_speed
                log(f"  段{i+1}: 需要加速{needed_speed:.2f}x超过上限{max_speed:.2f}x，使用{max_speed:.2f}x（仍有残余溢出）")
            else:
                actual_speed = needed_speed
                log(f"  段{i+1}: 加速{actual_speed:.2f}x ({a_dur:.2f}s→{a_dur/actual_speed:.2f}s，窗口{v_dur:.2f}s)")

            speed_plan.append({
                "index": i,
                "v_start": v_start, "v_end": v_end, "v_dur": v_dur,
                "a_start": a_start, "a_end": a_end, "a_dur": a_dur,
                "speed": actual_speed, "action": "speed_up",
            })

    log(f"smart_segment_fit: {segments_needing_speed}/{n}段需要加速，总溢出{total_overflow:.2f}s")

    if segments_needing_speed == 0:
        log("smart_segment_fit: 无需调速，所有段落均在窗口内")
        return audio_path, tts_srt_path

    # --- 7. 使用FFmpeg逐段处理音频 ---
    # 策略：逐段提取→调速→保存临时文件，间隙生成静音，再用concat拼接
    # 这种方式比单条filter_complex更稳健，避免Windows命令行长度限制

    output_audio = work_dir / "en_voice_segment_fit.wav"
    output_srt = work_dir / "en_synced_segment_fit.srt"
    seg_dir = work_dir / "_seg_fit_tmp"
    ensure_dir(seg_dir)

    # 7a. 逐段处理音频 + 间隙静音填充
    seg_files: list[Path] = []
    prev_seg_end: float = 0.0  # 上一段在视频时间轴上的结束位置

    for i, plan in enumerate(speed_plan):
        v_start = plan["v_start"]
        v_dur = plan["v_dur"]
        a_start = plan["a_start"]
        a_dur = plan["a_dur"]
        speed = plan["speed"]
        action = plan["action"]

        # --- 间隙静音：当前段开始前与上一段结束之间的空白 ---
        gap = v_start - prev_seg_end
        if gap > 0.02 and i > 0:  # 忽略微小间隙，第一段前的静音也处理
            silence_out = seg_dir / f"silence_{i:04d}.wav"
            run_cmd([
                ffmpeg_bin, "-y",
                "-f", "lavfi",
                "-i", f"anullsrc=r=44100:cl=stereo",
                "-t", f"{gap:.4f}",
                "-c:a", "pcm_s16le",
                str(silence_out),
            ])
            seg_files.append(silence_out)

        # 第一段之前的静音（如果视频开头有空白）
        if i == 0 and v_start > 0.02:
            silence_out = seg_dir / "silence_0000.wav"
            run_cmd([
                ffmpeg_bin, "-y",
                "-f", "lavfi",
                "-i", f"anullsrc=r=44100:cl=stereo",
                "-t", f"{v_start:.4f}",
                "-c:a", "pcm_s16le",
                str(silence_out),
            ])
            seg_files.append(silence_out)

        # --- 处理当前音频段 ---
        seg_out = seg_dir / f"seg_{i:04d}.wav"
        trim_start = max(0, a_start - segment_padding)
        trim_dur = a_dur + 2 * segment_padding

        if action == "speed_up":
            # 裁剪 + 加速 + 填充/截断到窗口时长
            filt = (
                f"atrim=start={trim_start:.4f}:duration={trim_dur:.4f},"
                f"asetpts=PTS-STARTPTS,"
                f"{atempo_filter(speed)},"
                f"apad=whole_dur={v_dur:.4f},"
                f"atrim=end={v_dur:.4f},"
                f"asetpts=PTS-STARTPTS"
            )
        else:
            # 裁剪 + 填充到窗口时长
            filt = (
                f"atrim=start={trim_start:.4f}:duration={trim_dur:.4f},"
                f"asetpts=PTS-STARTPTS,"
                f"apad=whole_dur={v_dur:.4f},"
                f"atrim=end={v_dur:.4f},"
                f"asetpts=PTS-STARTPTS"
            )

        run_cmd([
            ffmpeg_bin, "-y",
            "-i", str(audio_path),
            "-af", filt,
            "-c:a", "pcm_s16le",
            str(seg_out),
        ])
        seg_files.append(seg_out)
        prev_seg_end = v_start + v_dur

    # 7b. 尾部静音补充（如果最后一段之后还有视频时间）
    if prev_seg_end < video_duration - 0.02:
        tail_silence = video_duration - prev_seg_end
        silence_out = seg_dir / "silence_tail.wav"
        run_cmd([
            ffmpeg_bin, "-y",
            "-f", "lavfi",
            "-i", f"anullsrc=r=44100:cl=stereo",
            "-t", f"{tail_silence:.4f}",
            "-c:a", "pcm_s16le",
            str(silence_out),
        ])
        seg_files.append(silence_out)

    # 7c. 生成concat列表文件
    concat_list = seg_dir / "concat.txt"
    lines: list[str] = []
    for seg_file in seg_files:
        # FFmpeg concat demuxer 需要正斜杠路径
        p = seg_file.resolve().as_posix()
        lines.append(f"file '{p}'")
    concat_list.write_text("\n".join(lines), encoding="utf-8")

    # 7d. 拼接所有段
    run_cmd([
        ffmpeg_bin, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c:a", "pcm_s16le",
        str(output_audio),
    ])

    # 清理临时文件
    try:
        shutil.rmtree(seg_dir, ignore_errors=True)
    except Exception:
        pass

    log(f"smart_segment_fit: 逐段音频调速完成")

    # --- 8. 生成对齐后的字幕 ---
    # 字幕时间轴按视频窗口重新对齐
    synced_items: list[SubtitleItem] = []
    for i, plan in enumerate(speed_plan):
        if i >= len(tts_items):
            break
        v_start = plan["v_start"]
        v_dur = plan["v_dur"]
        a_dur = plan["a_dur"]
        speed = plan["speed"]

        # 字幕开始 = 视频窗口开始
        # 字幕结束 = min(视频窗口结束, 调速后的音频时长)
        if speed > 1.0:
            fitted_a_dur = a_dur / speed
        else:
            fitted_a_dur = a_dur

        sub_end = min(v_start + fitted_a_dur, v_start + v_dur)
        synced_items.append(SubtitleItem(
            index=i + 1,
            start=srt_timestamp(v_start),
            end=srt_timestamp(sub_end),
            text=tts_items[i].text,
        ))

    write_srt(synced_items, output_srt)
    log(f"smart_segment_fit: 输出音频={output_audio}, 字幕={output_srt}")

    return output_audio, output_srt


def maybe_fit_audio_to_video_duration(
    cfg: dict[str, Any],
    input_video: Path,
    audio_path: Path,
    srt_path: Path,
    work_dir: Path,
) -> tuple[Path, Path]:
    comp = cfg.get("compose", {})
    fit = comp.get("fit_audio_to_video", {})
    if not fit.get("enabled", False):
        return audio_path, srt_path

    video_duration = ffprobe_duration(cfg, input_video)
    audio_duration = ffprobe_duration(cfg, audio_path)
    if video_duration <= 0 or audio_duration <= 0:
        return audio_path, srt_path

    speed = audio_duration / video_duration
    tolerance = float(fit.get("tolerance", 0.03))
    log(
        f"Duration check: video={video_duration:.2f}s audio={audio_duration:.2f}s "
        f"speed_needed={speed:.3f}x"
    )
    if abs(speed - 1.0) <= tolerance:
        log("Audio duration is close enough to video; no speed adjustment needed.")
        return audio_path, srt_path

    min_speed = float(fit.get("min_speed", 0.85))
    max_speed = float(fit.get("max_speed", 1.20))
    if speed < min_speed or speed > max_speed:
        log(
            f"WARNING: required audio speed {speed:.3f}x is outside configured safe range "
            f"[{min_speed:.2f}, {max_speed:.2f}]. Will still apply because fit_audio_to_video.enabled=true."
        )

    ffmpeg = check_bin(cfg["ffmpeg"].get("ffmpeg_bin", "ffmpeg"))
    fitted_audio = work_dir / "en_voice_fit.wav"
    run_cmd([
        ffmpeg, "-y", "-i", str(audio_path),
        "-filter:a", atempo_filter(speed),
        str(fitted_audio),
    ])

    fitted_srt = srt_path
    try:
        items = parse_srt(srt_path.read_text(encoding="utf-8"))
        srt_duration = srt_items_duration(items)
        # Scale only if the selected SRT appears to be on the TTS/audio timeline.
        # If it is the original en_raw.srt, it is usually already on the video timeline and should be kept.
        if srt_duration > 0 and abs(srt_duration - audio_duration) / audio_duration <= float(fit.get("srt_audio_timeline_threshold", 0.15)):
            fitted_srt = work_dir / "en_synced_fit.srt"
            scale = video_duration / audio_duration
            scale_srt_file(srt_path, fitted_srt, scale)
            log(f"Scaled subtitle timeline by {scale:.6f}: {fitted_srt}")
        else:
            log(
                f"Subtitle timeline not scaled: srt_duration={srt_duration:.2f}s, "
                "it does not look like it is on the TTS audio timeline."
            )
    except Exception as exc:
        log(f"WARNING: failed to scale subtitle timeline, using original SRT: {exc}")
        fitted_srt = srt_path

    log(f"Using fitted audio: {fitted_audio}")
    return fitted_audio, fitted_srt


def parse_srt(text: str) -> list[SubtitleItem]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
    blocks = re.split(r"\n\s*\n", text.strip()) if text.strip() else []
    items: list[SubtitleItem] = []
    for block in blocks:
        lines = block.split("\n")
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            continue
        m = re.match(r"(.+?)\s*-->\s*(.+)", lines[1].strip())
        if not m:
            continue
        items.append(SubtitleItem(idx, m.group(1).strip(), m.group(2).strip(), "\n".join(lines[2:]).strip()))
    return items


def write_srt(items: Iterable[SubtitleItem], path: Path, renumber: bool = True) -> None:
    parts = []
    for n, item in enumerate(items, start=1):
        parts.append(item.to_srt(n if renumber else None))
    path.write_text("\n".join(parts).strip() + "\n", encoding="utf-8")


def extract_audio(cfg: dict[str, Any], input_video: Path, audio_out: Path) -> None:
    ffmpeg = check_bin(cfg["ffmpeg"]["ffmpeg_bin"])
    ensure_dir(audio_out.parent)
    run_cmd([
        ffmpeg, "-y", "-i", str(input_video),
        "-vn", "-ac", "1", "-ar", "16000", str(audio_out)
    ])


def transcribe(cfg: dict[str, Any], audio_path: Path, srt_out: Path) -> None:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError("缺少 faster-whisper。请先运行：pip install -r requirements.txt") from e

    asr = cfg["asr"]
    model_name = asr.get("model", "large-v3")
    model_dir = resolve_path(asr.get("model_dir", "./models/faster-whisper"))
    ensure_dir(model_dir)
    local_files_only = bool(asr.get("local_files_only", False))

    def find_model_source(name: str) -> str:
        # Manual model folders are supported. If the directory contains model.bin
        # directly, only use it when it corresponds to the primary configured model.
        manual_candidates = [model_dir / name, model_dir / f"faster-whisper-{name}"]
        if name == model_name:
            manual_candidates.insert(0, model_dir)
        for candidate in manual_candidates:
            if (candidate / "model.bin").exists() and (candidate / "config.json").exists():
                return str(candidate)
        # Hugging Face's normal Windows cache layout stores the usable model in a
        # revision folder under snapshots. Resolve it directly so local-only mode
        # never needs a Hub request just to locate an already downloaded model.
        snapshots = model_dir / f"models--Systran--faster-whisper-{name}" / "snapshots"
        if snapshots.exists():
            for candidate in sorted(snapshots.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if (candidate / "model.bin").exists() and (candidate / "config.json").exists():
                    return str(candidate)
        return name

    model_source = find_model_source(model_name)

    log(f"Loading faster-whisper model: {model_source}")
    log(f"Model download/cache dir: {model_dir}")
    if local_files_only:
        log("ASR local_files_only=true; will not download model files.")
    def recognize(device: str, compute_type: str, source: str = model_source) -> tuple[list[SubtitleItem], Any]:
        log(f"ASR runtime: device={device}, compute_type={compute_type}, model={source}")
        log("正在加载语音识别模型，请稍候……")
        loading_stop = threading.Event()

        def loading_heartbeat() -> None:
            started = time.monotonic()
            while not loading_stop.wait(30):
                elapsed = int(time.monotonic() - started)
                hint = "；首次使用可能正在下载模型" if not local_files_only and not Path(source).exists() else ""
                log(f"模型仍在加载：已等待 {elapsed // 60}分{elapsed % 60}秒{hint}……")

        threading.Thread(target=loading_heartbeat, daemon=True).start()
        try:
            model = WhisperModel(
                source,
                device=device,
                compute_type=compute_type,
                download_root=str(model_dir),
                local_files_only=local_files_only,
            )
        finally:
            loading_stop.set()
        segments, info = model.transcribe(
            str(audio_path),
            language=asr.get("language", "zh"),
            vad_filter=bool(asr.get("vad_filter", True)),
            beam_size=int(asr.get("beam_size", 5)),
        )
        log("模型已加载，开始分析音频。")
        heartbeat_stop = threading.Event()

        def heartbeat() -> None:
            started = time.monotonic()
            while not heartbeat_stop.wait(30):
                elapsed = int(time.monotonic() - started)
                log(f"语音识别仍在运行：已等待 {elapsed // 60}分{elapsed % 60}秒……")

        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        # faster-whisper performs most inference lazily while iterating segments,
        # so iteration must remain inside the CUDA fallback try/except.
        recognized: list[SubtitleItem] = []
        try:
            for seg in segments:
                text = (seg.text or "").strip()
                if not text:
                    continue
                recognized.append(SubtitleItem(len(recognized) + 1, srt_timestamp(seg.start), srt_timestamp(seg.end), text))
                if len(recognized) == 1 or len(recognized) % 20 == 0:
                    log(f"已识别 {len(recognized)} 个字幕片段，当前到 {seg.end:.1f} 秒。")
        finally:
            heartbeat_stop.set()
        return recognized, info

    device = str(asr.get("device", "auto"))
    if device.lower() == "auto":
        nvidia_smi = shutil.which("nvidia-smi")
        has_nvidia = False
        if nvidia_smi:
            try:
                check = subprocess.run(
                    [nvidia_smi, "--query-gpu=name", "--format=csv,noheader"],
                    capture_output=True, text=True, timeout=5,
                )
                has_nvidia = check.returncode == 0 and bool(check.stdout.strip())
            except (OSError, subprocess.SubprocessError):
                has_nvidia = False
        device = "cuda" if has_nvidia else "cpu"
        log(f"ASR 自动设备检测：{'发现 NVIDIA 显卡，使用 CUDA' if has_nvidia else '未发现可用 NVIDIA 显卡，使用 CPU'}。")
    compute_type = (
        str(asr.get("cpu_compute_type", "int8"))
        if device.lower() == "cpu" else str(asr.get("compute_type", "float16"))
    )
    initial_source = find_model_source(str(asr.get("cpu_model", model_name))) if device.lower() == "cpu" else model_source
    try:
        items, info = recognize(device, compute_type, initial_source)
    except Exception as exc:
        error_text = str(exc).lower()
        cuda_failure = any(word in error_text for word in ("cuda", "cudnn", "cublas", "driver version"))
        if device.lower().startswith("cuda") and bool(asr.get("fallback_to_cpu", True)) and cuda_failure:
            log(f"WARNING: GPU/CUDA 识别不可用：{exc}")
            log("正在自动切换到 CPU int8 继续识别；结果质量不变，但速度会明显变慢。")
            cpu_model = str(asr.get("cpu_model", "small"))
            if cpu_model == model_name:
                log(f"CPU 兜底继续使用已下载的 {cpu_model} 模型；无需下载，但识别会较慢。")
            else:
                log(f"CPU 兜底使用较轻量的 {cpu_model} 模型。")
            items, info = recognize("cpu", str(asr.get("cpu_compute_type", "int8")), find_model_source(cpu_model))
        else:
            raise
    log(f"Detected language={info.language}, probability={info.language_probability:.3f}")
    write_srt(items, srt_out)
    log(f"Wrote synchronized SRT: {srt_out}")


def srt_batch_to_text(items: list[SubtitleItem]) -> str:
    return "\n\n".join(item.to_srt() for item in items)


def translate_none(zh_srt: Path, en_srt: Path) -> None:
    shutil.copyfile(zh_srt, en_srt)
    log(f"translate.provider=none，已复制：{zh_srt} -> {en_srt}")


def translate_openai(cfg: dict[str, Any], zh_srt: Path, en_srt: Path) -> None:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise RuntimeError("?? openai ???????pip install -r requirements.txt") from e

    runtime_env = load_runtime_env()

    api_key = runtime_env.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Create project .env with OPENAI_API_KEY=your_key; "
            "or set the environment variable; or set translate.provider to none in config.user.yaml."
        )

    trans_cfg = cfg["translate"]
    base_url = (
        trans_cfg.get("base_url")
        or runtime_env.get("OPENAI_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or ""
    ).strip()

    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    client = OpenAI(**kwargs)

    items = parse_srt(zh_srt.read_text(encoding="utf-8"))
    batch_size = int(trans_cfg.get("batch_size", 15))
    translated_items: list[SubtitleItem] = []

    system = (
        "You are a professional subtitle translator. Return only valid SRT. "
        "Keep every subtitle item. Never merge, split, remove, or renumber items. "
        "Keep every timestamp exactly unchanged. Translate only subtitle text. "
        "Use concise, natural spoken English suitable for dubbing. "
        "Return no markdown and no explanations."
    )
    style = trans_cfg.get("style_prompt", "")

    def clean_srt_output(content: str) -> str:
        content = content.strip()
        content = re.sub(r"^```(?:srt)?\s*", "", content, flags=re.I)
        content = re.sub(r"\s*```$", "", content)
        return content.strip()

    def call_model(batch: list[SubtitleItem], batch_start: int, attempt: int) -> tuple[str, list[SubtitleItem]]:
        indexes = ", ".join(str(item.index) for item in batch)
        stricter = ""
        if attempt > 1:
            stricter = (
                "\n\nPrevious output had the wrong number of SRT items. "
                "You MUST return exactly the same number of SRT blocks as input. "
                "Do not omit short lines. Do not combine adjacent lines."
            )
        user = (
            f"{style}\n\n"
            f"Translate this SRT. Return EXACTLY {len(batch)} SRT blocks.\n"
            f"Required indexes: {indexes}\n"
            "Keep the same index and timestamp for every block.\n"
            f"{stricter}\n\n"
            f"SRT input:\n\n{srt_batch_to_text(batch)}"
        )
        resp = client.chat.completions.create(
            model=trans_cfg.get("model", "gpt-4o-mini"),
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0,
        )
        content = clean_srt_output(resp.choices[0].message.content or "")
        parsed = parse_srt(content)
        if len(parsed) != len(batch):
            debug_path = en_srt.with_suffix(f".batch_{batch_start + 1}.try_{attempt}.debug.txt")
            debug_path.write_text(content, encoding="utf-8")
            log(
                f"Batch {batch_start + 1}-{batch_start + len(batch)} count mismatch: "
                f"expected={len(batch)} got={len(parsed)}. Saved debug: {debug_path}"
            )
        return content, parsed

    def translate_batch(batch: list[SubtitleItem], batch_start: int) -> list[SubtitleItem]:
        last_content = ""
        last_parsed: list[SubtitleItem] = []
        for attempt in range(1, 3):
            log(f"Translating subtitles {batch_start + 1}-{batch_start + len(batch)} / {len(items)} attempt {attempt}")
            last_content, last_parsed = call_model(batch, batch_start, attempt)
            if len(last_parsed) == len(batch):
                # Use original indexes/timestamps to guarantee valid timing even if the model changes them slightly.
                return [
                    SubtitleItem(orig.index, orig.start, orig.end, translated.text)
                    for orig, translated in zip(batch, last_parsed)
                ]

        if len(batch) > 1:
            mid = len(batch) // 2
            log(
                f"Retry failed for subtitles {batch_start + 1}-{batch_start + len(batch)}; "
                "splitting into smaller batches."
            )
            return translate_batch(batch[:mid], batch_start) + translate_batch(batch[mid:], batch_start + mid)

        debug_path = en_srt.with_suffix(f".batch_{batch_start + 1}.failed.debug.txt")
        debug_path.write_text(last_content, encoding="utf-8")
        raise RuntimeError(
            f"Translation failed for subtitle #{batch[0].index}: expected 1 got {len(last_parsed)}. "
            f"Debug output saved: {debug_path}"
        )

    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        translated_items.extend(translate_batch(batch, start))

    write_srt(translated_items, en_srt, renumber=False)
    log(f"Wrote English SRT: {en_srt}")
    # Mismatch responses are useful only while diagnosing a failed translation.
    # Once the complete SRT has been written successfully, remove all stale batch
    # debug artifacts so the user-facing output directory stays clean.
    debug_pattern = f"{en_srt.stem}.batch_*.debug.txt"
    removed_debug = 0
    for debug_path in en_srt.parent.glob(debug_pattern):
        try:
            debug_path.unlink()
            removed_debug += 1
        except OSError as exc:
            log(f"WARNING: 无法删除翻译调试文件 {debug_path}: {exc}")
    if removed_debug:
        log(f"翻译已成功，自动清理了 {removed_debug} 个批次调试文件。")

def translate_srt(cfg: dict[str, Any], zh_srt: Path, en_srt: Path) -> None:
    provider = cfg.get("translate", {}).get("provider", "none").lower()
    if provider == "none":
        translate_none(zh_srt, en_srt)
    elif provider == "openai":
        translate_openai(cfg, zh_srt, en_srt)
    else:
        raise ValueError(f"Unsupported translate.provider: {provider}")


def prepare_tts(cfg: dict[str, Any], en_srt: Path, work_dir: Path) -> None:
    tts = cfg.get("tts", {})
    if not tts.get("auto_run", False):
        log("TTS auto_run=false。请在主界面点击“3 导入并处理 TTS 音频”。")
        return

    command = tts.get("command", "").strip()
    if not command:
        raise RuntimeError("tts.auto_run=true 但 tts.command 为空。")
    output_audio = resolve_path(tts.get("output_audio", "./output/en_voice.wav"))
    output_srt = resolve_path(tts.get("output_srt", "./output/en_synced.srt"))
    formatted = command.format(
        input_srt=str(en_srt),
        output_audio=str(output_audio),
        output_srt=str(output_srt),
        work_dir=str(work_dir),
    )
    log("RUN TTS external command: " + formatted)
    proc = subprocess.run(formatted, shell=True, cwd=str(ROOT))
    if proc.returncode != 0:
        raise RuntimeError(f"TTS command failed: {proc.returncode}")


def maybe_sync_subtitle(cfg: dict[str, Any], video_or_audio: Path, en_raw_srt: Path, final_srt: Path, tts_srt: Path) -> Path:
    sync = cfg.get("sync", {})
    if sync.get("prefer_tts_synced_srt", True) and tts_srt.exists():
        log(f"Using TTS synced SRT: {tts_srt}")
        return tts_srt

    if sync.get("use_ffsubsync", False):
        bin_name = check_bin(sync.get("ffsubsync_bin", "ffsubsync"))
        run_cmd([bin_name, str(video_or_audio), "-i", str(en_raw_srt), "-o", str(final_srt)])
        return final_srt

    log("未发现 en_synced.srt，且未启用 ffsubsync；将暂用 en_raw.srt。")
    return en_raw_srt


def fit_video_to_audio_segments(
    cfg: dict[str, Any],
    input_video: Path,
    audio_path: Path,
    zh_srt_path: Path,
    target_srt_path: Path,
    work_dir: Path,
) -> Path:
    """Keep English audio untouched and retime video intervals to its subtitle timeline."""
    if target_srt_path.name.lower() == "en_raw.srt":
        raise RuntimeError(
            "视频逐段变速模式需要 TTS 生成的同步字幕 en_synced.srt，"
            "en_raw.srt 仍是原视频时间轴，无法代表英文音频节奏。"
        )
    output = work_dir / "video_segment_fit.mp4"
    cache_version_file = work_dir / "video_segment_fit_version.txt"
    fit_cfg = cfg.get("compose", {}).get("video_segment_fit", {})
    min_speed = max(0.10, float(fit_cfg.get("min_speed", 0.75)))
    max_speed = max(min_speed, float(fit_cfg.get("max_speed", 1.35)))
    merge_short_sec = max(0.0, float(fit_cfg.get("merge_short_sec", 0.60)))
    cache_version = f"4-speed-limited-{min_speed:.3f}-{max_speed:.3f}-{merge_short_sec:.3f}"
    dependencies = [input_video, audio_path, zh_srt_path, target_srt_path]
    if (
        output.exists() and cache_version_file.exists()
        and cache_version_file.read_text(encoding="utf-8", errors="replace").strip() == cache_version
        and all(output.stat().st_mtime >= path.stat().st_mtime for path in dependencies)
    ):
        log(f"Reusing current video segment fit: {output}")
        return output
    source_items = parse_srt(zh_srt_path.read_text(encoding="utf-8"))
    target_items = parse_srt(target_srt_path.read_text(encoding="utf-8"))
    n = min(len(source_items), len(target_items))
    if n == 0:
        raise RuntimeError("视频变速模式需要有效的中文字幕和英文/TTS字幕时间轴。")
    if len(source_items) != len(target_items):
        log(f"WARNING: video segment fit subtitle counts differ: zh={len(source_items)} target={len(target_items)}; using {n}")

    video_duration = ffprobe_duration(cfg, input_video)
    audio_duration = ffprobe_duration(cfg, audio_path)
    subtitle_duration = max(srt_timestamp_to_seconds(item.end) for item in target_items[:n])
    target_total_duration = max(audio_duration, subtitle_duration)
    intervals: list[tuple[float, float, float]] = []  # source start, source end, target duration

    first_source_start = min(video_duration, srt_timestamp_to_seconds(source_items[0].start))
    first_target_start = max(0.0, srt_timestamp_to_seconds(target_items[0].start))
    if first_target_start > 0.005:
        intervals.append((0.0, max(0.04, first_source_start), first_target_start))

    # Map sentence-start anchors rather than manufacturing tiny gap clips. This keeps
    # every English pause and guarantees cumulative target duration.
    for i in range(n):
        source_start = min(video_duration, srt_timestamp_to_seconds(source_items[i].start))
        source_end = (
            min(video_duration, srt_timestamp_to_seconds(source_items[i + 1].start))
            if i + 1 < n else video_duration
        )
        target_start = max(0.0, srt_timestamp_to_seconds(target_items[i].start))
        target_end = (
            max(target_start, srt_timestamp_to_seconds(target_items[i + 1].start))
            if i + 1 < n else target_total_duration
        )
        target_duration = target_end - target_start
        if target_duration <= 0.005:
            continue
        if source_end - source_start < 0.005:
            source_start = max(0.0, source_start - 0.04)
            source_end = max(source_start + 0.04, source_end)
        intervals.append((source_start, source_end, target_duration))

    # Very short subtitle intervals produce violent speed changes. Merge them with
    # a neighbour before calculating speed so the adjustment is visually smoother.
    merged: list[tuple[float, float, float]] = []
    i = 0
    while i < len(intervals):
        start, end, target_duration = intervals[i]
        source_duration = end - start
        if i + 1 < len(intervals) and (source_duration < merge_short_sec or target_duration < merge_short_sec):
            next_start, next_end, next_target = intervals[i + 1]
            merged.append((start, next_end, target_duration + next_target))
            i += 2
        elif merged and i == len(intervals) - 1 and (source_duration < merge_short_sec or target_duration < merge_short_sec):
            prev_start, _prev_end, prev_target = merged.pop()
            merged.append((prev_start, end, prev_target + target_duration))
            i += 1
        else:
            merged.append((start, end, target_duration))
            i += 1
    intervals = merged

    filters: list[str] = []
    labels: list[str] = []
    speeds: list[float] = []
    limited: list[str] = []
    for i, (start, end, target_duration) in enumerate(intervals):
        source_duration = max(0.001, end - start)
        raw_speed = source_duration / target_duration
        actual_speed = min(max_speed, max(min_speed, raw_speed))
        speeds.append(actual_speed)
        label = f"seg{i}"
        if raw_speed > max_speed:
            # Keep the target duration exact by taking a centred sub-window. The
            # omitted frames become a small visual jump instead of an extreme fast-forward.
            used = min(source_duration, target_duration * max_speed)
            trim_start = start + (source_duration - used) / 2
            trim_end = trim_start + used
            filters.append(
                f"[0:v]trim=start={trim_start:.6f}:end={trim_end:.6f},"
                f"setpts=(PTS-STARTPTS)/{max_speed:.9f},trim=duration={target_duration:.6f}[{label}]"
            )
            limited.append(f"段{i + 1}: 原需 {raw_speed:.2f}x，限制为 {max_speed:.2f}x（画面轻微跳转）")
        elif raw_speed < min_speed:
            # Retiming at the lower limit ends early; clone the last frame for the
            # remaining time so the English audio clock remains authoritative.
            filters.append(
                f"[0:v]trim=start={start:.6f}:end={end:.6f},"
                f"setpts=(PTS-STARTPTS)/{min_speed:.9f},tpad=stop_mode=clone:stop_duration={target_duration:.6f},"
                f"trim=duration={target_duration:.6f}[{label}]"
            )
            limited.append(f"段{i + 1}: 原需 {raw_speed:.2f}x，限制为 {min_speed:.2f}x（末帧短暂停留）")
        else:
            filters.append(
                f"[0:v]trim=start={start:.6f}:end={end:.6f},"
                f"setpts=(PTS-STARTPTS)/{raw_speed:.9f}[{label}]"
            )
        labels.append(f"[{label}]")
    filters.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0[joined]")
    # Frame quantization across many trims can accumulate small duration errors.
    # Pad/trim once at the end so the video clock exactly follows the audio clock.
    filters.append(
        f"[joined]fps=30,tpad=stop_mode=clone:stop_duration=10,"
        f"trim=duration={target_total_duration:.6f},setpts=PTS-STARTPTS[v]"
    )

    filter_script = work_dir / "video_segment_fit_filters.txt"
    filter_script.write_text(";".join(filters), encoding="utf-8")
    report = work_dir / "video_segment_fit_report.txt"
    report.write_text(
        f"速度限制：{min_speed:.2f}x ～ {max_speed:.2f}x\n"
        f"合并后区间数：{len(intervals)}\n"
        f"触发限制：{len(limited)} 段\n\n" + ("\n".join(limited) if limited else "无极端变速段"),
        encoding="utf-8",
    )
    if limited:
        log(f"WARNING: {len(limited)} 个视频区间超过速度范围，详情：{report}")
        for line in limited[:10]:
            log("  " + line)
    ffmpeg = check_bin(cfg["ffmpeg"].get("ffmpeg_bin", "ffmpeg"))
    log(
        f"Video segment fit: {len(intervals)} intervals, video speed range "
        f"{min(speeds):.2f}x..{max(speeds):.2f}x; English audio remains unchanged."
    )
    run_cmd([
        ffmpeg, "-y", "-i", str(input_video),
        "-filter_complex_script", str(filter_script), "-map", "[v]", "-an",
        "-c:v", "libx264", "-crf", "12", "-preset", "fast",
        str(output),
    ])
    cache_version_file.write_text(cache_version, encoding="utf-8")
    return output


def align_srt_to_audio_duration(
    cfg: dict[str, Any], audio_path: Path, srt_path: Path, work_dir: Path,
) -> Path:
    """Use audio as the authoritative clock for video-fit mode."""
    items = parse_srt(srt_path.read_text(encoding="utf-8"))
    if not items:
        return srt_path
    audio_duration = ffprobe_duration(cfg, audio_path)
    subtitle_duration = srt_items_duration(items)
    if audio_duration <= 0 or subtitle_duration <= 0 or abs(audio_duration - subtitle_duration) <= 0.08:
        return srt_path
    aligned = work_dir / "en_synced_video_fit.srt"
    if aligned.exists() and aligned.stat().st_mtime >= max(audio_path.stat().st_mtime, srt_path.stat().st_mtime):
        return aligned
    scale = audio_duration / subtitle_duration
    scale_srt_file(srt_path, aligned, scale)
    log(
        f"Video-fit subtitle clock aligned to audio: srt={subtitle_duration:.3f}s, "
        f"audio={audio_duration:.3f}s, scale={scale:.6f}, output={aligned}"
    )
    return aligned


def escape_subtitles_path_for_ffmpeg(path: Path) -> str:
    # FFmpeg subtitles filter on Windows wants D\:/path/file.srt style, and backslashes are troublesome.
    s = path.resolve().as_posix()
    if re.match(r"^[A-Za-z]:/", s):
        s = s[0] + r"\:" + s[2:]
    s = s.replace("'", r"\'")
    return s


def compose_video(
    cfg: dict[str, Any], input_video: Path, audio_path: Path, srt_path: Path,
    output_video: Path, cleanup_timing_srt: Path | None = None,
) -> None:
    ffmpeg = check_bin(cfg["ffmpeg"]["ffmpeg_bin"])
    comp = cfg.get("compose", {})
    ensure_dir(output_video.parent)
    internal_dir = output_video.parent / "_internal"
    ensure_dir(internal_dir)

    animation = str(comp.get("subtitle_animation", "none"))
    subtitle_source = srt_path
    if animation != "none":
        subtitle_source = build_effect_ass(srt_path, internal_dir / "animated_subtitles.ass", animation)
    sub_path = escape_subtitles_path_for_ffmpeg(subtitle_source)
    style = comp.get("subtitle_style", "FontName=Arial,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Alignment=2,MarginV=38")
    style = re.sub(r"(^|,)FontName=@", r"\1FontName=", str(style))
    subtitle_filter = f"subtitles='{sub_path}':force_style='{style}'"
    timing_srt = cleanup_timing_srt or (resolve_path(cfg.get("work_dir", "./output")) / "zh.srt")
    vf = build_cleanup_graph(comp, subtitle_filter, timing_srt, dynamic_timing=True, video_size=ffprobe_video_size(cfg, input_video))
    filter_script = internal_dir / "compose_filters.txt"
    filter_script.write_text(vf, encoding="utf-8")

    run_cmd_logged([
        ffmpeg, "-y",
        "-i", str(input_video),
        "-i", str(audio_path),
        "-filter_complex_script", str(filter_script),
        "-map", "[v]", "-map", "1:a",
        "-c:v", comp.get("video_codec", "libx264"),
        "-crf", str(comp.get("crf", 18)),
        "-preset", comp.get("preset", "medium"),
        "-c:a", comp.get("audio_codec", "aac"),
        "-af", "apad",
        "-b:a", comp.get("audio_bitrate", "192k"),
        "-shortest",
        str(output_video),
    ], internal_dir / "compose_ffmpeg.log")
    log(f"Final video written: {output_video}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Video subtitle translation + dubbing automation pipeline")
    parser.add_argument("--config", default="", help="Optional standalone YAML config; normally uses config.default.yaml + config.user.yaml")
    parser.add_argument("--input", default=None, help="Input video path; overrides config input_video")
    parser.add_argument(
        "--step",
        default="all",
        choices=["all", "extract", "asr", "translate", "prepare_tts", "compose"],
        help="Pipeline step to run",
    )
    args = parser.parse_args()

    cfg = load_yaml_config(resolve_path(args.config, Path.cwd())) if args.config else load_project_config()
    input_value = str(args.input or cfg.get("input_video", "")).strip()
    if not input_value:
        raise FileNotFoundError("尚未选择输入视频，请先在图形界面顶部点击“选择 / 更换视频”。")
    input_video = resolve_path(input_value)
    work_dir = resolve_path(cfg.get("work_dir", "./output"))
    ensure_dir(work_dir)
    internal_dir = work_dir / "_internal"
    ensure_dir(internal_dir)

    audio_wav = internal_dir / "audio.wav"
    zh_srt = work_dir / "zh.srt"
    en_raw_srt = work_dir / "en_raw.srt"
    en_final_srt = internal_dir / "en_synced_by_ffsubsync.srt"
    tts_audio = resolve_path(cfg.get("tts", {}).get("output_audio", "./output/en_voice.wav"))
    tts_srt = resolve_path(cfg.get("tts", {}).get("output_srt", "./output/en_synced.srt"))
    output_video = resolve_path(cfg.get("compose", {}).get("output_video", "./output/final.mp4"))

    if not input_video.is_file():
        raise FileNotFoundError(f"Input video not found: {input_video}")

    step = args.step
    if step in ("all", "extract"):
        extract_audio(cfg, input_video, audio_wav)
        if step == "extract":
            return

    if step in ("all", "asr"):
        if not audio_wav.exists():
            extract_audio(cfg, input_video, audio_wav)
        transcribe(cfg, audio_wav, zh_srt)
        if step == "asr":
            return

    if step in ("all", "translate"):
        if not zh_srt.exists():
            raise FileNotFoundError(f"Missing {zh_srt}. Run --step asr first.")
        translate_srt(cfg, zh_srt, en_raw_srt)
        if step == "translate":
            return

    if step in ("all", "prepare_tts"):
        if not en_raw_srt.exists():
            raise FileNotFoundError(f"Missing {en_raw_srt}. Run --step translate first.")
        prepare_tts(cfg, en_raw_srt, work_dir)
        if step == "prepare_tts":
            return

    if step == "all":
        if not tts_audio.exists():
            log(f"TTS audio not found yet: {tts_audio}")
            log("已完成到 prepare_tts。请生成英文配音后，再运行：python src/pipeline.py --step compose")
            return

    if step in ("all", "compose"):
        if not tts_audio.exists():
            raise FileNotFoundError(f"Missing TTS audio: {tts_audio}")
        if not en_raw_srt.exists():
            raise FileNotFoundError(f"Missing English SRT: {en_raw_srt}")
        selected_srt = maybe_sync_subtitle(cfg, tts_audio, en_raw_srt, en_final_srt, tts_srt)

        media_fit_mode = str(cfg.get("compose", {}).get("media_fit_mode", "audio_segment_fit"))
        compose_input_video = input_video
        cleanup_timing_srt = zh_srt
        if media_fit_mode == "video_segment_fit":
            if not zh_srt.exists():
                raise FileNotFoundError(f"视频逐段变速需要中文字幕时间轴：{zh_srt}")
            video_fit_srt = align_srt_to_audio_duration(cfg, tts_audio, selected_srt, internal_dir)
            compose_input_video = fit_video_to_audio_segments(
                cfg, input_video, tts_audio, zh_srt, video_fit_srt, internal_dir,
            )
            fitted_audio, fitted_srt = tts_audio, video_fit_srt
            cleanup_timing_srt = fitted_srt
        else:
            # 默认：保持视频速度，逐句调整英文音频速度。
            ssf_cfg = cfg.get("compose", {}).get("smart_segment_fit", {})
            if ssf_cfg.get("enabled", False) and zh_srt.exists() and selected_srt.exists():
                fitted_audio, fitted_srt = smart_segment_fit(
                    cfg, input_video, tts_audio, selected_srt, zh_srt, internal_dir,
                )
            else:
                fitted_audio, fitted_srt = maybe_fit_audio_to_video_duration(
                    cfg, input_video, tts_audio, selected_srt, internal_dir,
                )

        compose_video(cfg, compose_input_video, fitted_audio, fitted_srt, output_video, cleanup_timing_srt)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log("ERROR: " + str(exc))
        sys.exit(1)
