from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from .media_service import _resolve_tool
from .voice_service import SHORTS_MAX_DURATION_SEC, parse_srt_timings


def inspect_project_for_export(
    project_file: Path,
    source_video: Path | None,
    narration_audio: Path | None,
    narration_duration_sec: float,
    synced_srt: Path | None,
    media: dict[str, Any],
) -> dict[str, Any]:
    """Run local, deterministic checks before spending time on a final render."""
    checks: list[dict[str, str]] = []

    def add(level: str, title: str, detail: str) -> None:
        checks.append({"level": level, "title": title, "detail": detail})

    if not project_file.exists():
        add("error", "项目文件缺失", "找不到当前项目的 project.json。")
        return _report(checks)

    if not source_video or not source_video.exists():
        add("error", "原视频不可用", "请重新关联创建项目时使用的原视频。")
    else:
        expected_size = int(media.get("file_size", 0) or 0)
        if expected_size and source_video.stat().st_size != expected_size:
            add("error", "原视频可能已变化", "当前文件大小与理解原片时记录的数据不一致。")
        else:
            add("pass", "原视频", "文件存在，未发现被替换的迹象。")

    project_dir = project_file.parent
    required = {
        "故事稿": project_dir / "script" / "story.json",
        "镜头匹配": project_dir / "timeline" / "matches.json",
        "粗剪时间线": project_dir / "timeline" / "rough_cut.json",
    }
    for label, path in required.items():
        if path.exists():
            add("pass", label, "文件已准备。")
        else:
            add("error", f"{label}缺失", f"请先完成对应步骤，缺少 {path.name}。")

    rough_cut_file = required["粗剪时间线"]
    if rough_cut_file.exists():
        try:
            timeline = json.loads(rough_cut_file.read_text(encoding="utf-8"))
            _inspect_timeline(timeline, float(media.get("duration_sec", 0) or 0), add)
        except (OSError, ValueError, TypeError) as exc:
            add("error", "粗剪时间线损坏", str(exc))

    if not narration_audio or not narration_audio.exists():
        add("error", "英文配音缺失", "请导入 GPT-SoVITS 生成的英文配音。")
    elif narration_duration_sec <= 0:
        add("error", "配音时长无效", "无法读取英文配音的真实时长。")
    elif narration_duration_sec >= SHORTS_MAX_DURATION_SEC:
        add(
            "error",
            "配音超过 Shorts 上限",
            f"当前 {narration_duration_sec:.1f} 秒，必须低于 {SHORTS_MAX_DURATION_SEC:.0f} 秒。",
        )
    else:
        add("pass", "英文配音", f"真实时长 {narration_duration_sec:.1f} 秒，符合 Shorts 安全线。")

    if synced_srt and synced_srt.exists():
        try:
            segments = parse_srt_timings(synced_srt.read_text(encoding="utf-8-sig"))
            _inspect_subtitles(segments, narration_duration_sec, add)
        except (OSError, ValueError, TypeError) as exc:
            add("error", "同步字幕无法读取", str(exc))
    else:
        add(
            "warning",
            "未导入真实同步 SRT",
            "仍可按句子比例生成字幕，但字幕切换点可能与 GPT-SoVITS 实际发音不完全一致。",
        )

    return _report(checks)


