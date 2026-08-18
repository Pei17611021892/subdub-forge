from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Callable


ProgressCallback = Callable[[float, str], None]


def api_configuration(config: dict[str, Any], app_root: Path, section: str = "vision") -> dict[str, object]:
    _load_env_file(app_root, str(config.get("shared", {}).get("env_file", "../.env")))
    section_config = config.get(section, {})
    api_key = str(os.getenv("OPENAI_API_KEY", "")).strip()
    base_url = str(os.getenv("OPENAI_BASE_URL", "") or section_config.get("base_url", "")).strip()
    return {
        "configured": bool(api_key),
        "api_key": api_key,
        "base_url": base_url,
        "model": str(section_config.get("model", "gpt-4o-mini")),
    }


def describe_event_keyframes(
    events_json: Path,
    config: dict[str, Any],
    app_root: Path,
    progress: ProgressCallback,
) -> dict[str, Any]:
    from openai import OpenAI

    vision = config.get("vision", {})
    api = api_configuration(config, app_root, "vision")
    api_key = str(api["api_key"])
    base_url = str(api["base_url"]).strip() or None
    if not api_key:
        raise RuntimeError("未配置 OPENAI_API_KEY，无法生成关键帧视觉描述")

    model = str(vision.get("model", "gpt-4o-mini"))
    batch_size = max(1, min(8, int(vision.get("batch_size", 4))))
    payload = json.loads(events_json.read_text(encoding="utf-8"))
    events = list(payload.get("events", []))
    content_mode = str(payload.get("content_mode", "speech"))
    project_analysis_dir = events_json.parent
    targets = [event for event in events if str(event.get("keyframe", "")).strip()]
    if not targets:
        return payload

    client = OpenAI(api_key=api_key, base_url=base_url)
    for offset in range(0, len(targets), batch_size):
        batch = targets[offset : offset + batch_size]
        if content_mode == "visual":
            instruction = (
                "下面是按时间顺序排列的视频场景，每个场景可能包含起始关键帧和后续采样帧。"
                "请把同一事件的多张图当作短动作序列分析，而不是互不相关的图片。"
                "只依据可见证据，详细记录：人物外观与可区分特征；身体姿态、朝向、视线、手部动作；"
                "人与机器、工具、水体、植被及其他物体的交互；机器或环境在前后帧中的状态变化；"
                "地形、天气、光线、水流、污染物和潜在障碍；动作是否受阻、重复、完成或产生可见结果；"
                "镜头景别及有助于前后场景衔接的线索。不要编造姓名、职业、动机、机器用途、因果或画外事件。"
                "无法确定时使用“似乎”“可能”并写入 uncertainty。description 使用详细中文事实描述；"
                "story_value 概括这一段对故事推进的价值；continuity 写与前后镜头可衔接的主体、动作或环境线索。"
                "严格返回 JSON 数组："
                "[{\"id\":1,\"description\":\"...\",\"story_value\":\"...\","
                "\"continuity\":\"...\",\"uncertainty\":\"...\"}]。"
            )
        else:
            instruction = (
                "依次分析下面的关键帧。只描述画面中可见的人物、动作、物体、环境和镜头类型，"
                "不要猜测看不到的剧情。每条用简洁中文，适合视频剪辑检索。"
                "严格返回 JSON 数组，格式为 [{\"id\":1,\"description\":\"...\"}]。"
            )
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": instruction,
            }
        ]
        for event in batch:
            frame_paths = [str(event.get("keyframe", ""))]
            if content_mode == "visual":
                frame_paths.extend(str(value) for value in event.get("visual_samples", [])[:2])
            existing_frames = [
                project_analysis_dir / value for value in frame_paths
                if value and (project_analysis_dir / value).exists()
            ]
            if not existing_frames:
                continue
            content.append(
                {
                    "type": "text",
                    "text": (
                        f"事件 {event['id']}，时间 {event['start']}-{event['end']} 秒，"
                        f"共 {len(existing_frames)} 张时间顺序画面："
                    ),
                }
            )
            for frame_index, frame_path in enumerate(existing_frames, start=1):
                content.append({"type": "text", "text": f"事件 {event['id']} · 画面 {frame_index}："})
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_data_url(frame_path), "detail": "low"},
                    }
                )

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                temperature=0.1,
            )
        except Exception as exc:
            raise friendly_api_error(exc, base_url, "视觉描述") from exc
        text = str(response.choices[0].message.content or "")
        descriptions = _parse_json_array(text)
        by_id = {int(item.get("id", 0)): item for item in descriptions}
        for event in batch:
            description = by_id.get(int(event["id"]), {})
            event["visual_description"] = str(
                description.get("description", event.get("visual_description", ""))
            ).strip()
            if content_mode == "visual":
                event["story_value"] = str(description.get("story_value", "")).strip()
                event["continuity"] = str(description.get("continuity", "")).strip()
                event["visual_uncertainty"] = str(description.get("uncertainty", "")).strip()

        payload["events"] = events
        payload["vision_model"] = model
        events_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        completed = min(offset + len(batch), len(targets))
        progress(completed / len(targets), f"已理解 {completed}/{len(targets)} 个关键帧")

    return payload


def friendly_api_error(exc: Exception, base_url: str | None, operation: str) -> RuntimeError:
    message = str(exc).strip()
    status_code = getattr(exc, "status_code", None)
    if status_code == 405 or "405 Not Allowed" in message or "405 Method Not Allowed" in message:
        endpoint = base_url or "OpenAI 官方接口"
        return RuntimeError(
            f"{operation}接口返回 405：当前地址“{endpoint}”拼接出的 /chat/completions 路由不接受 POST 请求。"
            "可能是地址并非 OpenAI 兼容 API 根地址、服务商路由发生变化，或当前渠道不支持视觉对话。"
            "请打开首页“API 设置”核对服务商提供的根地址（通常以 /v1 结尾）；"
            "不要填写服务商网页、管理后台或以 /chat/completions 结尾的完整请求地址。"
        )
    return RuntimeError(f"{operation}接口请求失败：{message or type(exc).__name__}")


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
        # The shared .env is the user-facing source of truth and may be edited while
        # StoryCut is running, so refresh existing process values as well.
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


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
