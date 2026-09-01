from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.layered_analysis_service import _chunk_events, analyze_layered_structure


class LayeredAnalysisServiceTests(unittest.TestCase):
    def test_single_chunk_skips_redundant_synthesis_request(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            events_file = root / "events.json"
            output_file = root / "layered_structure.json"
            events_file.write_text(
                json.dumps(
                    {
                        "content_mode": "speech",
                        "events": [
                            {"id": 1, "start": 0, "end": 20, "transcript": "Fact"},
                            {"id": 2, "start": 20, "end": 40, "transcript": "Result"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            calls: list[str] = []

            def fake_chat(*args, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(str(args[-1]))
                return {
                    "summary_zh": "短片摘要",
                    "progression_zh": "事实发展到结果",
                    "highlight_event_ids": [1],
                    "turning_point_event_ids": [2],
                }

            with patch(
                "src.layered_analysis_service.api_configuration",
                return_value={"api_key": "test", "base_url": ""},
            ), patch("openai.OpenAI", return_value=object()), patch(
                "src.layered_analysis_service._chat_json", side_effect=fake_chat
            ):
                result = analyze_layered_structure(
                    events_file,
                    output_file,
                    {"story": {"model": "model"}, "layered_analysis": {}},
                    root,
                    lambda *_args: None,
                )

            self.assertEqual(len(calls), 1)
            self.assertEqual(result["chunk_count"], 1)
            self.assertEqual(result["recommended_highlight_event_ids"], [1, 2])
            self.assertEqual(result["workflow"], "automatic_layered_analysis_v2")

    def test_chunks_respect_time_and_event_limits(self) -> None:
        events = [
            {"id": index + 1, "start": index * 20, "end": index * 20 + 10}
            for index in range(12)
        ]
        chunks = _chunk_events(events, target_duration=60, max_events=4, max_chunks=10)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 4 for chunk in chunks))
        self.assertEqual(
            [item["id"] for chunk in chunks for item in chunk],
            list(range(1, 13)),
        )
        limited = _chunk_events(events, target_duration=60, max_events=4, max_chunks=2)
        self.assertEqual(len(limited), 2)
        self.assertEqual(
            [item["id"] for chunk in limited for item in chunk],
            list(range(1, 13)),
        )

    def test_two_level_analysis_saves_chapters_and_global_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            events_file = root / "events.json"
            output_file = root / "layered_structure.json"
            events = [
                {
                    "id": index + 1,
                    "start": index * 10,
                    "end": index * 10 + 8,
                    "transcript": f"Fact {index + 1}",
                    "visual_description": f"Visible change {index + 1}",
                }
                for index in range(15)
            ]
            events_file.write_text(
                json.dumps({"content_mode": "speech", "events": events}),
                encoding="utf-8",
            )
            chapter_response = {
                "summary_zh": "章节摘要",
                "progression_zh": "状态发生变化",
                "key_facts": ["事实"],
                "highlight_event_ids": [1],
                "turning_point_event_ids": [1],
                "open_threads_zh": ["后续如何发展"],
                "continuity_zh": "同一对象延续",
            }
            synthesis_response = {
                "whole_video_summary_zh": "全片摘要",
                "central_thread_zh": "核心任务",
                "global_progression_zh": "开始、变化与结果",
                "cross_chapter_connections": [
                    {"from_chapter": 1, "to_chapter": 2, "connection_zh": "任务延续"}
                ],
                "global_turning_point_event_ids": [7],
                "recommended_highlight_event_ids": [1, 7, 15],
                "routine_or_repetitive_event_ids": [4],
                "story_angles": [
                    {"title_zh": "过程变化", "reason_zh": "有完整进展", "event_ids": [1, 7, 15]}
                ],
                "editorial_cautions_zh": ["不要虚构动机"],
            }
            responses: list[dict[str, object]] = []

            def fake_chat(*args, **kwargs):  # type: ignore[no-untyped-def]
                operation = str(args[-1])
                value = synthesis_response if "全片综合" in operation else chapter_response
                responses.append(value)
                return value

            progress: list[tuple[float, str]] = []
            with patch(
                "src.layered_analysis_service.api_configuration",
                return_value={"api_key": "test", "base_url": ""},
            ), patch("openai.OpenAI", return_value=object()), patch(
                "src.layered_analysis_service._chat_json", side_effect=fake_chat
            ):
                result = analyze_layered_structure(
                    events_file,
                    output_file,
                    {
                        "story": {"model": "test-model"},
                        "layered_analysis": {
                            "chunk_duration_sec": 60,
                            "max_events_per_chunk": 12,
                        },
                    },
                    root,
                    lambda value, status: progress.append((value, status)),
                )

            self.assertTrue(output_file.exists())
            self.assertGreater(result["chunk_count"], 1)
            self.assertEqual(len(responses), result["chunk_count"] + 1)
            self.assertEqual(result["central_thread_zh"], "核心任务")
            self.assertEqual(result["recommended_highlight_event_ids"], [1, 7, 15])
            self.assertEqual(progress[-1][0], 1.0)


if __name__ == "__main__":
    unittest.main()