def inspect_rendered_video(
    output_video: Path,
    expected_duration_sec: float,
    expected_width: int,
    expected_height: int,
    expected_audio: bool | None,
    config: dict[str, Any],
    app_root: Path,
) -> dict[str, Any]:
    """Probe the rendered file so a successful FFmpeg exit is not treated as sufficient."""
    checks: list[dict[str, str]] = []

    def add(level: str, title: str, detail: str) -> None:
        checks.append({"level": level, "title": title, "detail": detail})

    if not output_video.exists() or output_video.stat().st_size <= 0:
        add("error", "输出文件缺失", "FFmpeg 没有留下可读取的成片文件。")
        return _report(checks)
    size_mb = output_video.stat().st_size / (1024 * 1024)
    add("pass", "输出文件", f"文件已生成，大小 {size_mb:.1f} MB。")

    shared = config.get("shared", {})
    ffprobe = _resolve_tool(str(shared.get("ffprobe_bin", "ffprobe")), app_root, "ffprobe")
    if not ffprobe:
        add("warning", "无法实测输出编码", "未找到 ffprobe；已确认文件存在，但无法检查音视频流。")
        return _report(checks)
    try:
        process = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=index,codec_type,codec_name,width,height,pix_fmt",
                "-of",
                "json",
                str(output_video),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if process.returncode != 0:
            raise RuntimeError(process.stderr.strip() or f"ffprobe 退出码 {process.returncode}")
        probe = json.loads(process.stdout)
        _inspect_render_probe(
            probe,
            expected_duration_sec,
            expected_width,
            expected_height,
            expected_audio,
            add,
        )
    except (OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        add("error", "输出文件无法读取", f"ffprobe 检查失败：{exc}")
    return _report(checks)


def combine_quality_reports(*reports: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    for report in reports:
        for item in report.get("checks", []) if isinstance(report, dict) else []:
            if isinstance(item, dict):
                checks.append(
                    {
                        "level": str(item.get("level", "warning")),
                        "title": str(item.get("title", "检查项")),
                        "detail": str(item.get("detail", "")),
                    }
                )
    return _report(checks)


def _inspect_render_probe(
    probe: dict[str, Any],
    expected_duration: float,
    expected_width: int,
    expected_height: int,
    expected_audio: bool | None,
    add,
) -> None:
    streams = [dict(item) for item in probe.get("streams", []) if isinstance(item, dict)]
    video_streams = [item for item in streams if item.get("codec_type") == "video"]
    audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
    if not video_streams:
        add("error", "输出缺少视频流", "生成的文件中没有可播放的视频轨道。")
        return

    video = video_streams[0]
    width = int(video.get("width", 0) or 0)
    height = int(video.get("height", 0) or 0)
    codec = str(video.get("codec_name", "") or "未知")
    pix_fmt = str(video.get("pix_fmt", "") or "未知")
    if expected_width and expected_height and (width, height) != (expected_width, expected_height):
        add(
            "error",
            "输出分辨率异常",
            f"实际为 {width}×{height}，预期为 {expected_width}×{expected_height}。",
        )
    else:
        add("pass", "视频画面", f"{width}×{height}，编码 {codec}，像素格式 {pix_fmt}。")
    if codec != "h264":
        add("warning", "视频兼容性", f"当前编码为 {codec}；H.264 对 Shorts 和手机端兼容性更稳。")
    if pix_fmt not in {"yuv420p", "yuvj420p"}:
        add("warning", "像素格式兼容性", f"当前为 {pix_fmt}，部分手机播放器可能不兼容。")

    try:
        duration = float(probe.get("format", {}).get("duration", 0) or 0)
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 0:
        add("error", "输出时长无效", "无法读取成片时长。")
    elif duration >= SHORTS_MAX_DURATION_SEC:
        add("error", "成片超过 Shorts 上限", f"实际成片为 {duration:.2f} 秒。")
    else:
        difference = abs(duration - max(0.0, expected_duration))
        if expected_duration > 0 and difference > 2.0:
            add(
                "error",
                "成片时长偏差过大",
                f"实际 {duration:.2f} 秒，时间线 {expected_duration:.2f} 秒，相差 {difference:.2f} 秒。",
            )
        elif expected_duration > 0 and difference > 0.75:
            add(
                "warning",
                "成片时长存在轻微偏差",
                f"实际 {duration:.2f} 秒，时间线 {expected_duration:.2f} 秒。",
            )
        else:
            add("pass", "成片时长", f"实际 {duration:.2f} 秒，处于 Shorts 安全线内。")

    if expected_audio is True and not audio_streams:
        add("error", "输出缺少声音", "本次应包含英文配音，但成片中没有音频轨道。")
    elif expected_audio is False and audio_streams:
        add("warning", "仅字幕测试包含声音", "本次原本预期静音，但输出中存在音频轨道。")
    elif audio_streams:
        audio_codec = str(audio_streams[0].get("codec_name", "") or "未知")
        level = "pass" if audio_codec == "aac" else "warning"
        add(level, "音频轨道", f"音频存在，编码为 {audio_codec}。")
    elif expected_audio is None:
        add("warning", "输出没有音频", "这是仅字幕测试时的正常情况；正式成片应包含英文配音。")
    else:
        add("pass", "静音测试", "未发现音频轨道，符合仅字幕测试设置。")


def _inspect_timeline(timeline: dict[str, Any], source_duration: float, add) -> None:
    duration = float(timeline.get("duration_sec", 0) or 0)
    clips = [dict(item) for item in timeline.get("clips", []) if isinstance(item, dict)]
    narration = [dict(item) for item in timeline.get("narration", []) if isinstance(item, dict)]
    if duration <= 0 or not clips:
        add("error", "粗剪时间线为空", "没有可导出的镜头。")
        return
    if duration >= SHORTS_MAX_DURATION_SEC:
        add("error", "时间线超过 Shorts 上限", f"粗剪总时长为 {duration:.1f} 秒。")
    else:
        add("pass", "粗剪总时长", f"{duration:.1f} 秒，共 {len(clips)} 个镜头。")

    uncovered = [item for item in narration if not bool(item.get("covered", False))]
    if uncovered or not bool(timeline.get("all_narration_covered", False)):
        add("error", "存在未覆盖解说", f"有 {max(1, len(uncovered))} 句解说没有足够镜头。")
    else:
        add("pass", "解说镜头覆盖", f"{len(narration)} 句解说均有对应画面。")

    ordered = sorted(clips, key=lambda item: float(item.get("output_start", 0) or 0))
    gaps = 0
    overlaps = 0
    invalid_source = 0
    very_short = 0
    previous_end = 0.0
    signatures: list[tuple[int, int, int]] = []
    for clip in ordered:
        output_start = float(clip.get("output_start", 0) or 0)
        output_end = float(clip.get("output_end", 0) or 0)
        source_start = float(clip.get("source_start", 0) or 0)
        source_end = float(clip.get("source_end", 0) or 0)
        if output_start - previous_end > 0.08:
            gaps += 1
        if previous_end - output_start > 0.08:
            overlaps += 1
        previous_end = max(previous_end, output_end)
        if source_end <= source_start or source_start < 0 or (source_duration and source_end > source_duration + 0.1):
            invalid_source += 1
        if output_end - output_start < 0.25:
            very_short += 1
        signatures.append((int(clip.get("event_id", 0) or 0), round(source_start * 10), round(source_end * 10)))
    if gaps or overlaps:
        add("error", "镜头时间线不连续", f"检测到 {gaps} 处空隙、{overlaps} 处重叠。")
    if invalid_source:
        add("error", "镜头源时间无效", f"有 {invalid_source} 个镜头超出原视频或起止时间错误。")
    if very_short:
        add("warning", "存在闪切镜头", f"有 {very_short} 个镜头短于 0.25 秒，建议检查观看感受。")
    repeated = sum(count - 1 for count in Counter(signatures).values() if count > 1)
    adjacent_replays = sum(
        signatures[index] == signatures[index - 1] for index in range(1, len(signatures))
    )
    if adjacent_replays:
        add(
            "warning",
            "相邻镜头重复播放",
            f"有 {adjacent_replays} 处连续解说重复使用完全相同的原片区间。"
            "这可能是自动匹配的正常复用；请先看预览，只有画面跳回感明显时才需要在高级调整中替换镜头。",
        )
    elif repeated:
        add(
            "info",
            "镜头复用说明",
            f"有 {repeated} 个非相邻镜头复用了相同原片区间。多个文案共用合适画面属于正常情况，通常无需手动处理。",
        )
    else:
        add("pass", "镜头复用", "没有发现完全相同的原片区间被重复播放。")
    if not gaps and not overlaps and not invalid_source:
        add("pass", "镜头时间线", "起止范围连续且都在原视频范围内。")


def _inspect_subtitles(segments: list[dict[str, Any]], audio_duration: float, add) -> None:
    if not segments:
        add("error", "同步字幕为空", "SRT 中没有有效字幕。")
        return
    overlap_count = 0
    invalid_count = 0
    empty_count = 0
    previous_end = 0.0
    for item in segments:
        start = float(item.get("start", 0) or 0)
        end = float(item.get("end", 0) or 0)
        if start < previous_end - 0.03:
            overlap_count += 1
        if start < 0 or end <= start:
            invalid_count += 1
        if not str(item.get("text", "")).strip():
            empty_count += 1
        previous_end = max(previous_end, end)
    if invalid_count or overlap_count or empty_count:
        add(
            "error",
            "同步字幕时间轴异常",
            f"无效时间 {invalid_count} 条、相互重叠 {overlap_count} 条、空字幕 {empty_count} 条。",
        )
    else:
        add("pass", "同步字幕", f"{len(segments)} 条字幕，时间顺序正常。")
    last_end = max(float(item.get("end", 0) or 0) for item in segments)
    if audio_duration > 0 and abs(last_end - audio_duration) > 2.0:
        add(
            "warning",
            "字幕与配音尾部差距较大",
            f"字幕结束于 {last_end:.1f} 秒，配音为 {audio_duration:.1f} 秒，相差 {abs(last_end - audio_duration):.1f} 秒。",
        )


def _report(checks: list[dict[str, str]]) -> dict[str, Any]:
    errors = sum(item["level"] == "error" for item in checks)
    warnings = sum(item["level"] == "warning" for item in checks)
    passes = sum(item["level"] == "pass" for item in checks)
    infos = sum(item["level"] == "info" for item in checks)
    return {
        "passed": errors == 0,
        "error_count": errors,
        "warning_count": warnings,
        "pass_count": passes,
        "info_count": infos,
        "checks": checks,
    }
