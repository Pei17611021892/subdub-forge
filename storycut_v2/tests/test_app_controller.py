from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from src.app_controller import AppController


class AppControllerProjectNameTests(unittest.TestCase):
    def test_daily_project_names_increment_without_using_video_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            projects_dir = Path(temporary_dir)
            now = datetime(2026, 8, 17, 12, 0, 0)

            self.assertEqual(
                AppController._available_project_name(projects_dir, now),
                "v2-0817",
            )
            (projects_dir / "v2-0817").mkdir()
            self.assertEqual(
                AppController._available_project_name(projects_dir, now),
                "v2-0817-1",
            )
            (projects_dir / "v2-0817-1").mkdir()
            self.assertEqual(
                AppController._available_project_name(projects_dir, now),
                "v2-0817-2",
            )


if __name__ == "__main__":
    unittest.main()
