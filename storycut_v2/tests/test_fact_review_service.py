from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.fact_review_service import review_story_facts


class FactReviewServiceTests(unittest.TestCase):
    def test_review_is_advisory_normalized_and_saved(self) -> None:
        response_payload = {
            "summary_zh": "一处数字缺少原片依据。",
            "issues": [
                {
                    "severity": "medium",
                    "category": "number_unit",
                    "narration_ids": [1, 99],
                    "event_ids": [1],
                    "claim_en": "The motor produces exactly 500 watts.",
                    "reason_zh": "原片没有清晰显示功率数值。",
                    "suggestion_en": "The motor provides the power she needs.",
                }
            ],
        }

        class FakeCompletions:
            def __init__(self) -> None:
                self.calls = 0
                self.kwargs = {}

            def create(self, **kwargs):  # type: ignore[no-untyped-def]
                self.calls += 1
                self.kwargs = kwargs
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(response_payload)))]
                )

        completions = FakeCompletions()
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            events = root / "analysis" / "events.json"
            story = root / "script" / "story.json"
            output = root / "script" / "fact_review.json"
            events.parent.mkdir()
            story.parent.mkdir()
            events.write_text(
                json.dumps(
                    {
                        "events": [
                            {
                                "id": 1,
                                "transcript": "她启动了机器。",
                                "visual_description": "人物拉动机器启动绳。",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            story.write_text(
                json.dumps(
                    {
                        "narration": [
                            {
                                "id": 1,
                                "event_ids": [1],
                                "text_en": "The motor produces exactly 500 watts.",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False), patch(
                "openai.OpenAI", return_value=client
            ):
                report = review_story_facts(
                    events,
                    story,
                    output,
                    {
                        "shared": {"env_file": ".missing"},
                        "story": {"model": "story-model", "editor_model": "editor-model"},
                        "fact_review": {"model": ""},
                    },
                    root,
                )

            self.assertEqual(completions.calls, 1)
            self.assertEqual(completions.kwargs["model"], "editor-model")
            self.assertEqual(report["status"], "warning")
            self.assertEqual(report["medium_count"], 1)
            self.assertEqual(report["issues"][0]["narration_ids"], [1])
            self.assertFalse(report["stale"])
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["issue_count"], 1)


if __name__ == "__main__":
    unittest.main()
