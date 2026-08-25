from __future__ import annotations

import tempfile
import unittest
import json
import time
from threading import Event
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication

from src.app_controller import AppController


class AppControllerProjectNameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QCoreApplication.instance() or QCoreApplication([])

    def _controller(self, root: Path) -> AppController:
        with patch.object(AppController, "checkForUpdatesSilently"):
            return AppController(root)

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

    def test_fact_review_suggestion_replaces_only_target_line_and_marks_report_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "demo"
            (project / "script").mkdir(parents=True)
            project_file = project / "project.json"
            project_file.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
            controller = self._controller(root)
            controller._current_project_file = project_file
            controller._set_story(
                {
                    "narration": [
                        {"id": 1, "text_en": "Original one.", "event_ids": [1]},
                        {"id": 2, "text_en": "Original two.", "event_ids": [2]},
                    ]
                }
            )
            (project / "script" / "story.json").write_text(
                json.dumps(controller._story), encoding="utf-8"
            )
            controller._set_fact_review(
                {
                    "issue_count": 1,
                    "issues": [
                        {
                            "id": 1,
                            "severity": "medium",
                            "category": "source_support",
                            "narration_ids": [2],
                            "suggestion_en": "Safer two.",
                        }
                    ],
                }
            )

            controller.applyFactReviewSuggestion(1)

            self.assertEqual(controller._story_narration[0]["text_en"], "Original one.")
            self.assertEqual(controller._story_narration[1]["text_en"], "Safer two.")
            self.assertTrue(controller._fact_review["stale"])

    def test_cached_voice_duration_avoids_blocking_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "demo"
            audio_dir = project / "audio"
            audio_dir.mkdir(parents=True)
            audio = audio_dir / "narration.wav"
            audio.write_bytes(b"cached audio")
            project_file = project / "project.json"
            project_file.write_text(
                json.dumps(
                    {
                        "settings": {
                            "voice": {
                                "speed": 1.0,
                                "duration_sec": 12.5,
                                "audio_size": audio.stat().st_size,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            controller = self._controller(root)
            controller._current_project_file = project_file
            with patch("src.app_controller.probe_audio_duration") as probe:
                controller._load_voice(project_file)

            probe.assert_not_called()
            self.assertEqual(controller._narration_duration_sec, 12.5)

    def test_uncached_voice_duration_is_probed_in_background(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "demo"
            audio_dir = project / "audio"
            audio_dir.mkdir(parents=True)
            (audio_dir / "narration.wav").write_bytes(b"uncached audio")
            project_file = project / "project.json"
            project_file.write_text(json.dumps({"settings": {"voice": {}}}), encoding="utf-8")
            controller = self._controller(root)
            controller._current_project_file = project_file
            release = Event()
            completed = Event()

            def slow_probe(*_args):  # type: ignore[no-untyped-def]
                release.wait(1.0)
                completed.set()
                return 7.5

            with patch("src.app_controller.probe_audio_duration", side_effect=slow_probe):
                started = time.monotonic()
                controller._load_voice(project_file)
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 0.15)
                self.assertIn("后台读取", controller._voice_status)
                release.set()
                self.assertTrue(completed.wait(1.0))
                deadline = time.monotonic() + 1.0
                while controller._narration_duration_sec == 0 and time.monotonic() < deadline:
                    self.qt_app.processEvents()
                    time.sleep(0.01)

            self.assertEqual(controller._narration_duration_sec, 7.5)

    def test_manual_quality_check_exposes_loading_while_work_runs_in_background(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            controller = self._controller(Path(temporary_dir))
            release = Event()
            completed = Event()

            def slow_check():  # type: ignore[no-untyped-def]
                release.wait(1.0)
                completed.set()
                return {
                    "passed": True,
                    "pass_count": 1,
                    "info_count": 0,
                    "warning_count": 0,
                    "error_count": 0,
                    "checks": [{"level": "pass", "title": "测试", "detail": "完成"}],
                }

            with patch.object(controller, "_collect_quality_report", side_effect=slow_check):
                started = time.monotonic()
                controller.runQualityCheck()
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 0.15)
                self.assertTrue(controller.qualityCheckBusy)
                release.set()
                self.assertTrue(completed.wait(1.0))
                deadline = time.monotonic() + 1.0
                while controller.qualityCheckBusy and time.monotonic() < deadline:
                    self.qt_app.processEvents()
                    time.sleep(0.01)

            self.assertFalse(controller.qualityCheckBusy)
            self.assertTrue(controller.qualityCheckPassed)


if __name__ == "__main__":
    unittest.main()
