from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from .media_service import _ffmpeg_supports_filter, _resolve_tool


ProgressCallback = Callable[[float, str], None]


def render_rough_preview(
    source_video: Path | None,
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

    uses_timeline_sources = any(str(item.get("source_path", "")).strip() for item in clips)
    if uses_timeline_sources:
        missing = [
            str(item.get("source_path", "")).strip() or "（未记录路径）"
            for item in clips
            if not str(item.get("source_path", "")).strip()
            or not Path(str(item.get("source_path", ""))).is_file()
        ]
        if missing:
            raise ValueError(f"粗剪时间线有 {len(missing)} 个素材文件不可用：{missing[0]}")
    elif not source_video or not source_video.is_file():
        raise ValueError("原视频不可用，无法生成粗剪预览")

    shared = config.get("shared", {})
    ffmpeg = _resolve_tool(str(shared.get("ffmpeg_bin", "ffmpeg")), app_root, "ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 FFmpeg，无法生成粗剪预览")

    has_narration = bool(narration_audio and narration_audio.exists())
    narration_input_index = len(clips) if uses_timeline_sources else 1
    export_config = config.get("export", {})
    preserve_original_audio = bool(export_config.get("preserve_original_audio", False)) and not uses_timeline_sources
    original_audio_mix_volume = min(
        1.0, max(0.0, float(export_config.get("original_audio_mix_volume", 0.22) or 0.22))
    )
    configured_width = int(export_config.get("width", 1080) or 1080)
    configured_height = int(export_config.get("height", 1920) or 1920)
    fps = int(export_config.get("fps", 30) or 30)
    cleanup_original_subtitles = bool(export_config.get("cleanup_original_subtitles", True)) and not uses_timeline_sources
    source_crop_ratio = min(1.0, max(0.6, float(export_config.get("source_crop_height_ratio", 0.82) or 0.82)))
    fit_mode = str(export_config.get("fit_mode", "original")).lower()
    crop_fill_percent = _normalize_crop_fill_percent(
        export_config.get("vertical_crop_fill_percent", 100)
    )
    width = int(source_width or configured_width) if fit_mode == "original" else configured_width
    height = int(source_height or configured_height) if fit_mode == "original" else configured_height
    cleanup_mode = str(export_config.get("original_subtitle_cleanup_mode", "none")).lower()
    if cleanup_mode not in {"none", "mask", "blur", "delogo"}:
        cleanup_mode = "none"
    if uses_timeline_sources:
        cleanup_mode = "none"
    if cleanup_mode == "delogo" and not _ffmpeg_supports_filter(ffmpeg, "delogo"):
        cleanup_mode = "blur"
    subtitle_background_enabled = bool(
        export_config.get("subtitle_background_enabled", False)
    )
    subtitle_background_mode = str(
        export_config.get("subtitle_background_mode", "mask")
    ).lower()
    if subtitle_background_mode not in {"mask", "blur", "delogo"}:
        subtitle_background_mode = "mask"
    if subtitle_background_mode == "delogo" and not _ffmpeg_supports_filter(ffmpeg, "delogo"):
        subtitle_background_mode = "blur"
    filters: list[str] = []
    concat_inputs: list[str] = []
    original_audio_inputs: list[str] = []
    canvas_scale_x = min(3.0, max(0.25, float(export_config.get("canvas_scale_x", 1.0) or 1.0)))
    canvas_scale_y = min(3.0, max(0.25, float(export_config.get("canvas_scale_y", 1.0) or 1.0)))
    for index, clip in enumerate(clips):
        start = float(clip.get("source_start", 0))
        end = float(clip.get("source_end", 0))
        if end - start < 0.05:
            continue
        input_index = index if uses_timeline_sources else 0
        video_input = f"[{input_index}:v]"
        audio_input = f"[{input_index}:a]"
        media_kind = str(clip.get("media_kind", "video")).lower()
        clip_duration = end - start
        trim_filter = (
            f"trim=duration={clip_duration:.3f}"
            if media_kind == "image"
            else f"trim=start={start:.3f}:end={end:.3f}"
        )
        clip_width = int(clip.get("width", 0) or source_width or width)
        clip_height = int(clip.get("height", 0) or source_height or height)
        source_aspect = max(0.01, float(clip_width) / max(1.0, float(clip_height)))
        canvas_video_width = max(2, round(width * canvas_scale_x))
        canvas_video_height = max(2, round((width / source_aspect) * canvas_scale_y))
        canvas_video_width -= canvas_video_width % 2
        canvas_video_height -= canvas_video_height % 2
        source_cleanup = f"crop=iw:ih*{source_crop_ratio:.3f}:0:0," if cleanup_original_subtitles else ""
        if fit_mode == "crop_stretch":
            fit_filter = (
                f"scale={canvas_video_width}:{canvas_video_height},"
                f"crop=w='min(iw\\,{width})':h='min(ih\\,{height})':"
                "x='(iw-ow)/2':y='(ih-oh)/2',"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
            )
            filters.append(
                f"{video_input}{trim_filter},"
                f"setpts=PTS-STARTPTS,fps={fps},{source_cleanup}{fit_filter},"
                f"setsar=1,format=yuv420p[v{index}]"
            )
            concat_inputs.append(f"[v{index}]")
            if preserve_original_audio:
                filters.append(
                    f"{audio_input}atrim=start={start:.3f}:end={end:.3f},"
                    f"asetpts=PTS-STARTPTS,aresample=48000[aorig{index}]"
                )
                original_audio_inputs.append(f"[aorig{index}]")
            continue
        if fit_mode in {"crop", "vertical_crop"} and crop_fill_percent < 100:
            blur_radius = max(
                4,
                min(60, int(export_config.get("vertical_background_blur_radius", 24) or 24)),
            )
            foreground_height = _crop_region_height(height, crop_fill_percent)
            filters.extend(
                [
                    f"{video_input}{trim_filter},"
                    f"setpts=PTS-STARTPTS,fps={fps},{source_cleanup}split=2[vbg{index}][vfg{index}]",
                    f"[vbg{index}]scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height},gblur=sigma={blur_radius}[vbgfit{index}]",
                    f"[vfg{index}]scale={width}:{foreground_height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{foreground_height}[vfgfit{index}]",
                    f"[vbgfit{index}][vfgfit{index}]overlay=(W-w)/2:(H-h)/2,"
                    f"setsar=1,format=yuv420p[v{index}]",
                ]
            )
            concat_inputs.append(f"[v{index}]")
            if preserve_original_audio:
                filters.append(
                    f"{audio_input}atrim=start={start:.3f}:end={end:.3f},"
                    f"asetpts=PTS-STARTPTS,aresample=48000[aorig{index}]"
                )
                original_audio_inputs.append(f"[aorig{index}]")
            continue
        if fit_mode == "vertical_blur":
            blur_radius = max(
                4,
                min(60, int(export_config.get("vertical_background_blur_radius", 24) or 24)),
            )
            filters.extend(
                [
                    f"{video_input}{trim_filter},"
                    f"setpts=PTS-STARTPTS,fps={fps},{source_cleanup}split=2[vbg{index}][vfg{index}]",
                    f"[vbg{index}]scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height},gblur=sigma={blur_radius}[vbgfit{index}]",
                    f"[vfg{index}]scale={width}:{height}:force_original_aspect_ratio=decrease[vfgfit{index}]",
                    f"[vbgfit{index}][vfgfit{index}]overlay=(W-w)/2:(H-h)/2,"
                    f"setsar=1,format=yuv420p[v{index}]",
                ]
            )
            concat_inputs.append(f"[v{index}]")
            if preserve_original_audio:
                filters.append(
                    f"{audio_input}atrim=start={start:.3f}:end={end:.3f},"
                    f"asetpts=PTS-STARTPTS,aresample=48000[aorig{index}]"
                )
                original_audio_inputs.append(f"[aorig{index}]")
            continue
        if fit_mode in {"crop", "vertical_crop"}:
            fit_filter = (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height}"
            )
        elif fit_mode == "contain" or uses_timeline_sources:
            fit_filter = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"
            )
        else:
            fit_filter = ""
        filters.append(
            f"{video_input}{trim_filter},"
            f"setpts=PTS-STARTPTS,fps={fps},"
            f"{source_cleanup}"
            f"{fit_filter + ',' if fit_filter else ''}setsar=1,format=yuv420p[v{index}]"
        )
        concat_inputs.append(f"[v{index}]")
        if preserve_original_audio:
            filters.append(
                f"{audio_input}atrim=start={start:.3f}:end={end:.3f},"
                f"asetpts=PTS-STARTPTS,aresample=48000[aorig{index}]"
            )
            original_audio_inputs.append(f"[aorig{index}]")

    valid_count = len(concat_inputs)
    if valid_count == 0:
        raise ValueError("粗剪时间线中的镜头时长无效")
    has_subtitles = bool(subtitle_srt and subtitle_srt.exists())
    use_subtitle_background_effect = bool(
        has_subtitles
        and subtitle_background_enabled
        and subtitle_background_mode in {"blur", "delogo"}
        and cleanup_mode == "none"
    )
    effect_mode = subtitle_background_mode if use_subtitle_background_effect else cleanup_mode
    needs_cleanup = effect_mode != "none"
    concat_video_output = "[vjoined]" if needs_cleanup else ("[vbase]" if has_subtitles else "[vout]")
    filters.append(
        "".join(concat_inputs)
        + f"concat=n={valid_count}:v=1:a=0"
        + concat_video_output
    )
    if preserve_original_audio:
        filters.append(
            "".join(original_audio_inputs)
            + f"concat=n={valid_count}:v=0:a=1[aoriginal]"
        )
    if needs_cleanup:
        prefix = "subtitle_background" if use_subtitle_background_effect else "original_subtitle_cleanup"
        cleanup_x_ratio = min(0.95, max(0.0, float(export_config.get(f"{prefix}_x", 0.08) or 0.08)))
        cleanup_y_ratio = min(0.95, max(0.0, float(export_config.get(f"{prefix}_y", 0.82) or 0.82)))
        cleanup_w_ratio = min(1.0, max(0.02, float(export_config.get(f"{prefix}_width", 0.84) or 0.84)))
        cleanup_h_ratio = min(0.4, max(0.02, float(export_config.get(f"{prefix}_height", 0.14) or 0.14)))
        cleanup_opacity = min(1.0, max(0.0, float(export_config.get(f"{prefix}_opacity", 0.78) or 0.78)))
        blur_radius = max(1, min(40, int(export_config.get(f"{prefix}_blur_radius", 12) or 12)))
        blur_power = max(1, min(4, int(export_config.get(f"{prefix}_blur_power", 2) or 2)))
        region_padding = 0 if use_subtitle_background_effect else max(0, min(80, int(export_config.get("original_subtitle_region_padding", 4) or 4)))
        feather = 0 if use_subtitle_background_effect else max(0, min(60, int(export_config.get("original_subtitle_feather", 12) or 12)))
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
        if effect_mode == "mask":
            filters.append(
                f"[vjoined]drawbox=x={cleanup_x}:y={cleanup_y}:w={cleanup_w}:h={cleanup_h}:"
                f"color=black@{cleanup_opacity:.2f}:t=fill{cleanup_output}"
            )
        elif effect_mode == "delogo":
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
                    f"gblur=sigma={blur_radius}:steps={blur_power}[cleanblur]",
                    f"[cleanbase][cleanblur]overlay=x={cleanup_x}:y={cleanup_y}{cleanup_output}",
                ]
            )
    if has_subtitles:
        internal_dir = output_video.parent / "_internal"
        internal_dir.mkdir(parents=True, exist_ok=True)
        ass_path = internal_dir / f"{output_video.stem}_subtitles.ass"
        _build_shorts_ass(subtitle_srt, ass_path, width, height, export_config)
        filters.append(f"[vbase]subtitles='{_escape_subtitle_path(ass_path)}'[vout]")
    if has_narration:
        filters.append(
            f"[{narration_input_index}:a]atrim=start=0:end={total_duration:.3f},"
            "asetpts=PTS-STARTPTS,aresample=48000[anarration]"
        )
    if has_narration and preserve_original_audio:
        filters.extend(
            [
                f"[aoriginal]volume={original_audio_mix_volume:.3f}[abackground]",
                "[abackground][anarration]amix=inputs=2:duration=first:"
                "dropout_transition=0:normalize=0[aout]",
            ]
        )
    elif has_narration:
        filters.append("[anarration]anull[aout]")
    elif preserve_original_audio:
        filters.append("[aoriginal]anull[aout]")

    output_video.parent.mkdir(parents=True, exist_ok=True)
    internal_dir = output_video.parent / "_internal"
    internal_dir.mkdir(parents=True, exist_ok=True)
    filter_log = internal_dir / f"{output_video.stem}_filters.txt"
    ffmpeg_log = internal_dir / f"{output_video.stem}_ffmpeg.log"
    filter_log.write_text(";\n".join(filters) + "\n", encoding="utf-8")
    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if uses_timeline_sources:
        for clip in clips:
            path = str(clip.get("source_path", ""))
            if str(clip.get("media_kind", "video")).lower() == "image":
                duration = max(
                    0.05,
                    float(clip.get("source_end", 0)) - float(clip.get("source_start", 0)),
                )
                command.extend(["-loop", "1", "-framerate", str(fps), "-t", f"{duration:.3f}", "-i", path])
            else:
                command.extend(["-i", path])
    else:
        command.extend(["-i", str(source_video)])
    if has_narration:
        command.extend(["-i", str(narration_audio)])
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
        "-map",
        "[vout]",
        ]
    )
    has_output_audio = has_narration or preserve_original_audio
    if has_output_audio:
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
    if has_output_audio:
        command.extend(["-c:a", "aac", "-b:a", str(export_config.get("audio_bitrate", "192k"))])
    else:
        command.append("-an")
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
    ffmpeg_log.write_text(
        "COMMAND\n"
        + subprocess.list2cmdline(command)
        + "\n\nSTDERR\n"
        + (stderr or "(empty)")
        + f"\n\nEXIT_CODE\n{return_code}\n",
        encoding="utf-8",
    )
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
        "has_audio": has_output_audio,
        "audio_mode": (
            "narration_with_original"
            if has_narration and preserve_original_audio
            else "narration_only"
            if has_narration
            else "original_clips"
            if preserve_original_audio
            else "silent_subtitle_test"
        ),
        "width": width,
        "height": height,
        "fit_mode": fit_mode,
        "subtitles_burned": has_subtitles,
        "original_subtitles_cleaned": cleanup_original_subtitles or cleanup_mode != "none",
        "original_subtitle_cleanup_mode": cleanup_mode,
        "subtitle_background_mode": (
            subtitle_background_mode if subtitle_background_enabled else "none"
        ),
        "ffmpeg_log": str(ffmpeg_log),
        "filter_log": str(filter_log),
    }


