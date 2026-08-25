from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.export_service import _build_shorts_ass


class ExportSubtitleStyleTests(unittest.TestCase):
    def test_advanced_style_and_pop_animation_are_written_to_ass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            srt = root / "voice.srt"
            ass = root / "voice.ass"
            srt.write_text(
                "1\n00:00:00,000 --> 00:00:02,000\nScience in motion.\n",
                encoding="utf-8",
            )
            _build_shorts_ass(
                srt,
                ass,
                1920,
                1080,
                {
                    "subtitle_color": "#FFCC00FF",
                    "subtitle_outline_color": "#112233FF",
                    "subtitle_italic": True,
                    "subtitle_spacing": 2.5,
                    "subtitle_shadow": 3,
                    "subtitle_animation": "pop",
                    "subtitle_background_enabled": False,
                },
            )
            content = ass.read_text(encoding="utf-8-sig")
            self.assertIn("&H0000CCFF", content)
            self.assertIn("&H00332211", content)
            self.assertIn(",-1,-1,0,0,100,100,2.5,", content)
            self.assertIn(",1,3,3,2,", content)
            self.assertIn(r"\fscx88\fscy88", content)

    def test_animation_can_be_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            srt = root / "voice.srt"
            ass = root / "voice.ass"
            srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nStill.\n", encoding="utf-8")
            _build_shorts_ass(srt, ass, 1280, 720, {"subtitle_animation": "none"})
            content = ass.read_text(encoding="utf-8-sig")
            self.assertNotIn(r"\fad", content)
            self.assertNotIn(r"\fscx", content)


if __name__ == "__main__":
    unittest.main()
