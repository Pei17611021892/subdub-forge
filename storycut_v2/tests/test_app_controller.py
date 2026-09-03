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

    def test_narrative_strategy_is_saved_and_restored_per_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "demo"
            project.mkdir(parents=True)
            project_file = project / "project.json"
            project_file.write_text(
                json.dumps({"name": "demo", "settings": {}}), encoding="utf-8"
            )
            controller = self._controller(root)
            controller._current_project_file = project_file

            controller.setNarrativeStrategy("nature_observation")

            saved = json.loads(project_file.read_text(encoding="utf-8"))
            self.assertEqual(
                saved["settings"]["narrative_strategy"], "nature_observation"
            )
            second = self._controller(root)
            second.openProject(str(project_file))
            self.assertEqual(second.narrativeStrategy, "nature_observation")

    def test_layered_analysis_is_automatic_and_not_user_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "demo"
            project.mkdir(parents=True)
            project_file = project / "project.json"
            project_file.write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "media": {"duration_sec": 360},
                        "settings": {"layered_analysis_enabled": False},
                    }
                ),
                encoding="utf-8",
            )
            controller = self._controller(root)
            controller.openProject(str(project_file))

            self.assertTrue(controller.layeredAnalysisEnabled)
            self.assertFalse(controller.layeredAnalysisSuggested)
            controller.setLayeredAnalysisEnabled(False)
            self.assertTrue(controller.layeredAnalysisEnabled)
            self.assertIn("自动管理", controller.notice)

    def test_recent_projects_groups_series_and_parts_can_be_opened(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            projects = root / "projects"
            members = [
                {"part_index": 1, "directory": "series", "title_zh": "起因"},
                {"part_index": 2, "directory": "series-part-02", "title_zh": "结果"},
            ]
            for index, directory in enumerate(("series", "series-part-02"), start=1):
                project = projects / directory
                project.mkdir(parents=True)
                (project / "project.json").write_text(
                    json.dumps(
                        {
                            "name": directory,
                            "stage": "scripted",
                            "series": {
                                "id": "same-series",
                                "part_index": index,
                                "part_count": 2,
                                "members": members,
                                "reason_zh": "一条视频装不下两个完整机制",
                            },
                        }
                    ),
                    encoding="utf-8",
                )
            controller = self._controller(root)

            self.assertEqual(len(controller.recentProjects), 1)
            self.assertEqual(controller.recentProjects[0]["seriesText"], "2 集系列")
            controller.openProject(str(projects / "series" / "project.json"))
            self.assertEqual(len(controller.seriesParts), 2, controller.storyStatus)
            controller.openSeriesPart(2)
            self.assertEqual(controller.projectName, "series-part-02")
            self.assertTrue(controller.seriesParts[1]["current"])

    def test_long_story_generation_materializes_automatic_series(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "long-demo"
            for folder in ("analysis/keyframes", "script", "timeline", "audio", "cache"):
                (project / folder).mkdir(parents=True, exist_ok=True)
            project_file = project / "project.json"
            project_file.write_text(
                json.dumps(
                    {
                        "name": "long-demo",
                        "stage": "understood",
                        "source_video": "D:/source.mp4",
                        "settings": {"content_mode": "speech"},
                        "artifacts": {"events": "analysis/events.json"},
                    }
                ),
                encoding="utf-8",
            )
            events = {
                "duration_sec": 360,
                "content_mode": "speech",
                "events": [
                    {"id": index, "start": index * 10, "end": index * 10 + 8, "transcript": f"fact {index}"}
                    for index in range(1, 13)
                ],
            }
            (project / "analysis" / "events.json").write_text(
                json.dumps(events), encoding="utf-8"
            )
            (project / "analysis" / "layered_structure.json").write_text(
                json.dumps({"recommended_highlight_event_ids": [2, 8]}),
                encoding="utf-8",
            )

            def fake_story(events_file, story_file, *_args, **_kwargs):
                payload = json.loads(Path(events_file).read_text(encoding="utf-8"))
                event_id = int(payload["events"][0]["id"])
                story = {
                    "word_count": 8,
                    "estimated_duration_sec": 6,
                    "narration": [
                        {"id": 1, "event_ids": [event_id], "text_en": "This is one complete episode line."}
                    ],
                    "outline": [],
                }
                Path(story_file).parent.mkdir(parents=True, exist_ok=True)
                Path(story_file).write_text(json.dumps(story), encoding="utf-8")
                return story

            evaluation = {
                "single_part_acceptable": False,
                "coverage_score": 0.6,
                "reason_zh": "两个机制都需要完整解释",
                "parts": [
                    {"part_index": 1, "title_zh": "机制一", "event_ids": [2, 3]},
                    {"part_index": 2, "title_zh": "机制二", "event_ids": [8, 9]},
                ],
            }
            with patch.dict("os.environ", {"OPENAI_API_KEY": "test"}), patch(
                "src.app_controller.generate_story_script", side_effect=fake_story
            ), patch(
                "src.app_controller.evaluate_story_preservation", return_value=evaluation
            ):
                controller = self._controller(root)
                controller.openProject(str(project_file))
                controller.generateStory(180)
                deadline = time.monotonic() + 3
                while controller.storyBusy and time.monotonic() < deadline:
                    self.qt_app.processEvents()
                    time.sleep(0.01)
                self.qt_app.processEvents()

            self.assertFalse(controller.storyBusy)
            self.assertEqual(len(controller.seriesParts), 2, controller.storyStatus)
            self.assertIn("自动拆分为 2 集", controller.storyStatus)
            self.assertTrue((root / "projects" / "long-demo-part-02" / "project.json").exists())

    def test_story_planning_rate_inherits_median_measured_voice_rate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            for name, words, duration in (
                ("first", 225, 168),
                ("second", 150, 100),
                ("ignored", 300, 143),
            ):
                script_dir = root / "projects" / name / "script"
                script_dir.mkdir(parents=True)
                timing_model = (
                    "english_word_syllable_v3"
                    if name == "ignored"
                    else "measured_voice_projection_v1"
                )
                (script_dir / "story.json").write_text(
                    json.dumps(
                        {
                            "timing_model": timing_model,
                            "word_count": words,
                            "estimated_duration_sec": duration,
                        }
                    ),
                    encoding="utf-8",
                )

            controller = self._controller(root)

            self.assertAlmostEqual(controller._planning_words_per_second(), 1.42, delta=0.01)

    def test_story_planning_rate_uses_config_fallback_without_voice_sample(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            controller = self._controller(Path(temporary_dir))
            controller._config.setdefault("story", {})["planning_words_per_second"] = 1.45

            self.assertEqual(controller._planning_words_per_second(), 1.45)

    def test_fact_review_suggestion_replaces_line_and_marks_issue_applied(self) -> None:
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
            self.assertFalse(bool(controller._fact_review.get("stale", False)))
            self.assertTrue(controller._fact_review_issues[0]["applied"])
            self.assertEqual(controller.contentReviewPendingCount, 0)
            self.assertIn("全部 1 条可应用建议已应用", controller.contentReviewStatus)
            saved_review = json.loads(
                (project / "script" / "content_review.json").read_text(encoding="utf-8")
            )
            self.assertTrue(saved_review["fact_review"]["issues"][0]["applied"])

    def test_terminology_suggestion_replaces_line_and_marks_issue_applied(self) -> None:
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
                        {"id": 1, "text_en": "A lock washer resists rotation.", "event_ids": [1]},
                        {"id": 2, "text_en": "The locking washer stays in place.", "event_ids": [2]},
                    ]
                }
            )
            (project / "script" / "story.json").write_text(
                json.dumps(controller._story), encoding="utf-8"
            )
            controller._set_terminology_review(
                {
                    "issue_count": 1,
                    "issues": [
                        {
                            "id": 1,
                            "category": "term_variant",
                            "narration_ids": [1],
                            "suggestion_en": "A locking washer resists rotation.",
                        }
                    ],
                }
            )

            controller.applyTerminologySuggestion(1)

            self.assertEqual(
                controller._story_narration[0]["text_en"],
                "A locking washer resists rotation.",
            )
            self.assertFalse(bool(controller._terminology_review.get("stale", False)))
            self.assertTrue(controller._terminology_review_issues[0]["applied"])

    def test_combined_content_review_updates_both_sections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            controller = self._controller(Path(temporary_dir))
            controller._fact_review_job_id = 7
            controller._fact_review_busy = True
            controller._terminology_review_busy = True

            controller._apply_fact_review_finished(
                True,
                "",
                {
                    "fact_review": {
                        "issue_count": 1,
                        "high_count": 0,
                        "medium_count": 1,
                        "low_count": 0,
                        "issues": [],
                    },
                    "terminology_review": {
                        "issue_count": 1,
                        "issues": [
                            {
                                "id": 1,
                                "category": "term_variant",
                                "narration_ids": [2],
                                "suggestion_en": "A consistent term.",
                            }
                        ],
                    },
                },
                7,
            )

            self.assertFalse(controller.factReviewBusy)
            self.assertFalse(controller.terminologyReviewBusy)
            self.assertEqual(controller._fact_review["issue_count"], 1)
            self.assertEqual(controller._terminology_review["issue_count"], 1)

    def test_review_suggestion_resplits_story_and_regenerates_reference_srt(self) -> None:
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
                    "timing_model": "english_word_syllable_v3",
                    "narration": [
                        {
                            "id": 1,
                            "text_en": "A cotter pin blocks the nut.",
                            "event_ids": [1],
                        },
                        {
                            "id": 2,
                            "text_en": "The repair is now secure.",
                            "event_ids": [2],
                        },
                    ],
                }
            )
            story_file = project / "script" / "story.json"
            story_file.write_text(json.dumps(controller._story), encoding="utf-8")

            controller.updateNarration(
                0,
                "With a drilled bolt and a castellated nut, a cotter pin physically blocks the nut.",
            )

            saved = json.loads(story_file.read_text(encoding="utf-8"))
            self.assertEqual(len(saved["narration"]), 3)
            self.assertEqual([item["id"] for item in saved["narration"]], [1, 2, 3])
            self.assertEqual(saved["narration"][2]["text_en"], "The repair is now secure.")
            self.assertGreater(saved["estimated_duration_sec"], 0)
            srt = (project / "script" / "tts" / "gpt_sovits_reference.srt").read_text(
                encoding="utf-8"
            )
            self.assertIn("1\n00:00:00,000 -->", srt)
            self.assertIn("3\n", srt)
            self.assertIn("The repair is now secure.", srt)

    def test_batch_review_replacements_use_original_ids_before_resplitting(self) -> None:
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
                    "timing_model": "english_word_syllable_v3",
                    "narration": [
                        {"id": 1, "text_en": "Original first line.", "event_ids": [1]},
                        {"id": 2, "text_en": "Original second line.", "event_ids": [2]},
                    ],
                }
            )
            (project / "script" / "story.json").write_text(
                json.dumps(controller._story), encoding="utf-8"
            )
            controller._set_fact_review(
                {
                    "issue_count": 2,
                    "issues": [
                        {
                            "id": 1,
                            "severity": "medium",
                            "narration_ids": [1],
                            "suggestion_en": "The first clause explains the setup, the second clause states the result.",
                        },
                        {
                            "id": 2,
                            "severity": "medium",
                            "narration_ids": [2],
                            "suggestion_en": "The second original line is safely replaced.",
                        },
                    ],
                }
            )

            controller.applyAllContentReviewSuggestions()

            texts = [item["text_en"] for item in controller._story_narration]
            self.assertIn("The second original line is safely replaced.", texts)
            self.assertNotIn("Original second line.", texts)
            self.assertFalse(bool(controller._fact_review.get("stale", False)))
            self.assertFalse(controller._fact_review_issues[0].get("applied", False))
            self.assertTrue(controller._fact_review_issues[1]["applied"])
            self.assertEqual(controller.contentReviewPendingCount, 0)

    def test_manual_story_edit_still_marks_review_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "demo"
            (project / "script").mkdir(parents=True)
            project_file = project / "project.json"
            project_file.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
            controller = self._controller(root)
            controller._current_project_file = project_file
            controller._set_story(
                {"narration": [{"id": 1, "text_en": "Original line.", "event_ids": [1]}]}
            )
            (project / "script" / "story.json").write_text(
                json.dumps(controller._story), encoding="utf-8"
            )
            controller._set_fact_review({"issue_count": 0, "issues": []})

            controller.updateNarration(0, "A manually edited line.")

            self.assertTrue(controller._fact_review["stale"])
            self.assertIn("需要重新检查", controller.contentReviewStatus)

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

    def test_open_project_without_narration_finishes_normally(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "demo"
            project.mkdir(parents=True)
            project_file = project / "project.json"
            project_file.write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "stage": "understood",
                        "source_video": "",
                        "media": {"duration_sec": 20, "width": 1920, "height": 1080},
                    }
                ),
                encoding="utf-8",
            )
            controller = self._controller(root)

            controller.openProject(project_file.as_uri())

            self.assertEqual(controller.projectName, "demo")
            self.assertIn("项目已恢复", controller.notice)
            self.assertEqual(controller.voiceStatus, "等待导出 SRT 到 GPT-SoVITS")

    def test_voice_speed_completion_saves_working_audio_and_clears_busy_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "demo"
            audio_dir = project / "audio"
            audio_dir.mkdir(parents=True)
            working_audio = audio_dir / "narration.wav"
            working_audio.write_bytes(b"processed audio")
            project_file = project / "project.json"
            project_file.write_text(
                json.dumps({"settings": {"voice": {}}, "artifacts": {}}), encoding="utf-8"
            )
            controller = self._controller(root)
            controller._current_project_file = project_file
            controller._voice_job_id = 3
            controller._voice_busy = True

            with patch.object(controller, "_apply_voice_timing_to_matches"):
                controller._apply_voice_processing_finished(
                    True,
                    "",
                    {"speed": 1.15, "duration_sec": 178.26, "segments": []},
                    3,
                )

            saved = json.loads(project_file.read_text(encoding="utf-8"))
            self.assertFalse(controller.voiceBusy)
            self.assertEqual(saved["settings"]["voice"]["speed"], 1.15)
            self.assertEqual(saved["settings"]["voice"]["audio_size"], working_audio.stat().st_size)
            self.assertIn("1.15x", controller.voiceStatus)

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

    def test_analysis_hides_unreliable_estimate_until_current_run_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            controller = self._controller(Path(temporary_dir))
            controller._analysis_busy = True
            controller._analysis_started_at = 100.0
            controller._analysis_job_id = 7

            with patch("src.app_controller.time.monotonic", return_value=110.0):
                controller._apply_analysis_progress(0.1, "加载模型", 35.0, 7)
                self.assertEqual(controller.analysisEtaText, "正在处理")
                self.assertFalse(controller.analysisEtaReliable)
                self.assertEqual(controller.analysisEstimatedTotalText, "")

            # Four current-stage observations across more than 12 seconds all
            # predict the same finish time, so the remaining time is now safe to show.
            for now in (131.0, 136.0, 143.0, 150.0):
                with patch("src.app_controller.time.monotonic", return_value=now):
                    controller._apply_analysis_progress(
                        0.2 + (now - 131.0) / 100.0,
                        "正在转写",
                        300.0 - now,
                        7,
                    )

            with patch("src.app_controller.time.monotonic", return_value=150.0):
                self.assertTrue(controller.analysisEtaReliable)
                self.assertEqual(controller.analysisEtaText, "预计剩余约 2:30")

    def test_applying_duration_revision_archives_voice_and_rematches_shots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "demo"
            for folder in ("script", "analysis", "audio", "timeline"):
                (project / folder).mkdir(parents=True, exist_ok=True)
            project_file = project / "project.json"
            project_file.write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "artifacts": {
                            "story": "script/story.json",
                            "narration_audio": "audio/narration.wav",
                            "narration_srt": "audio/narration.srt",
                        },
                        "settings": {"voice": {"speed": 1.0, "duration_sec": 230, "audio_size": 5}},
                    }
                ),
                encoding="utf-8",
            )
            old_story = {
                "word_count": 20,
                "narration": [{"id": 1, "event_ids": [1], "text_en": "The old long story.", "estimated_duration_sec": 4}],
            }
            revised = {
                "word_count": 5,
                "estimated_duration_sec": 3,
                "narration": [
                    {"id": 1, "event_ids": [1], "text_en": "She starts the machine.", "visual_query": "machine", "estimated_duration_sec": 3, "word_count": 4}
                ],
                "outline": [{"order": 1, "event_ids": [1], "purpose": "hook", "summary": "Start"}],
            }
            (project / "script" / "story.json").write_text(json.dumps(old_story), encoding="utf-8")
            (project / "analysis" / "events.json").write_text(
                json.dumps({"events": [{"id": 1, "start": 0, "end": 8, "visual_description": "machine"}]}),
                encoding="utf-8",
            )
            (project / "audio" / "narration.wav").write_bytes(b"voice")
            (project / "audio" / "narration.srt").write_text("1\n00:00:00,000 --> 00:00:04,000\nOld\n", encoding="utf-8")
            (project / "timeline" / "matches.json").write_text(
                json.dumps({"items": [], "marker": "old matches"}), encoding="utf-8"
            )
            (project / "timeline" / "rough_cut.json").write_text(
                json.dumps({"items": [], "marker": "old rough cut"}), encoding="utf-8"
            )
            controller = self._controller(root)
            controller._current_project_file = project_file
            controller._set_story(old_story)
            controller._duration_revision_proposal = {"revised_story": revised}

            controller.applyNarrationDurationRevision()

            saved = json.loads((project / "script" / "story.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["narration"][0]["text_en"], "She starts the machine.")
            self.assertTrue((project / "timeline" / "matches.json").exists())
            self.assertFalse((project / "audio" / "narration.wav").exists())
            archives = list((project / "archive").glob("duration_revision_*"))
            self.assertEqual(len(archives), 1)
            self.assertTrue((archives[0] / "state" / "audio" / "narration.wav").exists())
            self.assertTrue((archives[0] / "state" / "timeline" / "matches.json").exists())
            self.assertTrue((archives[0] / "state" / "project.json").exists())
            self.assertFalse(controller.narrationAudioReady)
            self.assertTrue(controller.canRestoreDurationRevision)
            self.assertIn("5 词", controller.durationRevisionRestoreCurrentStats)
            self.assertIn("20 词", controller.durationRevisionRestoreArchivedStats)
            self.assertIn("She starts the machine.", controller.durationRevisionRestoreCurrentText)
            self.assertIn("The old long story.", controller.durationRevisionRestoreArchivedText)

            controller.restoreDurationRevisionArchive()

            restored_story = json.loads((project / "script" / "story.json").read_text(encoding="utf-8"))
            restored_matches = json.loads((project / "timeline" / "matches.json").read_text(encoding="utf-8"))
            restored_project = json.loads(project_file.read_text(encoding="utf-8"))
            self.assertEqual(restored_story["narration"][0]["text_en"], "The old long story.")
            self.assertEqual(restored_matches["marker"], "old matches")
            self.assertTrue((project / "audio" / "narration.wav").exists())
            self.assertEqual(restored_project["settings"]["voice"]["duration_sec"], 230)
            self.assertIn("restore_replaced_", restored_project["artifacts"]["duration_revision_archive"])
            self.assertTrue(controller.canRestoreDurationRevision)


if __name__ == "__main__":
    unittest.main()
