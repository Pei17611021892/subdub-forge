from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.series_service import (
    build_part_events_payload,
    collapse_story_series,
    evaluate_story_preservation,
    filter_layered_structure,
    materialize_story_series,
    should_evaluate_story_series,
    story_series_evaluation_reasons,
)


class SeriesServiceTests(unittest.TestCase):
    def test_part_payload_keeps_one_neighbor_on_each_side(self) -> None:
        payload = {
            "events": [
                {"id": index, "start": index, "end": index + 1}
                for index in range(1, 8)
            ]
        }
        focused = build_part_events_payload(payload, [3, 6])
        self.assertEqual(
            [item["id"] for item in focused["events"]], [2, 3, 4, 5, 6, 7]
        )
        self.assertEqual(focused["series_selected_event_ids"], [3, 6])

    def test_layered_structure_is_scoped_to_part(self) -> None:
        scoped = filter_layered_structure(
            {
                "global_turning_point_event_ids": [2, 8],
                "recommended_highlight_event_ids": [3, 9],
                "chapters": [
                    {"title": "A", "event_ids": [1, 2, 3]},
                    {"title": "B", "event_ids": [8, 9]},
                ],
            },
            [1, 2, 3],
            {"part_index": 1, "purpose_zh": "第一机制"},
        )
        self.assertEqual(scoped["global_turning_point_event_ids"], [2])
        self.assertEqual(scoped["recommended_highlight_event_ids"], [3])
        self.assertEqual(len(scoped["chapters"]), 1)
        self.assertEqual(scoped["series_part_plan"]["part_index"], 1)

    def test_materialized_parts_are_normal_projects_with_shared_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            projects = Path(temporary_dir) / "projects"
            root = projects / "v2-0902"
            for child in ("analysis/keyframes", "script", "cache"):
                (root / child).mkdir(parents=True, exist_ok=True)
            (root / "analysis" / "keyframes" / "one.jpg").write_bytes(b"frame")
            (root / "analysis" / "events.json").write_text(
                json.dumps(
                    {
                        "content_mode": "speech",
                        "events": [
                            {"id": i, "start": i * 5, "end": i * 5 + 4, "keyframe": "keyframes/one.jpg"}
                            for i in range(1, 9)
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (root / "analysis" / "layered_structure.json").write_text(
                json.dumps({"global_turning_point_event_ids": [2, 7]}),
                encoding="utf-8",
            )
            project_file = root / "project.json"
            project_file.write_text(
                json.dumps(
                    {
                        "name": "v2-0902",
                        "source_video": "D:/source.mp4",
                        "stage": "scripted",
                        "settings": {},
                        "artifacts": {},
                    }
                ),
                encoding="utf-8",
            )
            result = materialize_story_series(
                project_file,
                {"coverage_score": 0.6, "reason_zh": "两条主线"},
                [
                    {"plan": {"title_zh": "前半", "event_ids": [2, 3]}, "story": {"narration": []}},
                    {"plan": {"title_zh": "后半", "event_ids": [6, 7]}, "story": {"narration": []}},
                ],
            )
            child = projects / result["members"][1]["directory"]
            child_project = json.loads((child / "project.json").read_text(encoding="utf-8"))
            child_events = json.loads((child / "analysis" / "events.json").read_text(encoding="utf-8"))

            self.assertEqual(result["part_count"], 2)
            self.assertEqual(child_project["series"]["part_index"], 2)
            self.assertEqual(child_project["source_video"], "D:/source.mp4")
            self.assertTrue(Path(child_events["events"][0]["keyframe"]).is_absolute())
            self.assertTrue((root / "analysis" / "events_full.json").exists())

            archived = collapse_story_series(project_file)
            collapsed = json.loads(project_file.read_text(encoding="utf-8"))
            restored = json.loads((root / "analysis" / "events.json").read_text(encoding="utf-8"))
            self.assertEqual(archived, 1)
            self.assertNotIn("series", collapsed)
            self.assertEqual(len(restored["events"]), 8)
            self.assertFalse(child.exists())

    def test_only_long_or_dense_sources_need_preservation_evaluation(self) -> None:
        short = {"events": [{"id": 1, "start": 0, "end": 120}]}
        long_source = {"events": [{"id": 1, "start": 0, "end": 360}]}
        dense = {
            "events": [
                {"id": index + 1, "start": index, "end": index + 1}
                for index in range(90)
            ]
        }
        config = {"series": {"auto_split": True}}

        self.assertFalse(should_evaluate_story_series(short, config))
        self.assertTrue(should_evaluate_story_series(long_source, config))
        self.assertTrue(should_evaluate_story_series(dense, config))
        self.assertFalse(
            should_evaluate_story_series(long_source, {"series": {"auto_split": False}})
        )

    def test_short_dense_science_transcript_triggers_without_length_threshold(self) -> None:
        events = []
        for index in range(1, 31):
            events.append(
                {
                    "id": index,
                    "start": (index - 1) * 4,
                    "end": index * 4,
                    "speech_duration": 3.8,
                    "transcript_indices": [index],
                    "transcript": "高密度科普内容包含机制参数证据因果变化以及限制条件还要解释实验结果适用范围失败模式和例外情况",
                }
            )
        reasons = story_series_evaluation_reasons(
            {"duration_sec": 120, "events": events},
            {"series": {"auto_split": True}, "story": {"planning_words_per_second": 1.35}},
        )
        codes = {item["code"] for item in reasons}
        self.assertIn("transcript_compression", codes)
        self.assertIn("dense_speech", codes)
        self.assertNotIn("source_duration", codes)
        self.assertNotIn("event_count", codes)

    def test_technical_and_layered_complexity_are_independent_signals(self) -> None:
        events = [
            {
                "id": index,
                "start": index * 3,
                "end": index * 3 + 2,
                "technical_visual": {
                    "type": "chart" if index <= 8 else "none",
                    "facts": ["parameter"] if index <= 8 else [],
                },
            }
            for index in range(1, 25)
        ]
        layered = {
            "chapters": [
                {"key_facts": [f"fact {chapter}-{index}" for index in range(10)]}
                for chapter in range(2)
            ],
            "global_turning_point_event_ids": [2, 8, 16],
        }
        reasons = story_series_evaluation_reasons(
            {"duration_sec": 90, "events": events},
            {"series": {"auto_split": True}},
            layered=layered,
        )
        codes = {item["code"] for item in reasons}
        self.assertIn("technical_evidence", codes)
        self.assertIn("layered_complexity", codes)

    def test_near_limit_draft_with_low_evidence_coverage_triggers(self) -> None:
        events = [
            {
                "id": index,
                "start": index * 3,
                "end": index * 3 + 2,
                "visual_description": "A detailed mechanism visibly changes through several supported stages.",
            }
            for index in range(1, 31)
        ]
        story = {
            "estimated_duration_sec": 160,
            "narration": [
                {"id": 1, "event_ids": [1, 2, 3], "text_en": "A concise draft."}
            ],
        }
        reasons = story_series_evaluation_reasons(
            {"duration_sec": 90, "events": events},
            {"series": {"auto_split": True}},
            story=story,
        )
        self.assertIn(
            "draft_capacity_pressure", {item["code"] for item in reasons}
        )

    def test_evaluation_normalizes_smallest_supported_series_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            events_file = root / "events.json"
            story_file = root / "story.json"
            layered_file = root / "layered.json"
            events = [
                {"id": index, "start": index * 10, "end": index * 10 + 8, "transcript": f"事实 {index}"}
                for index in range(1, 13)
            ]
            events_file.write_text(json.dumps({"events": events}), encoding="utf-8")
            story_file.write_text(
                json.dumps(
                    {
                        "word_count": 230,
                        "estimated_duration_sec": 172,
                        "narration": [{"id": 1, "event_ids": [1], "text_en": "A short draft."}],
                    }
                ),
                encoding="utf-8",
            )
            layered_file.write_text(
                json.dumps({"recommended_highlight_event_ids": [1, 6, 7, 12]}),
                encoding="utf-8",
            )
            response = {
                "single_part_acceptable": False,
                "coverage_score": 0.62,
                "reason_zh": "两个独立机制无法在一集讲清",
                "missing_essential_points_zh": ["第二个机制"],
                "recommended_part_count": 2,
                "parts": [
                    {"event_ids": [1, 2, 3, 99], "title_zh": "机制一"},
                    {"event_ids": [7, 8, 12], "title_zh": "机制二"},
                ],
            }
            with patch.dict(
                "sys.modules", {"openai": SimpleNamespace(OpenAI=lambda **_kwargs: object())}
            ), patch(
                "src.series_service.api_configuration",
                return_value={"api_key": "test", "base_url": ""},
            ), patch(
                "src.series_service._chat_json", return_value=response
            ):
                result = evaluate_story_preservation(
                    events_file,
                    story_file,
                    layered_file,
                    {
                        "story": {"model": "model"},
                        "series": {"max_parts": 4},
                    },
                    root,
                )

            self.assertFalse(result["single_part_acceptable"])
            self.assertEqual(result["recommended_part_count"], 2)
            self.assertEqual(result["parts"][0]["event_ids"], [1, 2, 3])
            self.assertEqual(result["parts"][1]["event_ids"], [6, 7, 8, 12])
            self.assertEqual(result["model"], "model")


if __name__ == "__main__":
    unittest.main()
