from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.manuscript_service import (
    generate_manuscript_storyboard,
    split_overlong_manuscript_result,
)


class _FakeCompletions:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(self.payload, ensure_ascii=False)
                    )
                )
            ]
        )


class ManuscriptServiceTests(unittest.TestCase):
    def test_overlong_result_is_balanced_into_short_safe_parts(self) -> None:
        narration = [
            {
                "id": index,
                "text_en": f"Complete narration sentence number {index}.",
                "word_count": 5,
                "estimated_duration_sec": 10.5,
            }
            for index in range(1, 21)
        ]
        beats = [
            {
                "id": index,
                "narration_id": index,
                "text_en": narration[index - 1]["text_en"],
                "source_excerpt_zh": f"原文第{index}句。",
                "section_title_zh": "前半" if index <= 10 else "后半",
                "estimated_duration_sec": 10.5,
            }
            for index in range(1, 21)
        ]
        parts = split_overlong_manuscript_result(
            {
                "story": {
                    "title": "Long story",
                    "angle": "完整解释主题",
                    "estimated_duration_sec": 210,
                    "word_count": 100,
                    "narration": narration,
                },
                "storyboard": {"beats": beats},
            }
        )

        self.assertEqual(len(parts), 2)
        self.assertEqual([len(item["story"]["narration"]) for item in parts], [10, 10])
        self.assertTrue(
            all(item["story"]["estimated_duration_sec"] < 180 for item in parts)
        )
        self.assertEqual(parts[0]["plan"]["title_zh"], "前半")
        self.assertEqual(parts[1]["plan"]["title_zh"], "后半")
        self.assertEqual(parts[1]["story"]["narration"][0]["id"], 1)

    def test_one_request_creates_story_storyboard_and_review_events(self) -> None:
        payload = {
            "title_zh": "大象的低语",
            "title_en": "The Quiet Language of Elephants",
            "summary_zh": "从低频声音解释大象如何远距离交流",
            "narrative_strategy": "science_explainer",
            "narrative_strategy_reason_zh": "围绕现象、机制和结论展开",
            "outline": [
                {"purpose": "hook", "summary_zh": "提出听不见的交流"},
                {"purpose": "conclusion", "summary_zh": "说明语言定义仍需谨慎"},
            ],
            "beats": [
                {
                    "source_excerpt_zh": "大象会使用低频声音",
                    "text_en": "Elephants can communicate with rumbles below the range of human hearing.",
                    "shot_role": "mechanism",
                    "media_kind": "diagram",
                    "ideal_shot_zh": "大象特写叠加低频声波示意",
                    "must_show_zh": ["大象", "低频波形"],
                    "acceptable_fallbacks_zh": ["象群远景配声波动画"],
                    "avoid_zh": ["用吼叫镜头冒充低频交流"],
                    "search_queries_en": [
                        "elephant rumble communication",
                        "elephant herd listening",
                    ],
                    "editor_note_zh": "不可见机制优先使用图形解释",
                },
                {
                    "source_excerpt_zh": "是否属于语言仍有争议",
                    "text_en": "Whether this system counts as language depends on how language is defined.",
                    "shot_role": "payoff",
                    "media_kind": "video",
                    "ideal_shot_zh": "象群在自然环境中缓慢离开",
                    "must_show_zh": ["自然环境中的象群"],
                    "acceptable_fallbacks_zh": ["单只大象远景"],
                    "avoid_zh": ["马戏表演"],
                    "search_queries_en": ["elephant herd walking savannah"],
                    "editor_note_zh": "",
                },
            ],
        }
        fake_completions = _FakeCompletions(payload)
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=fake_completions)
        )

        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            manuscript = root / "source" / "manuscript.txt"
            manuscript.parent.mkdir()
            manuscript.write_text(
                "大象会使用低频声音。是否属于语言仍有争议。",
                encoding="utf-8",
            )
            story_file = root / "script" / "story.json"
            storyboard_file = root / "storyboard" / "storyboard.json"
            events_file = root / "analysis" / "events.json"
            progress: list[tuple[float, str]] = []

            with patch.dict(
                os.environ,
                {"OPENAI_API_KEY": "test-key"},
                clear=False,
            ), patch("openai.OpenAI", return_value=fake_client) as openai_factory:
                result = generate_manuscript_storyboard(
                    manuscript,
                    story_file,
                    storyboard_file,
                    events_file,
                    90,
                    {"story": {"model": "test-model"}},
                    root,
                    lambda value, status: progress.append((value, status)),
                    narrative_strategy="auto",
                    planning_words_per_second=1.4,
                )

            self.assertEqual(len(fake_completions.calls), 1)
            self.assertEqual(openai_factory.call_args.kwargs["timeout"], 240.0)
            self.assertEqual(openai_factory.call_args.kwargs["max_retries"], 1)
            request_prompt = fake_completions.calls[0]["messages"][0]["content"]
            self.assertIn("roughly 11 narration beats", request_prompt)
            self.assertEqual(len(result["story"]["narration"]), 2)
            self.assertEqual(result["story"]["content_mode"], "manuscript")
            self.assertEqual(result["storyboard"]["shot_segment_count"], 2)
            self.assertEqual(
                result["storyboard"]["beats"][0]["match_status"], "missing"
            )
            self.assertEqual(
                result["storyboard"]["beats"][0]["search_queries_en"][0],
                "elephant rumble communication",
            )
            self.assertTrue(story_file.exists())
            self.assertTrue(storyboard_file.exists())
            source_events = json.loads(events_file.read_text(encoding="utf-8"))
            self.assertEqual(source_events["content_mode"], "manuscript")
            self.assertEqual(len(source_events["events"]), 2)
            self.assertEqual(progress[-1][0], 1.0)


if __name__ == "__main__":
    unittest.main()
