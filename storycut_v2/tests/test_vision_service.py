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

    def test_missing_event_in_vision_response_is_a_failure(self) -> None:
        class FakeCompletions:
            def create(self, **_kwargs):  # type: ignore[no-untyped-def]
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content=json.dumps([]))
                        )
                    ]
                )

        client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            events, _source = self._project(root)
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False), patch(
                "openai.OpenAI", return_value=client
            ):
                with self.assertRaisesRegex(RuntimeError, "视觉接口未返回"):
                    describe_event_keyframes(
                        events,
                        {"shared": {"env_file": ".missing"}, "vision": {"batch_size": 4}},
                        root,
                        lambda _value, _status: None,
                    )

    def test_content_policy_failure_is_isolated_to_one_frame(self) -> None:
        class PolicyError(Exception):
            status_code = 400
            code = "content_policy_violation"

        class FakeCompletions:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, **kwargs):  # type: ignore[no-untyped-def]
                self.calls += 1
                images = [
                    item
                    for item in kwargs["messages"][0]["content"]
                    if item.get("type") == "image_url"
                ]
                if len(images) > 1 or self.calls == 3:
                    raise PolicyError("content_policy_violation")
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content=json.dumps([{"id": 1, "description": "安全画面"}])
                            )
                        )
                    ]
                )

        completions = FakeCompletions()
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            events, _source = self._project(root)
            second_frame = root / "analysis" / "keyframes" / "scene_0002.jpg"
            second_frame.write_bytes(b"second jpeg bytes")
            payload = json.loads(events.read_text(encoding="utf-8"))
            payload["events"].append(
                {
                    "id": 2,
                    "start": 3.0,
                    "end": 6.0,
                    "keyframe": "keyframes/scene_0002.jpg",
                }
            )
            events.write_text(json.dumps(payload), encoding="utf-8")
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False), patch(
                "openai.OpenAI", return_value=client
            ):
                result = describe_event_keyframes(
                    events,
                    {"shared": {"env_file": ".missing"}, "vision": {"batch_size": 2}},
                    root,
                    lambda _value, _status: None,
                )

        self.assertEqual(completions.calls, 3)
        self.assertEqual(result["events"][0]["visual_description"], "安全画面")
        self.assertIn("内容安全规则", result["events"][1]["vision_skipped_reason"])
        self.assertEqual(result["vision_skipped_event_count"], 1)

    def test_retry_skipped_only_requests_frames_without_descriptions(self) -> None:
        class FakeCompletions:
            def __init__(self) -> None:
                self.requested_text = ""

            def create(self, **kwargs):  # type: ignore[no-untyped-def]
                content = kwargs["messages"][0]["content"]
                self.requested_text = " ".join(
                    str(item.get("text", ""))
                    for item in content
                    if item.get("type") == "text"
                )
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content=json.dumps([{"id": 2, "description": "重试成功"}])
                            )
                        )
                    ]
                )

        completions = FakeCompletions()
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            events, _source = self._project(root)
            second_frame = root / "analysis" / "keyframes" / "scene_0002.jpg"
            second_frame.write_bytes(b"second jpeg bytes")
            payload = json.loads(events.read_text(encoding="utf-8"))
            payload["events"][0]["visual_description"] = "已经完成"
            payload["events"].append(
                {
                    "id": 2,
                    "start": 3.0,
                    "end": 6.0,
                    "keyframe": "keyframes/scene_0002.jpg",
                    "vision_skipped_reason": "内容安全规则限制",
                }
            )
            events.write_text(json.dumps(payload), encoding="utf-8")
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False), patch(
                "openai.OpenAI", return_value=client
            ):
                result = describe_event_keyframes(
                    events,
                    {"shared": {"env_file": ".missing"}, "vision": {"batch_size": 4}},
                    root,
                    lambda _value, _status: None,
                    retry_skipped=True,
                )

        self.assertIn("事件 2", completions.requested_text)
        self.assertNotIn("事件 1，", completions.requested_text)
        self.assertEqual(result["events"][0]["visual_description"], "已经完成")
        self.assertEqual(result["events"][1]["visual_description"], "重试成功")
        self.assertNotIn("vision_skipped_reason", result["events"][1])
        self.assertEqual(result["vision_skipped_event_count"], 0)

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
