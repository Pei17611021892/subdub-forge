from __future__ import annotations

import re
from pathlib import Path


def _seconds(stamp: str) -> float:
    h, minute, rest = stamp.replace(",", ".").split(":")
    return int(h) * 3600 + int(minute) * 60 + float(rest)


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    h, centiseconds = divmod(centiseconds, 360000)
    minute, centiseconds = divmod(centiseconds, 6000)
    sec, cs = divmod(centiseconds, 100)
    return f"{h}:{minute:02d}:{sec:02d}.{cs:02d}"


def _effect_tag(animation: str, duration_ms: int) -> str:
    if animation == "fade":
        fade = max(40, min(140, duration_ms // 4))
        return rf"{{\fad({fade},{fade})}}"
    if animation == "pop":
        enter = max(70, min(170, duration_ms // 3))
        fade = max(40, min(90, duration_ms // 6))
        return rf"{{\fscx88\fscy88\t(0,{enter},\fscx100\fscy100)\fad({fade},{fade})}}"
    return ""


def build_effect_ass(srt_path: Path, ass_path: Path, animation: str) -> Path:
    """Convert SRT to ASS and add deliberately short, per-caption animation tags."""
    text = srt_path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    events: list[str] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        timing = re.match(r"\s*(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)", lines[1])
        if not timing:
            continue
        start, end = map(_seconds, timing.groups())
        duration_ms = max(1, round((end - start) * 1000))
        caption = r"\N".join(lines[2:]).replace("{", r"\{").replace("}", r"\}")
        caption = _effect_tag(animation, duration_ms) + caption
        events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{caption}")

    header = """[Script Info]
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,30,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,3,0,2,10,10,36,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8-sig")
    return ass_path
