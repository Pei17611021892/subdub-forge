from __future__ import annotations

import tempfile
import unittest
import json
import os
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

    def test_manuscript_project_uses_neutral_name_and_persists_source_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            controller = self._controller(root)

            controller.createManuscriptProject("  大象之间是否拥有语言？\r\n它们会使用低频声音。  ")

            self.assertTrue(controller.hasProject)
            self.assertTrue(controller.manuscriptProject)
            self.assertEqual(controller.projectType, "manuscript")
            self.assertTrue(controller.projectName.startswith("manuscript-"))
            self.assertEqual(
                controller.sourceManuscriptText,
                "大象之间是否拥有语言？\n它们会使用低频声音。",
            )
            project_file = controller._current_project_file
            self.assertIsNotNone(project_file)
            payload = json.loads(project_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 2)
            self.assertEqual(payload["project_type"], "manuscript")
            self.assertEqual(payload["stage"], "manuscript_ready")
            self.assertEqual(payload["source_manuscript"], "source/manuscript.txt")
            self.assertTrue((project_file.parent / "storyboard").is_dir())
            self.assertTrue((project_file.parent / "assets").is_dir())

            controller.updateSourceManuscript("新的完整文稿。")
            reopened = self._controller(root)
            reopened.openProject(str(project_file))
            self.assertTrue(reopened.manuscriptProject)
            self.assertEqual(reopened.sourceManuscriptText, "新的完整文稿。")
            self.assertIn("纯文稿", reopened.recentProjects[0]["video"])
            self.assertEqual(reopened.recentProjects[0]["projectType"], "manuscript")
            self.assertEqual(reopened.recentProjects[0]["projectTypeText"], "纯文稿")

    def test_manuscript_candidate_image_can_be_previewed_and_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            controller = self._controller(root)
            controller.createManuscriptProject("一段需要配图的旁白。")
            image = root / "projects" / "candidate.jpg"
            image.write_bytes(b"image")
            controller._matches = [
                {
                    "narration_id": 3,
                    "narration_duration": 4.5,
                    "candidates": [
                        {
                            "event_id": 12,
                            "source_path": str(image),
                            "media_kind": "image",
                            "start": 0,
                            "end": 180,
                        }
                    ],
                }
            ]

            self.assertTrue(controller.requestCandidatePreview(3, 12))

            self.assertEqual(controller.previewSourceName, "candidate.jpg")
            self.assertEqual(controller.previewDurationSeconds, 4.5)
            self.assertEqual(controller.previewPosition, 0.0)
            self.assertEqual(controller.previewUrl, image.as_uri())
            self.assertFalse(controller.previewBusy)

            with patch("src.app_controller.QDesktopServices.openUrl", return_value=True) as open_url:
                self.assertTrue(controller.openCandidateSource(3, 12))
            opened_path = Path(open_url.call_args.args[0].toLocalFile())
            self.assertEqual(opened_path, image.resolve())

            controller.clearCandidatePreview()

            self.assertEqual(controller.previewSourceName, controller.projectName)
            self.assertEqual(controller.previewDurationSeconds, controller.durationSeconds)
            self.assertEqual(controller._preview_source_path, "")

    def test_video_cover_opens_current_source_in_system_player(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            project_file = root / "projects" / "demo" / "project.json"
            project_file.parent.mkdir(parents=True)
            project_file.write_text(json.dumps({"name": "demo"}), encoding="utf-8")
            controller = self._controller(root)
            controller._current_project_file = project_file
            controller._project_type = "video"
            controller._video_path = str(source)

            with patch("src.app_controller.QDesktopServices.openUrl", return_value=True) as open_url:
                self.assertTrue(controller.openCurrentVideoSource())

            opened_path = Path(open_url.call_args.args[0].toLocalFile())
            self.assertEqual(opened_path, source.resolve())

    def test_project_loaded_signal_observes_restored_story_and_applied_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "demo"
            (project / "script").mkdir(parents=True)
            project_file = project / "project.json"
            project_file.write_text(
                json.dumps({"name": "demo", "project_type": "video", "stage": "scripted"}),
                encoding="utf-8",
            )
            (project / "script" / "story.json").write_text(
                json.dumps({"narration": [{"id": 1, "text_en": "Ready.", "event_ids": [1]}]}),
                encoding="utf-8",
            )
            (project / "script" / "content_review.json").write_text(
                json.dumps(
                    {
                        "fact_review": {
                            "issue_count": 1,
                            "issues": [
                                {
                                    "id": 1,
                                    "narration_ids": [1],
                                    "suggestion_en": "Ready.",
                                    "applied": True,
                                }
                            ],
                        },
                        "terminology_review": {},
                    }
                ),
                encoding="utf-8",
            )
            controller = self._controller(root)
            observed: list[tuple[int, bool]] = []
            controller.projectLoaded.connect(
                lambda: observed.append(
                    (len(controller.storyNarration), controller.contentReviewHasAppliedSuggestions)
                )
            )

            controller.openProject(str(project_file))

            self.assertEqual(observed, [(1, True)])

    def test_recent_projects_finds_a_project_copied_with_an_extra_folder_level(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            nested = root / "projects" / "v2-0908" / "v2-0908"
            nested.mkdir(parents=True)
            (nested / "project.json").write_text(
                json.dumps(
                    {
                        "name": "v2-0908",
                        "stage": "understood",
                        "source_video": "D:/missing-source.mp4",
                        "updated_at": "2026-09-08T00:22:35",
                    }
                ),
                encoding="utf-8",
            )

            controller = self._controller(root)

            self.assertEqual(len(controller.recentProjects), 1)
            self.assertEqual(controller.recentProjects[0]["name"], "v2-0908")
            self.assertIn("v2-0908/v2-0908/project.json", controller.recentProjects[0]["projectFile"])

    def test_import_manuscript_reads_file_and_empty_text_does_not_create_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            manuscript_file = root / "elephant.md"
            manuscript_file.write_text("# 大象的语言\n\n它们如何交流？", encoding="utf-8")
            controller = self._controller(root)

            controller.createManuscriptProject("   ")
            self.assertFalse(controller.hasProject)
            self.assertIn("不能为空", controller.notice)

            controller.importManuscript(str(manuscript_file))
            self.assertTrue(controller.manuscriptProject)
            self.assertEqual(
                controller.sourceManuscriptText,
                "# 大象的语言\n\n它们如何交流？",
            )

    def test_manuscript_asset_manifest_restores_missing_entries_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            controller = self._controller(root)
            controller.createManuscriptProject("素材恢复测试。")
            project_file = controller._current_project_file
            missing = root / "removed.mp4"
            manifest = project_file.parent / "assets" / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "assets": [
                            {
                                "id": "asset-missing",
                                "source_path": str(missing),
                                "kind": "video",
                                "file_status": "missing",
                                "analysis_status": "missing",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            reopened = self._controller(root)
            reopened.openProject(str(project_file))

            self.assertEqual(reopened.manuscriptAssetCount, 1)
            self.assertIn("1 个文件缺失", reopened.matchingStatus)

    def test_manuscript_project_can_start_preview_without_single_source_video(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            controller = self._controller(root)
            controller.createManuscriptProject("多素材预览测试。")
            project_file = controller._current_project_file
            asset = root / "still.jpg"
            asset.write_bytes(b"image")
            rough_cut = project_file.parent / "timeline" / "rough_cut.json"
            rough_cut.write_text(
                json.dumps(
                    {
                        "duration_sec": 2.0,
                        "clips": [
                            {
                                "source_path": str(asset),
                                "media_kind": "image",
                                "source_start": 0,
                                "source_end": 2,
                                "width": 800,
                                "height": 1200,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            audio = project_file.parent / "audio" / "narration.wav"
            audio.parent.mkdir(exist_ok=True)
            audio.write_bytes(b"audio")
            controller._narration_audio_path = str(audio)
            controller._narration_duration_sec = 2.0

            with patch.object(controller, "_run_quality_check", return_value=True), patch.object(
                controller, "_start_rough_preview"
            ) as start_preview:
                controller.generateRoughPreview()

            start_preview.assert_called_once()
            self.assertEqual(controller._primary_timeline_dimensions(), (800, 1200))

    def test_editing_source_manuscript_marks_existing_storyboard_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            controller = self._controller(root)
            controller.createManuscriptProject("第一版文稿。")
            project_file = controller._current_project_file
            story = {
                "narration": [{"id": 1, "text_en": "The first draft."}],
            }
            storyboard = {
                "shot_segment_count": 1,
                "recommended_asset_count": 1,
                "coverage_percent": 0,
                "beats": [{"id": 1, "text_en": "The first draft."}],
            }
            (project_file.parent / "script" / "story.json").write_text(
                json.dumps(story), encoding="utf-8"
            )
            storyboard_file = project_file.parent / "storyboard" / "storyboard.json"
            storyboard_file.write_text(json.dumps(storyboard), encoding="utf-8")
            controller._set_story(story)
            controller._load_storyboard(project_file)

            controller.updateSourceManuscript("第二版文稿。")

            self.assertTrue(controller.storyboardStale)
            self.assertIn("需要重新规划", controller.storyStatus)
            saved_storyboard = json.loads(storyboard_file.read_text(encoding="utf-8"))
            self.assertTrue(saved_storyboard["stale"])

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

    def test_export_canvas_mode_is_saved_and_restored_per_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "demo"
            project.mkdir(parents=True)
            project_file = project / "project.json"
            project_file.write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "media": {"width": 1920, "height": 1080},
                        "settings": {},
                    }
                ),
                encoding="utf-8",
            )
            controller = self._controller(root)
            controller._current_project_file = project_file

            controller.setExportFitMode("original")
            controller.setExportFitMode("crop_stretch")
            controller.setCanvasAspectRatio("9:16")
            controller.setCanvasFixedScale(False)
            controller.setCanvasScale(1.25, 0.8)

            saved = json.loads(project_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["settings"]["export"]["fit_mode"], "crop_stretch")
            self.assertEqual(saved["settings"]["export"]["canvas_aspect_ratio"], "9:16")
            self.assertFalse(saved["settings"]["export"]["canvas_fixed_scale"])
            self.assertAlmostEqual(saved["settings"]["export"]["canvas_scale_x"], 1.25)
            self.assertAlmostEqual(saved["settings"]["export"]["canvas_scale_y"], 0.8)
            self.assertEqual(controller.subtitleCanvasWidth, 1080)
            self.assertEqual(controller.subtitleCanvasHeight, 1920)
            reopened = self._controller(root)
            reopened.openProject(str(project_file))
            self.assertEqual(reopened.exportFitMode, "crop_stretch")
            self.assertEqual(reopened.canvasAspectRatio, "9:16")
            self.assertFalse(reopened.canvasFixedScale)
            self.assertAlmostEqual(reopened.canvasScaleX, 1.25)
            self.assertAlmostEqual(reopened.canvasScaleY, 0.8)

            reopened.setExportFitMode("original")
            self.assertEqual(reopened.subtitleCanvasWidth, 1920)
            self.assertEqual(reopened.subtitleCanvasHeight, 1080)

            reopened.setExportFitMode("crop_stretch")
            reopened.setCanvasFixedScale(False)
            reopened.setCanvasScale(50, 0.01)
            self.assertAlmostEqual(reopened.canvasScaleX, 10.0)
            self.assertAlmostEqual(reopened.canvasScaleY, 0.25)

    def test_subtitle_cleaned_video_is_loaded_and_invalidated_by_cleanup_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "demo"
            media = project / "media"
            media.mkdir(parents=True)
            cleaned = media / "source_without_subtitles.mp4"
            cleaned.write_bytes(b"cleaned")
            cleaned_preview = project / "cache" / "source_without_subtitles.jpg"
            cleaned_preview.parent.mkdir(parents=True)
            cleaned_preview.write_bytes(b"preview")
            project_file = project / "project.json"
            project_file.write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "media": {"width": 1920, "height": 1080},
                        "settings": {},
                        "artifacts": {
                            "subtitle_cleaned_video": "media/source_without_subtitles.mp4"
                        },
                    }
                ),
                encoding="utf-8",
            )
            controller = self._controller(root)
            observed_cleaned_states: list[bool] = []
            controller.subtitleEffectPreviewChanged.connect(
                lambda: observed_cleaned_states.append(controller.subtitleCleanedVideoReady)
            )
            controller.openProject(str(project_file))
            self.assertTrue(controller.subtitleCleanedVideoReady)
            self.assertIn(True, observed_cleaned_states)
            self.assertTrue(controller.subtitleCleanedPreviewUrl.endswith("source_without_subtitles.jpg"))

            controller.updateSubtitleStyle("cleanupY", 0.75)

            saved = json.loads(project_file.read_text(encoding="utf-8"))
            self.assertFalse(controller.subtitleCleanedVideoReady)
            self.assertEqual(controller.subtitleCleanedPreviewUrl, "")
            self.assertNotIn("subtitle_cleaned_video", saved["artifacts"])
            self.assertTrue(cleaned.exists())

    def test_cleaned_video_delete_and_final_video_save(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "demo"
            media = project / "media"
            media.mkdir(parents=True)
            cleaned = media / "source_without_subtitles.mp4"
            cleaned.write_bytes(b"cleaned")
            project_file = project / "project.json"
            project_file.write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "settings": {},
                        "artifacts": {
                            "subtitle_cleaned_video": "media/source_without_subtitles.mp4"
                        },
                    }
                ),
                encoding="utf-8",
            )
            controller = self._controller(root)
            controller.openProject(str(project_file))
            controller.deleteSubtitleCleanedVideo()
            self.assertFalse(cleaned.exists())
            self.assertFalse(controller.subtitleCleanedVideoReady)

            preview = root / "export" / "preview.mp4"
            preview.parent.mkdir(parents=True)
            preview.write_bytes(b"video")
            destination = root / "saved" / "finished.mp4"
            controller._export_path = str(preview)
            controller.saveFinalVideo(destination.as_uri())
            self.assertEqual(destination.read_bytes(), b"video")

    def test_subtitle_style_preview_prefers_cleaned_video_and_falls_back_to_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            cleaned = root / "projects" / "demo" / "media" / "source_without_subtitles.mp4"
            cleaned.parent.mkdir(parents=True)
            cleaned.write_bytes(b"cleaned")
            controller = self._controller(root)
            controller._video_path = str(source)
            controller._subtitle_cleaned_video_path = str(cleaned)

            self.assertEqual(controller._subtitle_style_preview_video_path(), cleaned)
            self.assertEqual(controller.subtitleStylePreviewSourceName, cleaned.name)

            cleaned.unlink()
            self.assertEqual(controller._subtitle_style_preview_video_path(), source)
            self.assertEqual(controller.subtitleStylePreviewSourceName, source.name)

    def test_copied_project_can_preview_from_its_cleaned_video_when_original_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            missing_source = root / "home-computer-source.mp4"
            cleaned = root / "projects" / "demo" / "media" / "source_without_subtitles.mp4"
            cleaned.parent.mkdir(parents=True)
            cleaned.write_bytes(b"portable-cleaned-source")
            controller = self._controller(root)
            controller._video_path = str(missing_source)
            controller._subtitle_cleaned_video_path = str(cleaned)

            self.assertTrue(controller._ensure_source_video(allow_cleaned_video=True))
            self.assertEqual(controller._available_video_source(), cleaned)
            self.assertIn("另一台电脑", controller.notice)
            self.assertFalse(controller._ensure_source_video())

    def test_text_style_edits_keep_preview_image_and_background_mode_is_exported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            controller = self._controller(Path(temporary_dir))
            controller._subtitle_effect_preview_url = "file:///cleaned-preview.jpg"

            controller.updateSubtitleStyle("fontSize", 56)
            self.assertEqual(
                controller.subtitleEffectPreviewUrl, "file:///cleaned-preview.jpg"
            )

            controller.updateSubtitleStyle("backgroundEnabled", True)
            controller.updateSubtitleStyle("backgroundMode", "blur")
            export = controller._config_with_project_style()["export"]
            self.assertTrue(export["subtitle_background_enabled"])
            self.assertEqual(export["subtitle_background_mode"], "blur")
            self.assertGreater(export["subtitle_background_height"], 0)

    def test_subtitle_test_preview_is_not_loaded_as_final_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "demo"
            project.mkdir(parents=True)
            subtitle_test = root / "export" / "demo_storycut_subtitle_test.mp4"
            subtitle_test.parent.mkdir(parents=True)
            subtitle_test.write_bytes(b"test")
            project_file = project / "project.json"
            relative = Path("..") / ".." / "export" / subtitle_test.name
            project_file.write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "artifacts": {"rough_preview": relative.as_posix()},
                        "settings": {},
                    }
                ),
                encoding="utf-8",
            )
            controller = self._controller(root)

            controller._load_export(project_file)

            self.assertFalse(controller.previewVideoReady)
            self.assertTrue(controller.subtitleTestPreviewReady)
            saved = json.loads(project_file.read_text(encoding="utf-8"))
            self.assertNotIn("rough_preview", saved["artifacts"])
            self.assertEqual(saved["artifacts"]["subtitle_test_preview"], relative.as_posix())

    def test_render_setting_change_unbinds_previews_without_deleting_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "demo"
            project.mkdir(parents=True)
            final_preview = root / "export" / "demo_final.mp4"
            subtitle_preview = root / "export" / "demo_subtitle_test.mp4"
            final_preview.parent.mkdir(parents=True)
            final_preview.write_bytes(b"final")
            subtitle_preview.write_bytes(b"subtitle")
            project_file = project / "project.json"
            project_file.write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "stage": "previewed",
                        "settings": {},
                        "artifacts": {
                            "rough_preview": "../../export/demo_final.mp4",
                            "subtitle_test_preview": "../../export/demo_subtitle_test.mp4",
                            "rough_cut": "timeline/rough_cut.json",
                        },
                    }
                ),
                encoding="utf-8",
            )
            controller = self._controller(root)
            controller.openProject(str(project_file))
            self.assertTrue(controller.previewVideoReady)
            self.assertTrue(controller.subtitleTestPreviewReady)

            controller.setCanvasAspectRatio("9:16")

            self.assertFalse(controller.previewVideoReady)
            self.assertFalse(controller.subtitleTestPreviewReady)
            self.assertTrue(final_preview.exists())
            self.assertTrue(subtitle_preview.exists())
            saved = json.loads(project_file.read_text(encoding="utf-8"))
            self.assertNotIn("rough_preview", saved["artifacts"])
            self.assertNotIn("subtitle_test_preview", saved["artifacts"])
            self.assertIn("rough_preview", saved["artifact_state"]["invalidated"])
            self.assertEqual(saved["stage"], "matched")

            reopened = self._controller(root)
            reopened.openProject(str(project_file))
            self.assertFalse(reopened.previewVideoReady)
            self.assertFalse(reopened.subtitleTestPreviewReady)

    def test_opening_another_project_clears_previous_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "second"
            project.mkdir(parents=True)
            project_file = project / "project.json"
            project_file.write_text(
                json.dumps({"name": "second", "settings": {}}), encoding="utf-8"
            )
            controller = self._controller(root)
            controller._export_path = str(root / "old-preview.mp4")
            controller._subtitle_test_preview_path = str(root / "old-test.mp4")
            controller._quality_report = {
                "passed": False,
                "error_count": 1,
                "checks": [{"level": "error", "title": "旧项目", "detail": "旧状态"}],
            }
            controller._narration_audio_path = str(root / "old.wav")
            controller._analysis_busy = True
            controller._export_busy = True

            controller.openProject(str(project_file))

            self.assertEqual(controller.projectName, "second")
            self.assertFalse(controller.previewVideoReady)
            self.assertFalse(controller.subtitleTestPreviewReady)
            self.assertFalse(controller.qualityCheckPassed)
            self.assertEqual(controller.qualityCheckItems, [])
            self.assertFalse(controller.analysisBusy)
            self.assertFalse(controller.exportBusy)
            self.assertFalse(controller.narrationAudioReady)

    def test_reimporting_audio_unbinds_previous_srt_and_previews(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "demo"
            audio_dir = project / "audio"
            audio_dir.mkdir(parents=True)
            old_srt = audio_dir / "narration.srt"
            old_original_srt = audio_dir / "narration_original.srt"
            old_srt.write_text("old timing", encoding="utf-8")
            old_original_srt.write_text("old timing", encoding="utf-8")
            project_file = project / "project.json"
            project_file.write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "settings": {"voice": {}},
                        "artifacts": {
                            "narration_srt": "audio/narration.srt",
                            "narration_srt_original": "audio/narration_original.srt",
                            "rough_preview": "../../export/old.mp4",
                        },
                    }
                ),
                encoding="utf-8",
            )
            source = root / "replacement.wav"
            source.write_bytes(b"replacement")
            controller = self._controller(root)
            controller._current_project_file = project_file
            controller._export_path = str(root / "export" / "old.mp4")
            controller._synced_srt_path = str(old_srt)

            def fake_import(_source, original, *_args):  # type: ignore[no-untyped-def]
                original.write_bytes(b"new audio")
                return {"duration_sec": 12.5}

            with patch("src.app_controller.import_narration_audio", side_effect=fake_import), patch.object(
                controller, "_apply_voice_timing_to_matches"
            ):
                controller.importNarrationAudio(source.as_uri())

            self.assertTrue(controller.narrationAudioReady)
            self.assertFalse(controller.syncedSrtReady)
            self.assertFalse(controller.previewVideoReady)
            self.assertTrue(old_srt.exists())
            saved = json.loads(project_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["artifacts"]["narration_audio"], "audio/narration.wav")
            self.assertNotIn("narration_srt", saved["artifacts"])
            self.assertIn("narration_srt", saved["artifact_state"]["invalidated"])

            reopened = self._controller(root)
            reopened.openProject(str(project_file))
            self.assertTrue(reopened.narrationAudioReady)
            self.assertFalse(reopened.syncedSrtReady)

    def test_rematching_reapplies_existing_audio_and_srt_timing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "demo"
            (project / "script").mkdir(parents=True)
            (project / "analysis").mkdir()
            (project / "timeline").mkdir()
            (project / "audio").mkdir()
            project_file = project / "project.json"
            project_file.write_text(
                json.dumps({"name": "demo", "settings": {}, "artifacts": {}}),
                encoding="utf-8",
            )
            (project / "script" / "story.json").write_text("{}", encoding="utf-8")
            (project / "analysis" / "events.json").write_text("{}", encoding="utf-8")
            audio = project / "audio" / "narration.wav"
            audio.write_bytes(b"audio")
            srt = project / "audio" / "narration.srt"
            srt.write_text(
                "1\n00:00:00,000 --> 00:00:12,000\nVoice line.\n",
                encoding="utf-8",
            )
            controller = self._controller(root)
            controller._current_project_file = project_file
            controller._narration_audio_path = str(audio)
            controller._synced_srt_path = str(srt)
            controller._narration_duration_sec = 12.0

            with patch(
                "src.app_controller.generate_shot_matches",
                return_value={"items": [], "matching_warnings": []},
            ), patch(
                "src.app_controller.apply_voice_timing",
                return_value={"items": [], "matching_warnings": []},
            ) as retime, patch("src.app_controller.build_rough_cut"):
                controller.generateMatches()

            self.assertEqual(retime.call_args.args[1], 12.0)
            self.assertEqual(len(retime.call_args.args[2]), 1)

    def test_open_project_unbinds_a_preview_older_than_its_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "demo"
            timeline = project / "timeline"
            timeline.mkdir(parents=True)
            preview = root / "export" / "demo_final.mp4"
            preview.parent.mkdir(parents=True)
            preview.write_bytes(b"old preview")
            old_time = time.time() - 20
            preview.touch()
            os.utime(preview, (old_time, old_time))
            rough_cut = timeline / "rough_cut.json"
            rough_cut.write_text(json.dumps({"duration_sec": 10}), encoding="utf-8")
            project_file = project / "project.json"
            project_file.write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "stage": "previewed",
                        "settings": {},
                        "artifacts": {
                            "rough_cut": "timeline/rough_cut.json",
                            "rough_preview": "../../export/demo_final.mp4",
                        },
                    }
                ),
                encoding="utf-8",
            )

            controller = self._controller(root)
            controller.openProject(str(project_file))

            self.assertFalse(controller.previewVideoReady)
            self.assertTrue(preview.exists())
            saved = json.loads(project_file.read_text(encoding="utf-8"))
            self.assertIn("rough_preview", saved["artifact_state"]["invalidated"])
            self.assertEqual(saved["stage"], "matched")

    def test_render_preflight_does_not_check_the_previous_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            controller = self._controller(Path(temporary_dir))
            report = {
                "passed": True,
                "pass_count": 1,
                "info_count": 0,
                "warning_count": 0,
                "error_count": 0,
                "checks": [],
            }
            with patch.object(controller, "_collect_quality_report", return_value=report) as collect:
                self.assertTrue(controller._run_quality_check())

            collect.assert_called_once_with(deep_scan=False, include_render=False)

    def test_subtitle_test_completion_does_not_replace_final_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            final_preview = root / "final.mp4"
            subtitle_test = root / "subtitle_test.mp4"
            final_preview.write_bytes(b"final")
            subtitle_test.write_bytes(b"test")
            controller = self._controller(root)
            controller._export_path = str(final_preview)
            controller._export_job_id = 3

            controller._apply_export_finished(
                True,
                "仅字幕测试预览已生成",
                {"path": str(subtitle_test), "preview_kind": "subtitle_test"},
                3,
            )

            self.assertTrue(controller.previewVideoReady)
            self.assertEqual(controller.previewVideoPath, str(final_preview))
            self.assertTrue(controller.subtitleTestPreviewReady)

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
                        "settings": {
                            "content_mode": "speech",
                            "series_split_enabled": True,
                        },
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

    def test_series_split_is_opt_in_and_saved_per_project(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "demo"
            project.mkdir(parents=True)
            project_file = project / "project.json"
            project_file.write_text(
                json.dumps({"name": "demo", "settings": {}}), encoding="utf-8"
            )
            controller = self._controller(root)
            controller.openProject(str(project_file))

            self.assertFalse(controller.seriesSplitEnabled)
            controller.setSeriesSplitEnabled(True)

            saved = json.loads(project_file.read_text(encoding="utf-8"))
            self.assertTrue(saved["settings"]["series_split_enabled"])
            reopened = self._controller(root)
            reopened.openProject(str(project_file))
            self.assertTrue(reopened.seriesSplitEnabled)

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

    def test_voice_calibration_updates_current_story_and_keeps_original_speed_sample(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "demo"
            (project / "script").mkdir(parents=True)
            project_file = project / "project.json"
            project_file.write_text(
                json.dumps(
                    {
                        "name": "demo",
                        "settings": {
                            "voice": {
                                "duration_sec": 150,
                                "original_duration_sec": 180,
                                "speed": 1.2,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            story = {
                "word_count": 225,
                "estimated_duration_sec": 140,
                "narration": [
                    {
                        "id": 1,
                        "text_en": "A measured sentence.",
                        "word_count": 225,
                        "estimated_duration_sec": 140,
                    }
                ],
            }
            story_file = project / "script" / "story.json"
            story_file.write_text(json.dumps(story), encoding="utf-8")
            controller = self._controller(root)
            controller._current_project_file = project_file
            controller._set_story(story)

            controller._calibrate_story_with_voice(150, timing_source="processed_audio")

            saved = json.loads(story_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["estimated_duration_sec"], 150)
            self.assertIn("实际配音 2:30", controller.storyStats)
            self.assertAlmostEqual(controller._planning_words_per_second(), 1.25, delta=0.001)

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
            self.assertTrue(controller.contentReviewHasAppliedSuggestions)
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

    def test_applying_one_review_suggestion_preserves_unapplied_issues(self) -> None:
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
                        {"id": 1, "text_en": "First.", "event_ids": [1]},
                        {"id": 2, "text_en": "Second.", "event_ids": [2]},
                    ]
                }
            )
            (project / "script" / "story.json").write_text(
                json.dumps(controller._story), encoding="utf-8"
            )
            controller._set_fact_review(
                {
                    "issue_count": 2,
                    "issues": [
                        {"id": 1, "narration_ids": [1], "suggestion_en": "First revised."},
                        {"id": 2, "narration_ids": [2], "suggestion_en": "Second revised."},
                    ],
                }
            )

            controller.applyFactReviewSuggestion(1)

            self.assertEqual(len(controller.contentReviewIssues), 2)
            self.assertTrue(controller.contentReviewIssues[0]["applied"])
            self.assertFalse(controller.contentReviewIssues[1].get("applied", False))
            saved = json.loads(
                (project / "script" / "content_review.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(saved["fact_review"]["issues"]), 2)
            self.assertFalse(saved["fact_review"]["issues"][1].get("applied", False))
            controller._fact_review["stale"] = True
            self.assertIn("仍保留 1 条未应用建议", controller.contentReviewStatus)

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

    def test_open_project_restores_persistent_vision_failure_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "vision-failed"
            analysis = project / "analysis"
            analysis.mkdir(parents=True)
            (analysis / "events.json").write_text(
                json.dumps(
                    {
                        "events": [
                            {
                                "id": 1,
                                "start": 0,
                                "end": 3,
                                "keyframe": "keyframes/scene_0001.jpg",
                                "visual_description": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            project_file = project / "project.json"
            project_file.write_text(
                json.dumps(
                    {
                        "name": "vision-failed",
                        "project_type": "video",
                        "analysis_state": "vision_failed",
                        "stage": "analysis_partial",
                        "settings": {},
                        "warnings": {"vision": "content_policy_violation"},
                    }
                ),
                encoding="utf-8",
            )

            controller = self._controller(root)
            controller.openProject(str(project_file))

            self.assertFalse(controller.analysisComplete)
            self.assertTrue(controller.analysisNeedsVisionRetry)
            self.assertIn("content_policy_violation", controller.visionFailureDetail)
            self.assertIn("可直接重试画面理解", controller.analysisStatus)

    def test_retry_vision_preserves_local_analysis_and_invalidates_old_story(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            project = root / "projects" / "vision-retry"
            analysis = project / "analysis"
            keyframes = analysis / "keyframes"
            keyframes.mkdir(parents=True)
            (keyframes / "scene_0001.jpg").write_bytes(b"frame")
            transcript = analysis / "transcript.json"
            transcript.write_text('{"kept": true}', encoding="utf-8")
            events_file = analysis / "events.json"
            events_file.write_text(
                json.dumps(
                    {
                        "events": [
                            {
                                "id": 1,
                                "start": 0,
                                "end": 3,
                                "keyframe": "keyframes/scene_0001.jpg",
                                "visual_description": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (project / "script").mkdir()
            (project / "script" / "story.json").write_text("{}", encoding="utf-8")
            project_file = project / "project.json"
            project_file.write_text(
                json.dumps(
                    {
                        "name": "vision-retry",
                        "project_type": "video",
                        "analysis_state": "vision_failed",
                        "stage": "analysis_partial",
                        "settings": {},
                        "artifacts": {"story": "script/story.json"},
                        "warnings": {"vision": "temporary failure"},
                    }
                ),
                encoding="utf-8",
            )
            controller = self._controller(root)
            controller.openProject(str(project_file))
            controller._layered_analysis_enabled = False

            def fake_describe(path, *_args):  # type: ignore[no-untyped-def]
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["events"][0]["visual_description"] = "人物站在扶梯入口。"
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                return payload

            class ImmediateThread:
                def __init__(self, target, **_kwargs):  # type: ignore[no-untyped-def]
                    self.target = target

                def start(self) -> None:
                    self.target()

            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False), patch(
                "src.app_controller.describe_event_keyframes", side_effect=fake_describe
            ), patch("src.app_controller.threading.Thread", ImmediateThread):
                controller.retryVisionUnderstanding()

            saved = json.loads(project_file.read_text(encoding="utf-8"))
            self.assertEqual(transcript.read_text(encoding="utf-8"), '{"kept": true}')
            self.assertEqual(saved["analysis_state"], "complete")
            self.assertNotIn("vision", saved.get("warnings", {}))
            self.assertIn("story", saved["artifact_state"]["invalidated"])
            self.assertTrue(controller.analysisComplete)
            self.assertFalse(controller.analysisNeedsVisionRetry)

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
