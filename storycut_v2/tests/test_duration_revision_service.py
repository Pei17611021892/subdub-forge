from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.duration_revision_service import propose_duration_revision


class DurationRevisionServiceTests(unittest.TestCase):
    def test_proposal_uses_measured_voice_speed_and_does_not_replace_story(self) -> None:
        response_payload = {
            "summary_zh": "删去重复铺垫，保留启动机器与清理河道的主线。",
            "removed_or_merged": ["合并两处重复的环境描写"],
            "revised_story": {
                "title": "River Work",
                "angle": "A quiet maintenance ritual",
                "hook": "The river looks calm, but the work starts before sunrise.",
                "outline": [
                    {"event_ids": [1], "purpose": "hook", "summary": "She starts the machine."}
                ],
                "narration": [
                    {
                        "event_ids": [1],
                        "text_en": " ".join(["river"] * 205) + ".",
                        "visual_query": "woman starts machine beside river",
                        "estimated_duration_sec": 84,
                    }
                ],
            },
        }
        overcompressed_payload = json.loads(json.dumps(response_payload))
        overcompressed_payload["revised_story"]["narration"][0]["text_en"] = (
            " ".join(["river"] * 170) + "."
        )
        overcompressed_payload["revised_story"]["narration"][0][
            "estimated_duration_sec"
        ] = 70
        responses = iter([overcompressed_payload, response_payload])

        class FakeCompletions:
            def create(self, **_kwargs):  # type: ignore[no-untyped-def]
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(next(responses))))])

        client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            events = root / "analysis" / "events.json"
            story = root / "script" / "story.json"
            output = root / "script" / "duration_revision_proposal.json"
            events.parent.mkdir()
            story.parent.mkdir()
            events.write_text(
                json.dumps({"events": [{"id": 1, "start": 0, "end": 20, "visual_description": "女人启动河边机器。"}]}),
                encoding="utf-8",
            )
            original = {
                "word_count": 300,
                "narration": [
                    {"id": 1, "event_ids": [1], "text_en": "word " * 300, "visual_query": "machine"}
                ],
            }
            story.write_text(json.dumps(original), encoding="utf-8")

            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False), patch(
                "openai.OpenAI", return_value=client
            ):
                proposal = propose_duration_revision(
                    events,
                    story,
                    output,
                    240.0,
                    {"shared": {"env_file": ".missing"}, "story": {"editor_model": "editor-model"}},
                    root,
                )

            self.assertEqual(json.loads(story.read_text(encoding="utf-8")), original)
            self.assertLess(proposal["projected_duration_sec"], 179)
            self.assertGreaterEqual(proposal["projected_duration_sec"], 160)
            self.assertEqual(proposal["schema_version"], 2)
            self.assertEqual(proposal["model"], "editor-model")
            self.assertEqual(proposal["revised_word_count"], 205)
            self.assertEqual(
                proposal["revised_story"]["timing_model"],
                "measured_voice_projection_v1",
            )
            self.assertAlmostEqual(
                proposal["revised_story"]["estimated_duration_sec"],
                proposal["projected_duration_sec"],
                delta=0.1,
            )
            self.assertTrue(output.exists())
            self.assertIn("重复", proposal["summary_zh"])


if __name__ == "__main__":
    unittest.main()
