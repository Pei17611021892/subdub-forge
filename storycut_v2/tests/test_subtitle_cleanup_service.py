from __future__ import annotations

import unittest

from src.subtitle_cleanup_service import build_cleanup_filter


class SubtitleCleanupFilterTests(unittest.TestCase):
    def test_mask_uses_source_frame_coordinates(self) -> None:
        result = build_cleanup_filter(
            {
                "cleanupMode": "mask",
                "cleanupX": 0.1,
                "cleanupY": 0.8,
                "cleanupWidth": 0.8,
                "cleanupHeight": 0.1,
                "regionPadding": 0,
                "feather": 0,
            },
            1920,
            1080,
        )
        self.assertIn("x=192:y=864:w=1536:h=108", result)

    def test_blur_and_delogo_build_real_filters(self) -> None:
        blur = build_cleanup_filter({"cleanupMode": "blur"}, 1280, 720)
        delogo = build_cleanup_filter({"cleanupMode": "delogo"}, 1280, 720)
        self.assertIn("gblur=", blur)
        self.assertIn("overlay=", blur)
        self.assertTrue(delogo.startswith("delogo="))


if __name__ == "__main__":
    unittest.main()
