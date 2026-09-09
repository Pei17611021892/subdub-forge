from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.manuscript_media_service import (
    analyze_manuscript_assets,
    generate_manuscript_matches,
    merge_downloaded_stock_assets,
    set_manuscript_match_lock,
    stable_asset_id,
)
from src.matching_service import apply_voice_timing, build_rough_cut


class ManuscriptMediaServiceTests(unittest.TestCase):
    def test_downloaded_stock_media_keeps_source_and_license_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            media = root / "online" / "pexels-10.mp4"
            media.parent.mkdir()
            media.write_bytes(b"video")
            manifest = root / "manifest.json"
            search = root / "online_search.json"
            search.write_text(
                json.dumps(
                    {
                        "results": [
                            {
                                "narration_id": 2,
                                "query": "elephant herd communication",
                                "selected_candidate_id": "pexels-10",
                                "local_path": str(media),
                                "candidates": [
                                    {
                                        "candidate_id": "pexels-10",
                                        "provider": "Pexels",
                                        "provider_id": "10",
                                        "author": "A",
                                        "author_url": "https://example.test/a",
                                        "page_url": "https://example.test/video/10",
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = merge_downloaded_stock_assets(manifest, search)

            asset = result["assets"][0]
            self.assertEqual(asset["source_narration_id"], 2)
            self.assertEqual(asset["source_narration_ids"], [2])
            self.assertEqual(asset["provider"], "Pexels")
            self.assertEqual(asset["license_page"], "https://example.test/video/10")
            self.assertEqual(asset["id"], stable_asset_id(media))

    def test_image_analysis_persists_scene_thumbnail_and_recovers_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            image = root / "elephant herd.jpg"
            image.write_bytes(b"image")
            manifest = root / "assets" / "manifest.json"
            manifest.parent.mkdir()
            manifest.write_text(
                json.dumps(
                    {
                        "assets": [
                            {
                                "id": stable_asset_id(image),
                                "name": image.name,
                                "source_path": str(image),
                                "kind": "image",
                                "analysis_status": "pending",
                                "source": "local",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            analysis = root / "assets" / "analysis.json"
            thumbnails = root / "assets" / "thumbnails"

            def fake_thumbnail(_source, output, _timestamp, _kind, _ffmpeg):
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"thumbnail")

            with patch("src.manuscript_media_service._resolve_tool", return_value=None), patch(
                "src.manuscript_media_service._probe_image", return_value={"width": 1200, "height": 800}
            ), patch("src.manuscript_media_service._make_thumbnail", side_effect=fake_thumbnail):
                first = analyze_manuscript_assets(
                    manifest, analysis, thumbnails, {}, root, lambda _value, _status: None
                )
                second = analyze_manuscript_assets(
                    manifest, analysis, thumbnails, {}, root, lambda _value, _status: None
                )

            self.assertEqual(first["analysis_status"], "completed")
            self.assertEqual(first["scene_count"], 1)
            self.assertEqual(first["scenes"][0]["tag_profile"]["shot_scale"], "unknown")
            self.assertTrue(first["scenes"][0]["uncertain"])
            self.assertEqual(second["scenes"][0]["event_id"], 1)
            self.assertTrue((root / second["scenes"][0]["keyframe"]).exists())

    def test_global_matching_prefers_unique_scenes_and_preserves_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            storyboard = root / "storyboard.json"
            analysis = root / "analysis.json"
            matches = root / "matches.json"
            rough_cut = root / "rough_cut.json"
            storyboard.write_text(
                json.dumps(
                    {
                        "beats": [
                            {"id": 1, "narration_id": 1, "text_en": "Elephants communicate", "estimated_duration_sec": 3, "ideal_shot_zh": "象群交流", "search_queries_en": ["elephant herd communication"]},
                            {"id": 2, "narration_id": 2, "text_en": "A calf follows", "estimated_duration_sec": 3, "ideal_shot_zh": "幼象跟随", "search_queries_en": ["elephant calf walking"]},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            analysis.write_text(
                json.dumps(
                    {
                        "scenes": [
                            {"event_id": 1, "scene_id": "a-1", "asset_id": "a", "source_path": "a.mp4", "media_kind": "video", "start": 0, "end": 8, "duration_sec": 8, "keyframe": "assets/a.jpg", "tags": ["elephant", "herd", "communication"], "description": "elephant herd communication", "source_narration_id": 1},
                            {"event_id": 2, "scene_id": "b-1", "asset_id": "b", "source_path": "b.mp4", "media_kind": "video", "start": 1, "end": 9, "duration_sec": 8, "keyframe": "assets/b.jpg", "tags": ["elephant", "calf", "walking"], "description": "elephant calf walking", "source_narration_id": 2},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            first = generate_manuscript_matches(storyboard, analysis, matches)
            set_manuscript_match_lock(matches, 1, True)
            second = generate_manuscript_matches(storyboard, analysis, matches)
            apply_voice_timing(matches, 8.0)
            timeline = build_rough_cut(matches, rough_cut)

            self.assertEqual([item["selected_scene_id"] for item in first["items"]], ["a-1", "b-1"])
            self.assertTrue(second["items"][0]["locked"])
            self.assertEqual(second["items"][0]["selected_scene_id"], "a-1")
            self.assertTrue(all(clip.get("source_path") for clip in timeline["clips"]))


if __name__ == "__main__":
    unittest.main()
