from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def cleanup_mode(comp: dict[str, Any]) -> str:
    cleanup = comp.get("subtitle_cleanup", {})
    mode = str(cleanup.get("mode", "")).lower()
    if mode in {"none", "mask", "blur", "delogo"}:
        return mode
    return "delogo" if comp.get("cover_original_subtitle", True) else "none"


def _stamp_seconds(value: str) -> float:
    h, minute, rest = value.replace(",", ".").split(":")
    return int(h) * 3600 + int(minute) * 60 + float(rest)


def subtitle_enable_expression(srt_path: Path | None, enabled: bool) -> str:
    if not enabled or not srt_path or not srt_path.exists():
        return "1"
    text = srt_path.read_text(encoding="utf-8-sig", errors="replace")
    ranges: list[tuple[float, float]] = []
    # Very short gaps are visually distracting and hundreds of between() calls can
    # exhaust FFmpeg's expression parser. Bridge gaps up to 0.65 s by design.
    merge_gap = 0.65
    for start, end in re.findall(r"(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)", text):
        a, b = _stamp_seconds(start), _stamp_seconds(end)
        if ranges and a <= ranges[-1][1] + merge_gap:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], b))
        else:
            ranges.append((a, b))

    # Keep the expression evaluator safely below its practical complexity limit.
    # If necessary, repeatedly bridge the smallest remaining visual gap.
    while len(ranges) > 32:
        smallest = min(range(len(ranges) - 1), key=lambda i: ranges[i + 1][0] - ranges[i][1])
        ranges[smallest:smallest + 2] = [(ranges[smallest][0], ranges[smallest + 1][1])]
    expression = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in ranges)
    return expression if expression and len(expression) <= 2400 else "1"


def build_cleanup_graph(
    comp: dict[str, Any],
    subtitle_filter: str,
    timing_srt: Path | None = None,
    dynamic_timing: bool = True,
    input_label: str = "0:v",
    output_label: str = "v",
    video_size: tuple[int, int] | None = None,
) -> str:
    mode = cleanup_mode(comp)
    cleanup = comp.get("subtitle_cleanup", {})
    cover = comp.get("cover", {})
    x = str(cover.get("x", "0"))
    y = str(cover.get("y", "ih*0.74"))
    w = str(cover.get("w", "iw"))
    h = str(cover.get("h", "ih*0.26"))
    # All cleanup backgrounds stay continuous. Timeline toggles can expose one or two
    # source subtitle frames at retimed boundaries and create visible flashing.
    enable = "1"

    def pixels(value: str, axis: str, total: int) -> int:
        value = value.strip().lower().replace(" ", "")
        if value == axis:
            return total
        match = re.fullmatch(rf"{axis}\*([0-9.]+)", value)
        if match:
            return round(total * float(match.group(1)))
        try:
            return round(float(value))
        except ValueError:
            return 0

    region: tuple[int, int, int, int] | None = None
    if video_size:
        vw, vh = video_size
        # Expanding the blurred crop softens the visible boundary without relying on
        # geq/alphamerge, which is unavailable or unstable in some Windows FFmpeg builds.
        padding = max(0, min(120, int(cleanup.get("region_padding", 0)) + int(cleanup.get("feather", 0))))
        px = max(0, pixels(x, "iw", vw) - padding)
        py = max(0, pixels(y, "ih", vh) - padding)
        pw = min(vw - px, pixels(w, "iw", vw) + padding * 2)
        ph = min(vh - py, pixels(h, "ih", vh) + padding * 2)
        region = (px, py, max(2, pw), max(2, ph))
        x, y, w, h = map(str, region)

    if mode == "none":
        return f"[{input_label}]{subtitle_filter}[{output_label}]"
    if mode == "mask":
        color = str(cover.get("color", "black@0.92"))
        return f"[{input_label}]drawbox=x={x}:y={y}:w={w}:h={h}:color={color}:t=fill:enable='{enable}',{subtitle_filter}[{output_label}]"
    if mode == "delogo":
        # Delogo interpolates from surrounding pixels and is much cheaper than AI inpainting.
        if region and video_size:
            vw, vh = video_size
            px, py, pw, ph = region
            px, py = max(2, px), max(2, py)
            pw, ph = min(pw, vw - px - 2), min(ph, vh - py - 2)
            x, y, w, h = map(str, (px, py, max(2, pw), max(2, ph)))
        return f"[{input_label}]delogo=x={x}:y={y}:w={w}:h={h}:show=0:enable='{enable}',{subtitle_filter}[{output_label}]"

    radius = max(1, min(40, int(cleanup.get("blur_radius", 12))))
    power = max(1, min(4, int(cleanup.get("blur_power", 2))))
    # Crop, blur only the selected subtitle region, then place it back over the original frame.
    return (
        f"[{input_label}]split=2[clean_base][clean_region];"
        f"[clean_region]crop=w={w}:h={h}:x={x}:y={y},boxblur=luma_radius={radius}:luma_power={power}[clean_blur];"
        f"[clean_base][clean_blur]overlay=x={x}:y={y}:enable='{enable}'[cleaned];"
        f"[cleaned]{subtitle_filter}[{output_label}]"
    )
