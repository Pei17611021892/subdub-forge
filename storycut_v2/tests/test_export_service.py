from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.export_service import (
    _build_shorts_ass,
    _crop_region_height,
    _normalize_crop_fill_percent,
    render_rough_preview,
)


class ExportSubtitleStyleTests(unittest.TestCase):
    def test_vertical_crop_fill_percent_is_normalized_and_even(self) -> None:
        self.assertEqual(_normalize_crop_fill_percent(87), 90)
        self.assertEqual(_normalize_crop_fill_percent("bad"), 100)
        self.assertEqual(_crop_region_height(1920, 70), 1344)
        self.assertEqual(_crop_region_height(1919, 50) % 2, 0)

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

    def test_only_black_background_mode_uses_ass_text_box(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            srt = root / "voice.srt"
            srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nTest.\n", encoding="utf-8")
            mask_ass = root / "mask.ass"
            blur_ass = root / "blur.ass"

            _build_shorts_ass(
                srt,
                mask_ass,
                1080,
                1920,
                {
                    "subtitle_background_enabled": True,
                    "subtitle_background_mode": "mask",
                    "subtitle_box_padding": 12,
                },
            )
            _build_shorts_ass(
                srt,
                blur_ass,
                1080,
                1920,
                {
                    "subtitle_background_enabled": True,
                    "subtitle_background_mode": "blur",
                    "subtitle_outline_width": 3,
                },
            )

            self.assertIn(",3,12,0,2,", mask_ass.read_text(encoding="utf-8-sig"))
            self.assertIn(",1,3,1,2,", blur_ass.read_text(encoding="utf-8-sig"))

    def test_crop_stretch_builds_centered_scale_crop_and_pad_filter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            rough_cut = root / "rough.json"
            rough_cut.write_text(
                json.dumps(
                    {
                        "duration_sec": 1.0,
                        "clips": [{"source_start": 0.0, "source_end": 1.0}],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "preview.mp4"
            output.write_bytes(b"rendered")
            process = MagicMock()
            process.stdout = []
            process.stderr.read.return_value = ""
            process.wait.return_value = 0

            with patch("src.export_service._resolve_tool", return_value="ffmpeg"), patch(
                "src.export_service.subprocess.Popen", return_value=process
            ):
                result = render_rough_preview(
                    source,
                    rough_cut,
                    output,
                    None,
                    None,
                    1920,
                    1080,
                    {
                        "shared": {},
                        "export": {
                            "fit_mode": "crop_stretch",
                            "width": 1080,
                            "height": 1920,
                            "canvas_scale_x": 1.2,
                            "canvas_scale_y": 0.8,
                        },
                    },
                    root,
                    lambda _value, _status: None,
                )

            filters = Path(result["filter_log"]).read_text(encoding="utf-8")
            self.assertIn("scale=1296:486", filters)
            self.assertIn("crop=w='min(iw\\,1080)'", filters)
            self.assertIn("pad=1080:1920", filters)

    def test_timeline_sources_build_video_and_looped_image_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            video = root / "one.mp4"
            image = root / "still.jpg"
            video.write_bytes(b"video")
            image.write_bytes(b"image")
            rough_cut = root / "rough.json"
            rough_cut.write_text(
                json.dumps(
                    {
                        "duration_sec": 3.0,
                        "clips": [
                            {
                                "source_path": str(video),
                                "media_kind": "video",
                                "source_start": 1.0,
                                "source_end": 2.5,
                                "width": 1280,
                                "height": 720,
                            },
                            {
                                "source_path": str(image),
                                "media_kind": "image",
                                "source_start": 0.0,
                                "source_end": 1.5,
                                "width": 800,
                                "height": 1200,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            output = root / "preview.mp4"
            output.write_bytes(b"rendered")
            process = MagicMock()
            process.stdout = []
            process.stderr.read.return_value = ""
            process.wait.return_value = 0

            with patch("src.export_service._resolve_tool", return_value="ffmpeg"), patch(
                "src.export_service.subprocess.Popen", return_value=process
            ) as popen:
                result = render_rough_preview(
                    None,
                    rough_cut,
                    output,
                    None,
                    None,
                    0,
                    0,
                    {"shared": {}, "export": {"fit_mode": "vertical_blur", "width": 1080, "height": 1920}},
                    root,
                    lambda _value, _status: None,
                )

            command = popen.call_args.args[0]
            filters = Path(result["filter_log"]).read_text(encoding="utf-8")
            self.assertIn(str(video), command)
            self.assertIn(str(image), command)
            self.assertIn("-loop", command)
            self.assertIn("[0:v]trim=start=1.000:end=2.500", filters)
            self.assertIn("[1:v]trim=duration=1.500", filters)
            self.assertFalse(result["has_audio"])


if __name__ == "__main__":
    unittest.main()
