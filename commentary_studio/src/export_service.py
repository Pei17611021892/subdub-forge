from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from .media_service import _resolve_tool


ProgressCallback = Callable[[float, str], None]


def render_rough_preview(
    source_video: Path,
    rough_cut_json: Path,
    output_video: Path,
    narration_audio: Path | None,
    subtitle_srt: Path | None,
    source_width: int,
    source_height: int,
    config: dict[str, Any],
    app_root: Path,
    progress: ProgressCallback,
) -> dict[str, Any]:
    timeline = json.loads(rough_cut_json.read_text(encoding="utf-8"))
    clips = [dict(item) for item in timeline.get("clips", []) if isinstance(item, dict)]
    total_duration = float(timeline.get("duration_sec", 0) or 0)
    if not clips or total_duration <= 0:
        raise ValueError("粗剪时间线中没有可导出的镜头")

    shared = config.get("shared", {})
    ffmpeg = _resolve_tool(str(shared.get("ffmpeg_bin", "ffmpeg")), app_root, "ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 FFmpeg，无法生成粗剪预览")

    if narration_audio is None or not narration_audio.exists():
        raise ValueError("缺少英文配音，最终视频不会使用原片声音")
    export_config = config.get("export", {})
    configured_width = int(export_config.get("width", 1080) or 1080)
    configured_height = int(export_config.get("height", 1920) or 1920)
    fps = int(export_config.get("fps", 30) or 30)
    cleanup_original_subtitles = bool(export_config.get("cleanup_original_subtitles", True))
    source_crop_ratio = min(1.0, max(0.6, float(export_config.get("source_crop_height_ratio", 0.82) or 0.82)))
    fit_mode = str(export_config.get("fit_mode", "original")).lower()
    width = int(source_width or configured_width) if fit_mode == "original" else configured_width
    height = int(source_height or configured_height) if fit_mode == "original" else configured_height
    cleanup_mode = str(export_config.get("original_subtitle_cleanup_mode", "none")).lower()
    if cleanup_mode not in {"none", "mask", "blur", "delogo"}:
        cleanup_mode = "none"
    filters: list[str] = []
    concat_inputs: list[str] = []
    for index, clip in enumerate(clips):
        start = float(clip.get("source_start", 0))
        end = float(clip.get("source_end", 0))
        if end - start < 0.05:
            continue
        source_cleanup = f"crop=iw:ih*{source_crop_ratio:.3f}:0:0," if cleanup_original_subtitles else ""
        if fit_mode == "crop":
            fit_filter = (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height}"
            )
        elif fit_mode == "contain":
            fit_filter = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
            )
        else:
            fit_filter = ""
        filters.append(
            f"[0:v]trim=start={start:.3f}:end={end:.3f},"
            f"setpts=PTS-STARTPTS,fps={fps},"
            f"{source_cleanup}"
            f"{fit_filter + ',' if fit_filter else ''}setsar=1,format=yuv420p[v{index}]"
        )
        concat_inputs.append(f"[v{index}]")

    valid_count = len([value for value in filters if value.startswith("[0:v]")])
    if valid_count == 0:
        raise ValueError("粗剪时间线中的镜头时长无效")
    has_subtitles = bool(subtitle_srt and subtitle_srt.exists())
    needs_cleanup = cleanup_mode != "none"
    concat_video_output = "[vjoined]" if needs_cleanup else ("[vbase]" if has_subtitles else "[vout]")
    filters.append(
        "".join(concat_inputs)
        + f"concat=n={valid_count}:v=1:a=0"
        + concat_video_output
    )
    if needs_cleanup:
        cleanup_x_ratio = min(0.95, max(0.0, float(export_config.get("original_subtitle_cleanup_x", 0.08) or 0.08)))
        cleanup_y_ratio = min(0.95, max(0.0, float(export_config.get("original_subtitle_cleanup_y", 0.82) or 0.82)))
        cleanup_w_ratio = min(1.0, max(0.02, float(export_config.get("original_subtitle_cleanup_width", 0.84) or 0.84)))
        cleanup_h_ratio = min(0.4, max(0.02, float(export_config.get("original_subtitle_cleanup_height", 0.14) or 0.14)))
        cleanup_opacity = min(1.0, max(0.0, float(export_config.get("original_subtitle_cleanup_opacity", 0.78) or 0.78)))
        blur_radius = max(1, min(40, int(export_config.get("original_subtitle_blur_radius", 12) or 12)))
        blur_power = max(1, min(4, int(export_config.get("original_subtitle_blur_power", 2) or 2)))
        region_padding = max(0, min(80, int(export_config.get("original_subtitle_region_padding", 4) or 4)))
        feather = max(0, min(60, int(export_config.get("original_subtitle_feather", 12) or 12)))
        padding = min(120, region_padding + feather)

        base_x = min(width - 4, round(width * cleanup_x_ratio))
        base_y = min(height - 4, round(height * cleanup_y_ratio))
        base_w = min(width - base_x, max(4, round(width * cleanup_w_ratio)))
        base_h = min(height - base_y, max(4, round(height * cleanup_h_ratio)))
        cleanup_x = max(0, base_x - padding)
        cleanup_y = max(0, base_y - padding)
        cleanup_w = min(width - cleanup_x, base_w + padding * 2)
        cleanup_h = min(height - cleanup_y, base_h + padding * 2)
        cleanup_output = "[vbase]" if has_subtitles else "[vout]"
        if cleanup_mode == "mask":
            filters.append(
                f"[vjoined]drawbox=x={cleanup_x}:y={cleanup_y}:w={cleanup_w}:h={cleanup_h}:"
                f"color=black@{cleanup_opacity:.2f}:t=fill{cleanup_output}"
            )
        elif cleanup_mode == "delogo":
            # Delogo interpolates inward from surrounding pixels and cannot touch
            # the frame edge, matching the proven implementation in the old tool.
            delogo_x = max(2, cleanup_x)
            delogo_y = max(2, cleanup_y)
            delogo_w = max(2, min(cleanup_w, width - delogo_x - 2))
            delogo_h = max(2, min(cleanup_h, height - delogo_y - 2))
            filters.append(
                f"[vjoined]delogo=x={delogo_x}:y={delogo_y}:w={delogo_w}:h={delogo_h}:"
                f"show=0{cleanup_output}"
            )
        else:
            filters.extend(
                [
                    "[vjoined]split=2[cleanbase][cleanregion]",
                    f"[cleanregion]crop=w={cleanup_w}:h={cleanup_h}:x={cleanup_x}:y={cleanup_y},"
                    f"boxblur=luma_radius={blur_radius}:luma_power={blur_power}[cleanblur]",
                    f"[cleanbase][cleanblur]overlay=x={cleanup_x}:y={cleanup_y}{cleanup_output}",
                ]
            )
    if has_subtitles:
        ass_path = output_video.parent / "_internal" / "shorts_subtitles.ass"
        _build_shorts_ass(subtitle_srt, ass_path, width, height, export_config)
        filters.append(f"[vbase]subtitles='{_escape_subtitle_path(ass_path)}'[vout]")
    filters.append(
        f"[1:a]atrim=start=0:end={total_duration:.3f},"
        "asetpts=PTS-STARTPTS,aresample=48000[aout]"
    )

    output_video.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_video),
    ]
    command.extend(["-i", str(narration_audio)])
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
        "-map",
        "[vout]",
        ]
    )
    command.extend(["-map", "[aout]"])
    command.extend(
        [
            "-c:v",
            str(export_config.get("video_codec", "libx264")),
            "-preset",
            str(export_config.get("preset", "veryfast")),
            "-crf",
            str(export_config.get("crf", 18)),
            "-pix_fmt",
            "yuv420p",
        ]
    )
    command.extend(["-c:a", "aac", "-b:a", str(export_config.get("audio_bitrate", "192k"))])
    command.extend(["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(output_video)])

    progress(0.03, f"正在裁剪并拼接 {valid_count} 个镜头…")
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
            if key in {"out_time_us", "out_time_ms"}:
                try:
                    rendered = float(value) / 1_000_000
                    ratio = min(0.96, rendered / total_duration)
                    progress(max(0.05, ratio), f"正在生成粗剪预览：{round(ratio * 100)}%")
                except ValueError:
                    pass
    stderr = process.stderr.read() if process.stderr is not None else ""
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(stderr.strip() or f"FFmpeg 退出码 {return_code}")
    if not output_video.exists() or output_video.stat().st_size == 0:
        raise RuntimeError("FFmpeg 未生成有效的预览文件")
    progress(1.0, "粗剪预览生成完成")
    return {
        "path": str(output_video),
        "duration_sec": total_duration,
        "clip_count": valid_count,
        "file_size": output_video.stat().st_size,
        "has_audio": True,
        "audio_mode": "narration_only",
        "width": width,
        "height": height,
        "fit_mode": fit_mode,
        "subtitles_burned": has_subtitles,
        "original_subtitles_cleaned": cleanup_original_subtitles or needs_cleanup,
        "original_subtitle_cleanup_mode": cleanup_mode,
    }


def _escape_subtitle_path(path: Path) -> str:
    value = path.resolve().as_posix()
    if re.match(r"^[A-Za-z]:/", value):
        value = value[0] + r"\:" + value[2:]
    return value.replace("'", r"\'")


def _build_shorts_ass(
    srt_path: Path,
    ass_path: Path,
    width: int,
    height: int,
    config: dict[str, Any],
) -> None:
    text = srt_path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    events = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.splitlines()
        timing_index = next((index for index, line in enumerate(lines) if "-->" in line), -1)
        if timing_index < 0:
            continue
        left, right = [part.strip() for part in lines[timing_index].split("-->", 1)]
        caption = r"\N".join(lines[timing_index + 1 :]).replace("{", r"\{").replace("}", r"\}")
        events.append(
            f"Dialogue: 0,{_ass_time(left)},{_ass_time(right)},Default,,0,0,0,,"
            rf"{{\fad(70,70)}}{caption}"
        )
    font_name = str(config.get("subtitle_font", "Arial"))
    font_size = int(config.get("subtitle_font_size", 48) or 48)
    margin_v = int(config.get("subtitle_margin_v", 72) or 72)
    margin_h = int(config.get("subtitle_margin_h", 72) or 72)
    bold = -1 if bool(config.get("subtitle_bold", True)) else 0
    background_enabled = bool(config.get("subtitle_background_enabled", True))
    background_opacity = min(0.95, max(0.0, float(config.get("subtitle_background_opacity", 0.62) or 0.62)))
    outline_width = int(config.get("subtitle_outline_width", 3) or 3)
    box_padding = int(config.get("subtitle_box_padding", 12) or 12)
    border_style = 3 if background_enabled else 1
    border_size = box_padding if background_enabled else outline_width
    alpha = round((1.0 - background_opacity) * 255) if background_enabled else 0
    border_color = f"&H{alpha:02X}000000" if background_enabled else "&H00000000"
    shadow = 0 if background_enabled else 1
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},&H00FFFFFF,&H00FFFFFF,{border_color},{border_color},{bold},0,0,0,100,100,0,0,{border_style},{border_size},{shadow},2,{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")


def _ass_time(value: str) -> str:
    hours, minutes, rest = value.replace(",", ".").split(":")
    seconds = float(rest)
    return f"{int(hours)}:{int(minutes):02d}:{seconds:05.2f}"
