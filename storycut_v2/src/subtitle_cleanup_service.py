from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from .media_service import _ffmpeg_supports_filter, _resolve_tool


ProgressCallback = Callable[[float, str], None]


def render_subtitle_clean_video(
    source: Path,
    output: Path,
    style: dict[str, object],
    width: int,
    height: int,
    duration_sec: float,
    config: dict[str, Any],
    app_root: Path,
    progress: ProgressCallback,
) -> Path:
    ffmpeg = _resolve_tool(
        str(config.get("shared", {}).get("ffmpeg_bin", "ffmpeg")), app_root, "ffmpeg"
    )
    if not ffmpeg:
        raise RuntimeError("未找到 FFmpeg，无法生成去字幕视频")
    if not source.exists():
        raise FileNotFoundError(f"原视频不存在：{source}")

    render_style = dict(style)
    if (
        str(render_style.get("cleanupMode", "mask")).lower() == "delogo"
        and not _ffmpeg_supports_filter(ffmpeg, "delogo")
    ):
        render_style["cleanupMode"] = "blur"
    video_filter = build_cleanup_filter(render_style, width, height)
    export = config.get("export", {})
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        video_filter,
        "-c:v",
        str(export.get("video_codec", "libx264")),
        "-preset",
        str(export.get("preset", "veryfast")),
        "-crf",
        str(export.get("crf", 18)),
        "-c:a",
        "aac",
        "-b:a",
        str(export.get("audio_bitrate", "192k")),
        "-movflags",
        "+faststart",
        "-progress",
        "pipe:1",
        "-nostats",
        str(output),
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if process.stdout is not None:
        for line in process.stdout:
            key, _, value = line.strip().partition("=")
            if key not in {"out_time_us", "out_time_ms"}:
                continue
            try:
                rendered = float(value) / 1_000_000
            except ValueError:
                continue
            ratio = min(0.98, rendered / max(0.1, duration_sec))
            progress(ratio, f"正在生成去字幕视频：{round(ratio * 100)}%")
    stderr = process.stderr.read() if process.stderr is not None else ""
    if process.wait() != 0:
        raise RuntimeError(stderr.strip() or "FFmpeg 生成去字幕视频失败")
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError("FFmpeg 未生成有效的去字幕视频")
    progress(1.0, "去字幕视频生成完成")
    return output


def build_cleanup_filter(style: dict[str, object], width: int, height: int) -> str:
    width = max(4, int(width))
    height = max(4, int(height))
    mode = str(style.get("cleanupMode", "mask")).lower()
    if mode not in {"mask", "blur", "delogo"}:
        mode = "mask"
    x_ratio = min(0.95, max(0.0, float(style.get("cleanupX", 0.08))))
    y_ratio = min(0.95, max(0.0, float(style.get("cleanupY", 0.82))))
    w_ratio = min(1.0, max(0.02, float(style.get("cleanupWidth", 0.84))))
    h_ratio = min(0.4, max(0.02, float(style.get("cleanupHeight", 0.14))))
    opacity = min(1.0, max(0.0, float(style.get("cleanupOpacity", 0.78))))
    radius = max(1, min(40, int(style.get("blurRadius", 12))))
    power = max(1, min(4, int(style.get("blurPower", 2))))
    padding = min(
        120,
        max(0, min(80, int(style.get("regionPadding", 4))))
        + max(0, min(60, int(style.get("feather", 12)))),
    )
    base_x = min(width - 4, round(width * x_ratio))
    base_y = min(height - 4, round(height * y_ratio))
    base_w = min(width - base_x, max(4, round(width * w_ratio)))
    base_h = min(height - base_y, max(4, round(height * h_ratio)))
    x = max(0, base_x - padding)
    y = max(0, base_y - padding)
    w = min(width - x, base_w + padding * 2)
    h = min(height - y, base_h + padding * 2)
    if mode == "mask":
        return f"drawbox=x={x}:y={y}:w={w}:h={h}:color=black@{opacity:.2f}:t=fill"
    if mode == "delogo":
        dx, dy = max(2, x), max(2, y)
        dw = max(2, min(w, width - dx - 2))
        dh = max(2, min(h, height - dy - 2))
        return f"delogo=x={dx}:y={dy}:w={dw}:h={dh}:show=0"
    return (
        "split=2[base][region];"
        f"[region]crop=w={w}:h={h}:x={x}:y={y},"
        f"gblur=sigma={radius}:steps={power}[blur];"
        f"[base][blur]overlay=x={x}:y={y}"
    )
