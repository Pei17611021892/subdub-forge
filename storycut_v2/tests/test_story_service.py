from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.story_service import _normalize_story, generate_story_script, refresh_story_timing


class StoryServiceTests(unittest.TestCase):
    def test_visual_mode_plans_then_runs_final_editor(self) -> None:
        events = [
            {
                "id": index,
                "start": float((index - 1) * 3),
                "end": float(index * 3),
                "transcript": "",
                "visual_description": f"Visible action and state change {index}",
                "story_value": f"Progression value {index}",
                "continuity": f"Same subject continues through event {index}",
                "visual_uncertainty": "",
            }
            for index in range(1, 37)
        ]
        selected_event_groups = [
            [1, 2, 3],
            [4, 5, 6],
            [16, 17, 18],
            [19, 20, 21],
            [31, 32, 33],
            [34, 35, 36],
        ]
        selected_event_ids = [event_id for group in selected_event_groups for event_id in group]
        plan = {
            "title": "A Practical Morning",
            "angle": "围绕任务从受阻到完成的变化",
            "premise": "人物处理一项连续任务",
            "central_question": "她能否让设备恢复可用状态",
            "emotional_curve": "观察、受阻、调整、释然",
            "highlight_event_ids": selected_event_ids,
            "outline": [
                {
                    "order": index,
                    "event_ids": selected_event_groups[index - 1],
                    "stage": "attempt",
                    "visible_change": f"阶段 {index} 出现可见变化",
                    "story_function": "推进任务",
                    "subtext": "耐心来自连续动作",
                    "transition": "进入下一次调整",
                }
                for index in range(1, 7)
            ],
        }
        narration = []
        for index in range(1, 13):
            event_ids = selected_event_groups[(index - 1) % len(selected_event_groups)]
            narration.append(
                {
                    "id": index,
                    "event_ids": event_ids,
                    "text_en": (
                        "Each careful adjustment changes what the next visible effort can accomplish."
                    ),
                    "visual_query": f"阶段 {index} 的动作变化",
                    "estimated_duration_sec": 4.5,
                }
            )
        final_story = {
            "title": "A Practical Morning",
            "angle": "从动作变化中呈现任务推进",
            "hook": "The work begins before the machine is ready to move.",
            "selected_event_ids": selected_event_ids,
            "omitted_event_ids": [
                event_id for event_id in range(1, 37) if event_id not in selected_event_ids
            ],
            "outline": [
                {
                    "order": item["order"],
                    "event_ids": item["event_ids"],
                    "purpose": item["stage"],
                    "summary": item["visible_change"],
                }
                for item in plan["outline"]
            ],
            "narration": narration,
        }
        overlong_story = {**final_story, "narration": narration * 3}
        responses = iter([plan, overlong_story, final_story])
        called_models: list[str] = []

        class FakeCompletions:
            def create(self, **kwargs):  # type: ignore[no-untyped-def]
                called_models.append(str(kwargs["model"]))
                payload = next(responses)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
                )

        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))

        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            events_path = root / "analysis" / "events.json"
            story_path = root / "script" / "story.json"
            events_path.parent.mkdir(parents=True)
            events_path.write_text(
                json.dumps({"content_mode": "visual", "events": events}),
                encoding="utf-8",
            )
            config = {
                "shared": {"env_file": ".env"},
                "story": {
                    "model": "planner-model",
                    "editor_model": "editor-model",
                    "temperature": 0.55,
                },
            }
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
                with patch("openai.OpenAI", return_value=fake_client):
                    result = generate_story_script(
                        events_path,
                        story_path,
                        60,
                        config,
                        root,
                        lambda _value, _status: None,
                    )

            self.assertEqual(
                called_models,
                ["planner-model", "editor-model", "editor-model"],
            )
            self.assertEqual(result["workflow"], "visual_story_editor_v2")
            self.assertEqual(result["planner_model"], "planner-model")
            self.assertEqual(result["editor_model"], "editor-model")
            self.assertGreaterEqual(result["word_count"], 114)
            self.assertEqual(len(result["selected_event_ids"]), 18)
            self.assertTrue(story_path.exists())
            self.assertTrue(story_path.with_name("story_plan.json").exists())

    def test_short_connector_is_not_left_as_its_own_tts_unit(self) -> None:
        events = [{"id": 1}, {"id": 2}]
        result = _normalize_story(
            {
                "narration": [
                    {"event_ids": [1], "text_en": "Next,", "visual_query": "过渡"},
                    {
                        "event_ids": [2],
                        "text_en": "she changes her grip and tries from another angle.",
                        "visual_query": "调整动作",
                    },
                ]
            },
            events,
            60,
            "test-model",
        )
        self.assertEqual(len(result["narration"]), 1)
        self.assertTrue(result["narration"][0]["text_en"].startswith("Next,"))
        self.assertEqual(result["narration"][0]["event_ids"], [1, 2])

    def test_old_story_timing_is_upgraded_without_an_api_call(self) -> None:
        refreshed, changed = refresh_story_timing(
            {
                "estimated_duration_sec": 1.0,
                "word_count": 8,
                "narration": [
                    {
                        "id": 1,
                        "event_ids": [3],
                        "text_en": "She steadies the old machine and tries again.",
                        "visual_query": "人物再次尝试",
                        "estimated_duration_sec": 1.0,
                        "word_count": 8,
                    }
                ],
            }
        )

        self.assertTrue(changed)
        self.assertEqual(refreshed["timing_model"], "english_word_syllable_v3")
        self.assertGreaterEqual(refreshed["narration"][0]["estimated_duration_sec"], 3.0)
        self.assertGreaterEqual(refreshed["estimated_duration_sec"], 3.0)


if __name__ == "__main__":
    unittest.main()
