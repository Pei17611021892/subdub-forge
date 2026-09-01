from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.content_review_service import _safe_single_tts_suggestion, review_story_content


class ContentReviewServiceTests(unittest.TestCase):
    def test_direct_apply_suggestion_must_remain_one_complete_tts_unit(self) -> None:
        self.assertEqual(
            _safe_single_tts_suggestion("The locking washer resists rotation."),
            "The locking washer resists rotation.",
        )
        self.assertEqual(_safe_single_tts_suggestion("After many cycles,"), "")
        self.assertEqual(
            _safe_single_tts_suggestion("The nut turns, and it can fall off."), ""
        )

    def test_one_request_returns_fact_and_terminology_reports(self) -> None:
        response_payload = {
            "fact_review": {
                "summary_zh": "一处数字缺少依据。",
                "issues": [
                    {
                        "severity": "medium",
                        "category": "number_unit",
                        "narration_ids": [2],
                        "event_ids": [1],
                        "claim_en": "It produces exactly 500 watts.",
                        "reason_zh": "原片没有显示功率。",
                        "suggestion_en": "It supplies the needed power.",
                    }
                ],
            },
            "terminology_review": {
                "summary_zh": "同一零件出现两种译法。",
                "canonical_terms": [
                    {
                        "source_term": "锁紧垫圈",
                        "preferred_en": "locking washer",
                        "reason_zh": "符合原片结构",
                    }
                ],
                "issues": [
                    {
                        "category": "term_variant",
                        "narration_ids": [1],
                        "term": "锁紧垫圈",
                        "variants": ["lock washer", "locking washer"],
                        "reason_zh": "译法不一致。",
                        "suggestion_en": "The locking washer resists rotation.",
                    }
                ],
            },
        }

        class FakeCompletions:
            def __init__(self) -> None:
                self.calls = 0
                self.prompt = ""

            def create(self, **kwargs):  # type: ignore[no-untyped-def]
                self.calls += 1
                self.prompt = str(kwargs["messages"][0]["content"])
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(response_payload)))]
                )

        completions = FakeCompletions()
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            events = root / "analysis" / "events.json"
            story = root / "script" / "story.json"
            output = root / "script" / "content_review.json"
            events.parent.mkdir()
            story.parent.mkdir()
            events.write_text(
                json.dumps({"events": [{"id": 1, "transcript": "锁紧垫圈", "visual_description": "垫圈"}, {"id": 2, "transcript": "锁紧垫圈", "visual_description": "垫圈"}]}),
                encoding="utf-8",
            )
            story.write_text(
                json.dumps({"narration": [{"id": 1, "event_ids": [1], "text_en": "It produces exactly 500 watts."}, {"id": 2, "event_ids": [2], "text_en": "The lock washer resists rotation."}]}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False), patch(
                "openai.OpenAI", return_value=client
            ):
                report = review_story_content(
                    events,
                    story,
                    output,
                    {"shared": {"env_file": ".missing"}, "story": {"model": "story-model"}},
                    root,
                )

            self.assertEqual(completions.calls, 1)
            self.assertIn("complete independently speakable unit", completions.prompt)
            self.assertEqual(report["fact_review"]["medium_count"], 1)
            self.assertEqual(report["terminology_review"]["issue_count"], 1)
            self.assertEqual(
                report["terminology_review"]["canonical_terms"][0]["preferred_en"],
                "locking washer",
            )
            self.assertIn("fact_review", json.loads(output.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