def _normalize_crop_fill_percent(value: object) -> int:
    try:
        numeric = int(round(float(value)))
    except (TypeError, ValueError):
        numeric = 100
    return min((50, 60, 70, 80, 90, 100), key=lambda option: abs(option - numeric))


def _crop_region_height(canvas_height: int, fill_percent: object) -> int:
    percent = _normalize_crop_fill_percent(fill_percent)
    height = max(2, int(round(max(2, canvas_height) * percent / 100.0)))
    return height if height % 2 == 0 else height - 1


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
        duration_ms = max(1, round((_srt_seconds(right) - _srt_seconds(left)) * 1000))
        animation_tag = _subtitle_animation_tag(
            str(config.get("subtitle_animation", "fade")), duration_ms
        )
        events.append(
            f"Dialogue: 0,{_ass_time(left)},{_ass_time(right)},Default,,0,0,0,,"
            f"{animation_tag}{caption}"
        )
    font_name = str(config.get("subtitle_font", "Arial"))
    font_size = int(config.get("subtitle_font_size", 48) or 48)
    margin_v = int(config.get("subtitle_margin_v", 72) or 72)
    margin_h = int(config.get("subtitle_margin_h", 72) or 72)
    bold = -1 if bool(config.get("subtitle_bold", True)) else 0
    italic = -1 if bool(config.get("subtitle_italic", False)) else 0
    spacing = min(20.0, max(-5.0, float(config.get("subtitle_spacing", 0) or 0)))
    text_color = _rgba_to_ass(str(config.get("subtitle_color", "#FFFFFFFF")))
    configured_outline_color = _rgba_to_ass(
        str(config.get("subtitle_outline_color", "#000000FF"))
    )
    background_mode = str(config.get("subtitle_background_mode", "mask")).lower()
    background_enabled = bool(config.get("subtitle_background_enabled", True)) and background_mode == "mask"
    background_opacity = min(0.95, max(0.0, float(config.get("subtitle_background_opacity", 0.62) or 0.62)))
    outline_width = int(config.get("subtitle_outline_width", 3) or 3)
    box_padding = int(config.get("subtitle_box_padding", 12) or 12)
    border_style = 3 if background_enabled else 1
    border_size = box_padding if background_enabled else outline_width
    alpha = round((1.0 - background_opacity) * 255) if background_enabled else 0
    border_color = f"&H{alpha:02X}000000" if background_enabled else configured_outline_color
    shadow = 0 if background_enabled else max(0, min(10, int(config.get("subtitle_shadow", 1) or 0)))
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{text_color},{text_color},{border_color},{border_color},{bold},{italic},0,0,100,100,{spacing:g},0,{border_style},{border_size},{shadow},2,{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")


def _ass_time(value: str) -> str:
    hours, minutes, rest = value.replace(",", ".").split(":")
    seconds = float(rest)
    return f"{int(hours)}:{int(minutes):02d}:{seconds:05.2f}"


def _srt_seconds(value: str) -> float:
    hours, minutes, rest = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def _subtitle_animation_tag(animation: str, duration_ms: int) -> str:
    if animation == "none":
        return ""
    if animation == "pop":
        enter = max(70, min(170, duration_ms // 3))
        fade = max(40, min(90, duration_ms // 6))
        return rf"{{\fscx88\fscy88\t(0,{enter},\fscx100\fscy100)\fad({fade},{fade})}}"
    fade = max(40, min(140, duration_ms // 4))
    return rf"{{\fad({fade},{fade})}}"


def _rgba_to_ass(value: str) -> str:
    cleaned = value.strip().lstrip("#")
    if len(cleaned) == 6:
        cleaned += "FF"
    if not re.fullmatch(r"[0-9A-Fa-f]{8}", cleaned):
        cleaned = "FFFFFFFF"
    red, green, blue, rgba_alpha = (
        cleaned[0:2], cleaned[2:4], cleaned[4:6], cleaned[6:8]
    )
    ass_alpha = 255 - int(rgba_alpha, 16)
    return f"&H{ass_alpha:02X}{blue}{green}{red}".upper()
