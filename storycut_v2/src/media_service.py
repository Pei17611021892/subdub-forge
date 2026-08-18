from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _resolve_tool(value: str, app_root: Path, name: str) -> str | None:
    configured = str(value or "").strip()
    if configured and configured.lower() != name:
        path = Path(configured)
        if not path.is_absolute():
            path = (app_root / path).resolve()
        if path.exists():
            return str(path)

    discovered = shutil.which(configured or name)
    if discovered:
        return discovered

    repository_root = app_root.parent
    candidates = (
        repository_root / "tools" / "ffmpeg" / "bin" / f"{name}.exe",
        repository_root / "tools" / "ffmpeg" / f"{name}.exe",
        repository_root / "tools" / f"{name}.exe",
    )
    return next((str(path) for path in candidates if path.exists()), None)


def analyze_media(video: Path, cover: Path, config: dict[str, Any], app_root: Path) -> dict[str, Any]:
    shared = config.get("shared", {})
    ffprobe = _resolve_tool(str(shared.get("ffprobe_bin", "ffprobe")), app_root, "ffprobe")
    ffmpeg = _resolve_tool(str(shared.get("ffmpeg_bin", "ffmpeg")), app_root, "ffmpeg")

    if ffprobe:
        metadata = _probe_with_ffprobe(video, ffprobe)
    else:
        metadata = _probe_with_pyav(video)

    cover.parent.mkdir(parents=True, exist_ok=True)
    if ffmpeg:
        _cover_with_ffmpeg(video, cover, ffmpeg, float(metadata.get("duration_sec", 0)))
        metadata["cover_backend"] = "ffmpeg"
    else:
        _cover_with_pyav(video, cover, float(metadata.get("duration_sec", 0)))
        metadata["cover_backend"] = "pyav"

    metadata["ffmpeg_available"] = bool(ffmpeg)
    metadata["ffprobe_available"] = bool(ffprobe)
    return metadata


def extract_preview_frame(
    video: Path,
    output: Path,
    timestamp: float,
    config: dict[str, Any],
    app_root: Path,
) -> str:
    shared = config.get("shared", {})
    ffmpeg = _resolve_tool(str(shared.get("ffmpeg_bin", "ffmpeg")), app_root, "ffmpeg")
    output.parent.mkdir(parents=True, exist_ok=True)
    if ffmpeg:
        command = [
            ffmpeg,
            "-y",
            "-ss",
            f"{max(0.0, timestamp):.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            "scale=960:-2",
            "-q:v",
            "3",
            str(output),
        ]
        subprocess.run(command, capture_output=True, check=True)
        return "ffmpeg"
    _frame_with_pyav(video, output, timestamp, 960)
    return "pyav"


def render_subtitle_effect_preview(
    video: Path,
    output: Path,
    timestamp: float,
    style: dict[str, object],
    video_width: int,
    video_height: int,
    config: dict[str, Any],
    app_root: Path,
) -> None:
    """Render the real FFmpeg cleanup effect used by export onto one source frame."""
    shared = config.get("shared", {})
    ffmpeg = _resolve_tool(str(shared.get("ffmpeg_bin", "ffmpeg")), app_root, "ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 FFmpeg，无法生成真实字幕底板预览")

    width = max(2, int(video_width))
    height = max(2, int(video_height))
    mode = str(style.get("cleanupMode", "mask"))
    x_ratio = min(0.95, max(0.0, float(style.get("cleanupX", 0.08))))
    y_ratio = min(0.95, max(0.0, float(style.get("cleanupY", 0.82))))
    w_ratio = min(1.0, max(0.02, float(style.get("cleanupWidth", 0.84))))
    h_ratio = min(0.4, max(0.02, float(style.get("cleanupHeight", 0.14))))
    opacity = min(1.0, max(0.0, float(style.get("cleanupOpacity", 0.78))))
    radius = max(1, min(40, int(style.get("blurRadius", 12))))
    power = max(1, min(4, int(style.get("blurPower", 2))))
    region_padding = max(0, min(80, int(style.get("regionPadding", 4))))
    feather = max(0, min(60, int(style.get("feather", 12))))
    padding = min(120, region_padding + feather)

    base_x = min(width - 4, round(width * x_ratio))
    base_y = min(height - 4, round(height * y_ratio))
    base_w = min(width - base_x, max(4, round(width * w_ratio)))
    base_h = min(height - base_y, max(4, round(height * h_ratio)))
    x = max(0, base_x - padding)
    y = max(0, base_y - padding)
    w = min(width - x, base_w + padding * 2)
    h = min(height - y, base_h + padding * 2)

    if mode == "mask":
        effect = f"drawbox=x={x}:y={y}:w={w}:h={h}:color=black@{opacity:.2f}:t=fill"
    elif mode == "delogo":
        dx, dy = max(2, x), max(2, y)
        dw = max(2, min(w, width - dx - 2))
        dh = max(2, min(h, height - dy - 2))
        effect = f"delogo=x={dx}:y={dy}:w={dw}:h={dh}:show=0"
    else:
        effect = (
            f"split=2[base][region];"
            f"[region]crop=w={w}:h={h}:x={x}:y={y},"
            f"boxblur=luma_radius={radius}:luma_power={power}[blur];"
            f"[base][blur]overlay=x={x}:y={y}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, timestamp):.3f}", "-i", str(video),
        "-frames:v", "1", "-vf", f"{effect},scale=960:-2",
        "-q:v", "3", str(output),
    ]
    subprocess.run(command, capture_output=True, check=True)


