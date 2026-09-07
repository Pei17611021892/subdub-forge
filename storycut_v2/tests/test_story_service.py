from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.story_service import (
    _build_speech_story_prompt,
    _chat_json,
    _normalize_story,
    _problematic_tts_unit_ids,
    _trim_narration_to_duration,
    calibrate_story_timing_from_voice,
    generate_story_script,
    narrative_strategy_options,
    normalize_story_after_text_edit,
    refresh_story_timing,
)


class StoryServiceTests(unittest.TestCase):
    def test_narrative_strategy_auto_and_none_keep_one_request_workflow(self) -> None:
        options = {item["value"] for item in narrative_strategy_options()}
        self.assertIn("auto", options)
        self.assertIn("none", options)
        self.assertIn("science_explainer", options)

        auto_prompt = _build_speech_story_prompt([], 180, 300, "existing", "auto")
        unchanged_prompt = _build_speech_story_prompt([], 180, 300, "existing", "none")
        self.assertIn("First identify the dominant subject", auto_prompt)
        self.assertNotIn("NARRATIVE STRATEGY", unchanged_prompt)

    def test_reasoning_effort_steps_down_until_provider_accepts_it(self) -> None:
        attempts: list[str] = []

        class CompatibilityError(Exception):
            status_code = 400

        class FakeCompletions:
            def create(self, **kwargs):  # type: ignore[no-untyped-def]
                effort = str(kwargs.get("reasoning_effort", ""))
                attempts.append(effort)
                if effort != "low":
                    raise CompatibilityError("unsupported reasoning_effort")
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))]
                )

        client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
        result = _chat_json(
            client,
            "reasoning-model",
            "Return JSON.",
            0.5,
            None,
            "测试推理档位",
            reasoning_effort="xhigh",
        )

        self.assertEqual(attempts, ["xhigh", "high", "medium", "low"])
        self.assertTrue(result["ok"])

    def test_real_voice_replaces_estimate_and_sync_refines_each_line(self) -> None:
        story = {
            "timing_model": "english_word_syllable_v3",
            "word_count": 6,
            "estimated_duration_sec": 6,
            "narration": [
                {"id": 1, "text_en": "First short line.", "word_count": 3, "estimated_duration_sec": 2},
                {"id": 2, "text_en": "Second longer line.", "word_count": 3, "estimated_duration_sec": 4},
            ],
        }
        calibrated = calibrate_story_timing_from_voice(story, 12)

        self.assertEqual(calibrated["estimated_duration_before_voice_sec"], 6)
        self.assertEqual(calibrated["estimated_duration_sec"], 12)
        self.assertEqual(calibrated["timing_model"], "measured_voice_projection_v1")
        self.assertEqual(
            [item["estimated_duration_sec"] for item in calibrated["narration"]],
            [4, 8],
        )

        synced = calibrate_story_timing_from_voice(
            calibrated,
            12,
            [
                {"start": 0.3, "end": 4.5, "text": "First short line."},
                {"start": 5.0, "end": 11.8, "text": "Second longer line."},
            ],
        )
        self.assertEqual(synced["voice_timing_source"], "synced_srt")
        self.assertEqual(
            [item["estimated_duration_sec"] for item in synced["narration"]],
            [5, 7],
        )

    def test_single_short_fallback_keeps_hook_turning_point_and_ending(self) -> None:
        narration = [
            {
                "id": index,
                "event_ids": [index],
                "text_en": f"Complete sentence {index}.",
                "word_count": 20,
                "estimated_duration_sec": 30,
            }
            for index in range(1, 9)
        ]
        trimmed = _trim_narration_to_duration(
            {
                "narration": narration,
                "outline": [
                    {"order": index, "event_ids": [index], "summary": str(index)}
                    for index in range(1, 9)
                ],
                "selected_event_ids": list(range(1, 9)),
                "omitted_event_ids": [],
            },
            {
                "global_turning_point_event_ids": [5],
                "routine_or_repetitive_event_ids": [2, 3, 4],
            },
            175,
        )

        kept = [item["event_ids"][0] for item in trimmed["narration"]]
        self.assertIn(1, kept)
        self.assertIn(5, kept)
        self.assertIn(8, kept)
        self.assertLess(trimmed["estimated_duration_sec"], 180)
        self.assertTrue(trimmed["forced_single_short_trim"])

    def test_layered_structure_is_optional_prompt_context(self) -> None:
        default_prompt = _build_speech_story_prompt([], 180, 300, "existing", "none")
        layered_prompt = _build_speech_story_prompt(
            [],
            180,
            300,
            "existing",
            "none",
            {
                "central_thread_zh": "跨章节核心任务",
                "recommended_highlight_event_ids": [2, 8],
            },
        )
        self.assertNotIn("OPTIONAL LONG-VIDEO LAYERED ANALYSIS", default_prompt)
        self.assertIn("OPTIONAL LONG-VIDEO LAYERED ANALYSIS", layered_prompt)
        self.assertIn("跨章节核心任务", layered_prompt)
        self.assertIn("not a checklist", layered_prompt)
        self.assertIn("only 1-4 event IDs", layered_prompt)

    def test_planning_voice_rate_calibrates_story_duration(self) -> None:
        words = " ".join(f"word{index}" for index in range(300)) + "."
        result = _normalize_story(
            {
                "narration": [
                    {
                        "event_ids": [1],
                        "text_en": words,
                        "visual_query": "测试画面",
                    }
                ]
            },
            [{"id": 1}],
            180,
            "test-model",
            planning_words_per_second=1.339,
        )

        self.assertEqual(result["timing_model"], "planning_voice_rate_v1")
        self.assertEqual(result["planning_words_per_second"], 1.339)
        self.assertAlmostEqual(result["estimated_duration_sec"], 224.05, delta=0.1)

        edited = normalize_story_after_text_edit(result)
        self.assertEqual(edited["timing_model"], "planning_voice_rate_v1")
        self.assertAlmostEqual(
            edited["estimated_duration_sec"],
            edited["word_count"] / 1.339,
            delta=0.1,
        )

    def test_gpt_sovits_fragment_check_catches_dependent_and_short_units(self) -> None:
        story = {
            "narration": [
                {"id": 1, "text_en": "A clamped joint looks perfectly still,"},
                {"id": 2, "text_en": "After millions of cycles,"},
                {"id": 3, "text_en": "the nut starts turning,"},
                {"id": 4, "text_en": "The next idea stops rotation itself:"},
                {"id": 5, "text_en": "The nut can eventually fall off."},
                {"id": 6, "text_en": "Once machines start running."},
                {"id": 7, "text_en": "Motors spin. Gears hit. Structures flex."},
            ]
        }

        self.assertEqual(_problematic_tts_unit_ids(story), [2, 3, 4, 6, 7])

    def test_speech_mode_rewrites_an_overlong_draft_before_failing(self) -> None:
        events = [
            {
                "id": 1,
                "start": 0,
                "end": 30,
                "transcript": "A locking fastener prevents rotation under vibration.",
                "visual_description": "A bolt and locking washer are demonstrated.",
            }
        ]
        overlong = {
            "title": "Fasteners",
            "angle": "解释防松原理",
            "hook": "A bolt can loosen under vibration.",
            "outline": [{"event_ids": [1], "purpose": "explain", "summary": "原理"}],
            "narration": [
                {
                    "event_ids": [1],
                    "text_en": " ".join(["mechanism"] * 500) + ".",
                    "visual_query": "螺栓防松结构",
                    "estimated_duration_sec": 205,
                }
            ],
        }
        revised = {
            "title": "Fasteners",
            "angle": "解释防松原理",
            "hook": "Vibration keeps testing every threaded joint.",
            "outline": [{"event_ids": [1], "purpose": "explain", "summary": "原理"}],
            "narration": [
                {
                    "event_ids": [1],
                    "text_en": "A locking washer adds resistance so vibration cannot rotate the nut freely.",
                    "visual_query": "锁紧垫圈阻止螺母旋转",
                    "estimated_duration_sec": 5,
                }
            ],
        }
        responses = iter([overlong, revised])
        called_models: list[str] = []

        class FakeCompletions:
            def create(self, **kwargs):  # type: ignore[no-untyped-def]
                called_models.append(str(kwargs["model"]))
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content=json.dumps(next(responses)))
                        )
                    ]
                )

        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            events_path = root / "analysis" / "events.json"
            story_path = root / "script" / "story.json"
            events_path.parent.mkdir(parents=True)
            events_path.write_text(
                json.dumps({"content_mode": "speech", "events": events}), encoding="utf-8"
            )
            config = {
                "shared": {"env_file": ".env"},
                "story": {
                    "model": "story-model",
                    "editor_model": "editor-model",
                    "temperature": 0.55,
                },
            }
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False), patch(
                "openai.OpenAI", return_value=fake_client
            ):
                result = generate_story_script(
                    events_path,
                    story_path,
                    180,
                    config,
                    root,
                    lambda _value, _status: None,
                )
            story_saved = story_path.exists()

        self.assertEqual(called_models, ["story-model", "editor-model"])
        self.assertEqual(result["workflow"], "speech_story_editor_v2")
        self.assertLess(result["estimated_duration_sec"], 179)
        self.assertTrue(story_saved)

    def test_speech_mode_rewrites_broken_gpt_sovits_units(self) -> None:
        events = [{"id": 1, "start": 0, "end": 10, "transcript": "持续震动会让螺母松动。"}]
        broken = {
            "title": "Fasteners",
            "outline": [{"event_ids": [1], "purpose": "explain", "summary": "原理"}],
            "narration": [
                {
                    "event_ids": [1],
                    "text_en": "After millions of cycles, the nut can begin to turn.",
                    "visual_query": "震动中的螺母",
                }
            ],
        }
        revised = {
            "title": "Fasteners",
            "outline": [{"event_ids": [1], "purpose": "explain", "summary": "原理"}],
            "narration": [
                {
                    "event_ids": [1],
                    "text_en": "Millions of vibration cycles can gradually overcome the friction holding the nut.",
                    "visual_query": "震动中的螺母",
                }
            ],
        }
        responses = iter([broken, revised])
        prompts: list[str] = []

        class FakeCompletions:
            def create(self, **kwargs):  # type: ignore[no-untyped-def]
                prompts.append(str(kwargs["messages"][0]["content"]))
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(next(responses))))]
                )

        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            events_path = root / "analysis" / "events.json"
            story_path = root / "script" / "story.json"
            events_path.parent.mkdir(parents=True)
            events_path.write_text(
                json.dumps({"content_mode": "speech", "events": events}), encoding="utf-8"
            )
            config = {"shared": {"env_file": ".env"}, "story": {"model": "model"}}
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False), patch(
                "openai.OpenAI", return_value=fake_client
            ):
                result = generate_story_script(
                    events_path, story_path, 180, config, root, lambda *_args: None
                )

        self.assertEqual(len(prompts), 2)
        self.assertIn("broken GPT-SoVITS units", prompts[1])
        self.assertEqual(_problematic_tts_unit_ids(result), [])

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

    def test_short_connector_remains_visible_for_tts_quality_validation(self) -> None:
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
        self.assertEqual(len(result["narration"]), 2)
        self.assertEqual(result["narration"][0]["text_en"], "Next,")
        self.assertEqual(_problematic_tts_unit_ids(result), [1])

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

    def test_measured_voice_projection_timing_is_preserved_on_reopen(self) -> None:
        story = {
            "timing_model": "measured_voice_projection_v1",
            "estimated_duration_sec": 165.0,
            "narration": [
                {
                    "id": 1,
                    "text_en": "A measured line.",
                    "estimated_duration_sec": 165.0,
                }
            ],
        }

        refreshed, changed = refresh_story_timing(story)

        self.assertFalse(changed)
        self.assertEqual(refreshed["estimated_duration_sec"], 165.0)


if __name__ == "__main__":
    unittest.main()
