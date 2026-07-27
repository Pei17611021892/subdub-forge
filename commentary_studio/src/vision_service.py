from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Callable


ProgressCallback = Callable[[float, str], None]


def describe_event_keyframes(
    events_json: Path,
    config: dict[str, Any],
    app_root: Path,
    progress: ProgressCallback,
) -> dict[str, Any]:
    from openai import OpenAI

    vision = config.get("vision", {})
    _load_env_file(app_root, str(config.get("shared", {}).get("env_file", "../.env")))
    api_key = str(os.getenv("OPENAI_API_KEY", "")).strip()
    base_url = str(vision.get("base_url") or os.getenv("OPENAI_BASE_URL", "")).strip() or None
    if not api_key:
        raise RuntimeError("未配置 OPENAI_API_KEY，无法生成关键帧视觉描述")

    model = str(vision.get("model", "gpt-4o-mini"))
    batch_size = max(1, min(8, int(vision.get("batch_size", 4))))
    payload = json.loads(events_json.read_text(encoding="utf-8"))
    events = list(payload.get("events", []))
    project_analysis_dir = events_json.parent
    targets = [event for event in events if str(event.get("keyframe", "")).strip()]
    if not targets:
        return payload

    client = OpenAI(api_key=api_key, base_url=base_url)
    for offset in range(0, len(targets), batch_size):
        batch = targets[offset : offset + batch_size]
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "依次分析下面的关键帧。只描述画面中可见的人物、动作、物体、环境和镜头类型，"
                    "不要猜测看不到的剧情。每条用简洁中文，适合视频剪辑检索。"
                    "严格返回 JSON 数组，格式为 [{\"id\":1,\"description\":\"...\"}]。"
                ),
            }
        ]
        for event in batch:
            keyframe = project_analysis_dir / str(event["keyframe"])
            if not keyframe.exists():
                continue
            content.append({"type": "text", "text": f"事件 {event['id']}，时间 {event['start']}-{event['end']} 秒："})
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _image_data_url(keyframe), "detail": "low"},
                }
            )

        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            temperature=0.1,
        )
        text = str(response.choices[0].message.content or "")
        descriptions = _parse_json_array(text)
        by_id = {int(item.get("id", 0)): str(item.get("description", "")).strip() for item in descriptions}
        for event in batch:
            event["visual_description"] = by_id.get(int(event["id"]), event.get("visual_description", ""))

        payload["events"] = events
        payload["vision_model"] = model
        events_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        completed = min(offset + len(batch), len(targets))
        progress(completed / len(targets), f"已理解 {completed}/{len(targets)} 个关键帧")

    return payload


def _load_env_file(app_root: Path, configured: str) -> None:
    path = Path(configured)
    if not path.is_absolute():
        path = (app_root / path).resolve()
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _image_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
        if not match:
            raise ValueError("视觉模型未返回可解析的 JSON 数组")
        value = json.loads(match.group(0))
    if not isinstance(value, list):
        raise ValueError("视觉模型返回结果不是数组")
    return [item for item in value if isinstance(item, dict)]