def _probe_with_ffprobe(video: Path, ffprobe: str) -> dict[str, Any]:
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(video),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    duration = _float_value(payload.get("format", {}).get("duration")) or _float_value(video_stream.get("duration"))
    return {
        "duration_sec": duration,
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
        "fps": _fraction_value(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")),
        "video_codec": str(video_stream.get("codec_name") or "unknown"),
        "audio_codec": str(audio_streams[0].get("codec_name") or "none") if audio_streams else "none",
        "audio_tracks": len(audio_streams),
        "file_size": video.stat().st_size,
        "probe_backend": "ffprobe",
    }


def _probe_with_pyav(video: Path) -> dict[str, Any]:
    import av

    with av.open(str(video)) as container:
        video_stream = next(iter(container.streams.video), None)
        audio_streams = list(container.streams.audio)
        if video_stream is None:
            raise ValueError("文件中没有视频轨道")
        duration = float(container.duration / av.time_base) if container.duration else 0.0
        if not duration and video_stream.duration is not None and video_stream.time_base is not None:
            duration = float(video_stream.duration * video_stream.time_base)
        rate = video_stream.average_rate or video_stream.base_rate
        return {
            "duration_sec": duration,
            "width": int(video_stream.width or 0),
            "height": int(video_stream.height or 0),
            "fps": float(rate) if rate else 0.0,
            "video_codec": str(video_stream.codec_context.name or "unknown"),
            "audio_codec": str(audio_streams[0].codec_context.name or "none") if audio_streams else "none",
            "audio_tracks": len(audio_streams),
            "file_size": video.stat().st_size,
            "probe_backend": "pyav",
        }


def _cover_with_ffmpeg(video: Path, cover: Path, ffmpeg: str, duration: float) -> None:
    timestamp = min(max(duration * 0.12, 0.5), 10.0) if duration else 1.0
    command = [
        ffmpeg,
        "-y",
        "-ss",
        f"{timestamp:.3f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-vf",
        "scale=640:-2",
        str(cover),
    ]
    subprocess.run(command, capture_output=True, check=True)


def _cover_with_pyav(video: Path, cover: Path, duration: float) -> None:
    timestamp = min(max(duration * 0.12, 0.5), 10.0) if duration else 1.0
    _frame_with_pyav(video, cover, timestamp, 640)


def _frame_with_pyav(video: Path, output: Path, timestamp: float, max_width: int) -> None:
    import av
    from PySide6.QtGui import QImage

    with av.open(str(video)) as container:
        stream = next(iter(container.streams.video), None)
        if stream is None:
            raise ValueError("文件中没有视频轨道")
        if stream.time_base:
            container.seek(int(max(0.0, timestamp) / float(stream.time_base)), stream=stream, any_frame=False, backward=True)
        frame = next(container.decode(stream), None)
        if frame is None:
            raise ValueError("无法从视频中解码封面帧")
        width = min(max_width, frame.width)
        height = max(2, round(frame.height * width / frame.width))
        height -= height % 2
        array = frame.reformat(width=width, height=height, format="rgb24").to_ndarray()
        image = QImage(array.data, width, height, int(array.strides[0]), QImage.Format.Format_RGB888).copy()
        if not image.save(str(cover), "JPG", 88):
            raise ValueError("封面图片保存失败")


def _float_value(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _fraction_value(value: Any) -> float:
    text = str(value or "0")
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        denominator_value = _float_value(denominator)
        return _float_value(numerator) / denominator_value if denominator_value else 0.0
    return _float_value(text)
