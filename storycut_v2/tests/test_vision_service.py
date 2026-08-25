from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.vision_service import describe_event_keyframes


class VisionServiceTests(unittest.TestCase):
    def _project(self, root: Path) -> tuple[Path, Path]:
        analysis = root / "analysis"
        frame = analysis / "keyframes" / "scene_0001.jpg"
        frame.parent.mkdir(parents=True)
        frame.write_bytes(b"test jpeg bytes")
        events = analysis / "events.json"
        events.write_text(
            json.dumps(
                {
                    "content_mode": "speech",
                    "events": [
                        {
                            "id": 1,
                            "start": 0.0,
                            "end": 3.0,
                            "keyframe": "keyframes/scene_0001.jpg",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        source = root / "source.mp4"
        source.write_bytes(b"source placeholder")
        return events, source

    def test_technical_fields_reuse_existing_vision_request(self) -> None:
        response_payload = [
            {
                "id": 1,
                "description": "画面显示一张温度曲线图。",
                "screen_text": [
                    {"text": "Temperature (°C)", "role": "label", "confidence": "high"}
                ],
                "technical_visual": {
                    "type": "chart",
                    "summary": "温度曲线随时间上升",
                    "facts": ["纵轴单位为 °C"],
                    "importance": 2,
                    "needs_high_detail_review": False,
                },
            }
        ]
        class FakeCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs):  # type: ignore[no-untyped-def]
                self.calls += 1
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content=json.dumps(response_payload))
                        )
                    ]
                )

        completions = FakeCompletions()
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            events, _source = self._project(root)
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False), patch(
                "openai.OpenAI", return_value=client
            ):
                result = describe_event_keyframes(
                    events,
                    {"shared": {"env_file": ".missing"}, "vision": {"batch_size": 4}},
                    root,
                    lambda _value, _status: None,
                )

            self.assertEqual(completions.calls, 1)
            self.assertEqual(result["visual_schema_version"], 2)
            self.assertEqual(result["technical_visual_event_count"], 1)
            self.assertEqual(result["high_detail_review_count"], 0)
            self.assertEqual(result["events"][0]["technical_visual"]["type"], "chart")
            self.assertEqual(result["events"][0]["screen_text"][0]["text"], "Temperature (°C)")

    def test_high_detail_review_is_conditional_and_batched(self) -> None:
        responses = iter(
            [
                [
                    {
                        "id": 1,
                        "description": "低清画面中存在重要公式。",
                        "screen_text": [],
                        "technical_visual": {
                            "type": "formula",
                            "summary": "公式字符尚不清晰",
                            "facts": [],
                            "importance": 3,
                            "needs_high_detail_review": True,
                            "review_reason": "公式影响解说事实",
                        },
                    }
                ],
                [
                    {
                        "id": 1,
                        "screen_text": [
                            {"text": "E = mc²", "role": "value", "confidence": "high"}
                        ],
                        "technical_visual": {
                            "type": "formula",
                            "summary": "画面清楚显示质能方程",
                            "facts": ["公式为 E = mc²"],
                            "importance": 3,
                            "needs_high_detail_review": False,
                            "high_detail_reviewed": True,
                        },
                    }
                ],
            ]
        )

        class FakeCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **_kwargs):  # type: ignore[no-untyped-def]
                self.calls += 1
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content=json.dumps(next(responses)))
                        )
                    ]
                )

        fake_completions = FakeCompletions()
        client = SimpleNamespace(chat=SimpleNamespace(completions=fake_completions))
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            events, source = self._project(root)
            high_frame = root / "analysis" / "high_detail" / "event_0001.jpg"
            high_frame.parent.mkdir()
            high_frame.write_bytes(b"high detail jpeg")
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False), patch(
                "openai.OpenAI", return_value=client
            ), patch(
                "src.vision_service._extract_high_detail_frames",
                side_effect=lambda selected, *_args: [(selected[0], high_frame)],
            ):
                result = describe_event_keyframes(
                    events,
                    {
                        "shared": {"env_file": ".missing"},
                        "vision": {
                            "batch_size": 4,
                            "high_detail_review_enabled": True,
                            "max_high_detail_events": 6,
                        },
                    },
                    root,
                    lambda _value, _status: None,
                    source,
                )

            self.assertEqual(fake_completions.calls, 2)
            self.assertEqual(result["high_detail_review_count"], 1)
            event = result["events"][0]
            self.assertTrue(event["technical_visual"]["high_detail_reviewed"])
            self.assertEqual(event["screen_text"][0]["text"], "E = mc²")


if __name__ == "__main__":
    unittest.main()
