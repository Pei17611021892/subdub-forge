from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from .media_service import _resolve_tool

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
    source_video: Path | None = None,
    retry_skipped: bool = False,
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
    technical_enabled = bool(vision.get("technical_visuals_enabled", True))
    high_detail_enabled = technical_enabled and bool(
        vision.get("high_detail_review_enabled", True)
    )
    payload = json.loads(events_json.read_text(encoding="utf-8"))
    events = list(payload.get("events", []))
    content_mode = str(payload.get("content_mode", "speech"))
    project_analysis_dir = events_json.parent
    all_targets = [event for event in events if str(event.get("keyframe", "")).strip()]
    if retry_skipped:
        targets = [
            event
            for event in all_targets
            if not str(event.get("visual_description", "")).strip()
        ]
    else:
        targets = [
            event
            for event in all_targets
            if not str(event.get("visual_description", "")).strip()
            and not str(event.get("vision_skipped_reason", "")).strip()
        ]
    if not targets:
        return payload

    client = OpenAI(api_key=api_key, base_url=base_url)
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
                "依次分析下面的关键帧，并结合每个事件附带的解说文字判断这张画面在当前叙事中的位置。"
                "description 用具体中文描述可见主体、动作或状态、环境、构图和镜头类型，避免“许多人在一个空间”"
                "这类泛化表述；不要把解说中提到但画面看不到的内容写成可见事实。"
                "story_value 说明画面具体对应、铺垫或偏离了当前解说的哪一部分；continuity 写可用于衔接前后镜头的"
                "人物、物体、地点、动作方向或视觉风格；uncertainty 写无法从单帧确认的内容。"
                "每项 id 必须原样复制事件编号，绝不能从 1 重新编号。严格返回 JSON 数组，格式为 "
                "[{\"id\":5,\"description\":\"...\",\"story_value\":\"...\","
                "\"continuity\":\"...\",\"uncertainty\":\"...\"}]。"
        )
    if technical_enabled:
        instruction += (
                " 同一次分析还要识别画面中的文字、数字、单位、标题、标签、表格、图表、示意图、公式和软件界面。"
                "只抄录清晰可见的内容，不补全模糊文字；图表只描述可见坐标、图例、趋势和数值，"
                "公式保留原符号，不要擅自求解或推导。每个结果额外返回 screen_text 数组与 technical_visual 对象："
                "screen_text=[{\"text\":\"原文\",\"role\":\"title|label|value|caption|other\","
                "\"confidence\":\"high|medium|low\"}]；technical_visual={\"type\":"
                "\"none|text|chart|diagram|formula|table|interface|document|mixed\","
                "\"summary\":\"中文摘要\",\"facts\":[\"可见事实\"],\"uncertainty\":\"不确定项\","
                "\"importance\":0,\"needs_high_detail_review\":false,\"review_reason\":\"\"}。"
                "importance 使用 0-3；只有内容可能影响科普事实、但当前低清图确实无法辨认时，才请求高清复查。"
        )

    processed = 0

    def persist_progress(status: str) -> None:
        nonlocal processed
        payload["events"] = events
        payload["vision_model"] = model
        events_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        first_pass_share = 0.82 if high_detail_enabled and source_video else 1.0
        progress(processed / len(targets) * first_pass_share, status)

    def request_batch(batch: list[dict[str, Any]]) -> None:
        nonlocal processed
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
                        f"当前解说：{str(event.get('transcript', '')).strip()[:360] or '无对白'}。"
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
            if is_content_policy_error(exc):
                if len(batch) > 1:
                    midpoint = len(batch) // 2
                    request_batch(batch[:midpoint])
                    request_batch(batch[midpoint:])
                    return
                event = batch[0]
                event["vision_skipped_reason"] = "该关键画面被视觉接口的内容安全规则限制"
                processed += 1
                persist_progress(
                    f"已处理 {processed}/{len(targets)} 个关键帧；1 个受限画面已跳过"
                )
                return
            raise friendly_api_error(exc, base_url, "视觉描述") from exc
        text = str(response.choices[0].message.content or "")
        descriptions, response_tail = _parse_json_array_details(text)
        if response_tail:
            _record_response_anomaly(
                project_analysis_dir / "vision_response_anomalies.json",
                batch,
                text,
                response_tail,
            )
        requested_ids = [int(event.get("id", 0)) for event in batch]
        returned_ids = [int(item.get("id", 0) or 0) for item in descriptions]
        if (
            len(descriptions) == len(batch)
            and not set(returned_ids).intersection(requested_ids)
        ):
            descriptions = [
                {**item, "id": int(event.get("id", 0))}
                for event, item in zip(batch, descriptions)
            ]
        by_id = {int(item.get("id", 0)): item for item in descriptions}
        completed_events = [
            event for event in batch if int(event.get("id", 0)) in by_id
        ]
        missing_events = [
            event for event in batch if int(event.get("id", 0)) not in by_id
        ]
        for event in completed_events:
            description = by_id.get(int(event["id"]), {})
            _apply_vision_item(event, description, content_mode, technical_enabled)
            event.pop("vision_skipped_reason", None)
        if completed_events:
            processed += len(completed_events)
            persist_progress(f"已理解 {processed}/{len(targets)} 个关键帧")
        if missing_events:
            if len(missing_events) < len(batch):
                request_batch(missing_events)
                return
            if len(batch) > 1:
                midpoint = len(batch) // 2
                request_batch(batch[:midpoint])
                request_batch(batch[midpoint:])
                return
            event = batch[0]
            event["vision_skipped_reason"] = "视觉接口未返回该关键场景的描述"
            processed += 1
            persist_progress(
                f"已处理 {processed}/{len(targets)} 个关键帧；1 个无返回画面已跳过"
            )

    for offset in range(0, len(targets), batch_size):
        request_batch(targets[offset : offset + batch_size])

    reviewed = 0
    if high_detail_enabled and source_video and source_video.exists():
        max_reviews = max(0, min(12, int(vision.get("max_high_detail_events", 6) or 6)))
        candidates = _select_high_detail_events(events, max_reviews)
        if candidates:
            progress(0.84, f"发现 {len(candidates)} 个重要文字/图表画面，正在高清复查…")
            try:
                reviewed = _review_high_detail_events(
                    client,
                    model,
                    candidates,
                    source_video,
                    events_json.parent,
                    config,
                    app_root,
                    base_url,
                    progress,
                )
            except Exception as exc:
                payload["technical_review_warning"] = str(exc)
                progress(1.0, f"关键帧理解完成；高清复查已跳过：{exc}")
        else:
            progress(1.0, "关键帧理解完成；没有需要额外高清复查的技术画面")

    payload["visual_schema_version"] = 2 if technical_enabled else 1
    payload["technical_visual_event_count"] = sum(
        bool(event.get("screen_text"))
        or str(event.get("technical_visual", {}).get("type", "none")) != "none"
        for event in events
    )
    payload["high_detail_review_count"] = reviewed
    payload["visual_description_event_count"] = sum(
        bool(str(event.get("visual_description", "")).strip()) for event in events
    )
    payload["vision_skipped_event_count"] = sum(
        bool(str(event.get("vision_skipped_reason", "")).strip()) for event in all_targets
    )
    payload["events"] = events
    events_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return payload


def _apply_vision_item(
    event: dict[str, Any],
    item: dict[str, Any],
    content_mode: str,
    technical_enabled: bool,
) -> None:
    event["visual_description"] = str(
        item.get("description", event.get("visual_description", ""))
    ).strip()
    event["story_value"] = str(item.get("story_value", "")).strip()
    event["continuity"] = str(item.get("continuity", "")).strip()
    event["visual_uncertainty"] = str(item.get("uncertainty", "")).strip()
    if technical_enabled:
        event["screen_text"] = _normalize_screen_text(item.get("screen_text", []))
        event["technical_visual"] = _normalize_technical_visual(
            item.get("technical_visual", {})
        )


def _normalize_screen_text(value: Any) -> list[dict[str, str]]:
    if isinstance(value, str):
        value = [{"text": value}]
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, str]] = []
    for raw in value[:16]:
        item = raw if isinstance(raw, dict) else {"text": raw}
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        role = str(item.get("role", "other")).strip().lower()
        confidence = str(item.get("confidence", "medium")).strip().lower()
        normalized.append(
            {
                "text": text[:300],
                "role": role if role in {"title", "label", "value", "caption", "other"} else "other",
                "confidence": confidence if confidence in {"high", "medium", "low"} else "medium",
            }
        )
    return normalized


def _normalize_technical_visual(value: Any) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    visual_type = str(item.get("type", "none")).strip().lower()
    allowed_types = {
        "none", "text", "chart", "diagram", "formula", "table",
        "interface", "document", "mixed",
    }
    facts = item.get("facts", [])
    if isinstance(facts, str):
        facts = [facts]
    normalized_facts = [str(fact).strip()[:400] for fact in facts[:12] if str(fact).strip()]
    try:
        importance = max(0, min(3, int(item.get("importance", 0) or 0)))
    except (TypeError, ValueError):
        importance = 0
    return {
        "type": visual_type if visual_type in allowed_types else "mixed",
        "summary": str(item.get("summary", "")).strip()[:1000],
        "facts": normalized_facts,
        "uncertainty": str(item.get("uncertainty", "")).strip()[:600],
        "importance": importance,
        "needs_high_detail_review": bool(item.get("needs_high_detail_review", False)),
        "review_reason": str(item.get("review_reason", "")).strip()[:500],
        "high_detail_reviewed": bool(item.get("high_detail_reviewed", False)),
    }


def _select_high_detail_events(events: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    candidates = [
        event
        for event in events
        if isinstance(event.get("technical_visual"), dict)
        and bool(event["technical_visual"].get("needs_high_detail_review", False))
        and int(event["technical_visual"].get("importance", 0) or 0) >= 1
    ]
    candidates.sort(
        key=lambda event: (
            -int(event.get("technical_visual", {}).get("importance", 0) or 0),
            float(event.get("start", 0) or 0),
        )
    )
    return candidates[:limit]


def _extract_high_detail_frames(
    events: list[dict[str, Any]],
    source_video: Path,
    analysis_dir: Path,
    config: dict[str, Any],
    app_root: Path,
) -> list[tuple[dict[str, Any], Path]]:
    shared = config.get("shared", {})
    ffmpeg = _resolve_tool(str(shared.get("ffmpeg_bin", "ffmpeg")), app_root, "ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 FFmpeg，无法抽取技术画面高清复查帧")
    width = max(960, min(2160, int(config.get("vision", {}).get("high_detail_width", 1440) or 1440)))
    target_dir = analysis_dir / "high_detail"
    target_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[tuple[dict[str, Any], Path]] = []
    for event in events:
        start = float(event.get("start", 0) or 0)
        duration = max(0.0, float(event.get("end", start) or start) - start)
        timestamp = start + min(0.75, max(0.15, duration * 0.2))
        output = target_dir / f"event_{int(event.get('id', 0)):04d}.jpg"
        result = subprocess.run(
            [
                ffmpeg, "-y", "-ss", f"{timestamp:.3f}", "-i", str(source_video),
                "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "2", str(output),
            ],
            capture_output=True,
        )
        if result.returncode == 0 and output.exists() and output.stat().st_size > 0:
            event["high_detail_frame"] = f"high_detail/{output.name}"
            extracted.append((event, output))
    return extracted


def _review_high_detail_events(
    client: Any,
    model: str,
    events: list[dict[str, Any]],
    source_video: Path,
    analysis_dir: Path,
    config: dict[str, Any],
    app_root: Path,
    base_url: str | None,
    progress: ProgressCallback,
) -> int:
    extracted = _extract_high_detail_frames(events, source_video, analysis_dir, config, app_root)
    if not extracted:
        raise RuntimeError("没有成功抽取可供高清复查的画面")
    batch_size = max(
        1, min(6, int(config.get("vision", {}).get("high_detail_batch_size", 3) or 3))
    )
    reviewed = 0
    for offset in range(0, len(extracted), batch_size):
        batch = extracted[offset : offset + batch_size]
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "这些是从原视频重新抽取的高清技术画面。逐项核实文字、数字、单位、坐标轴、图例、"
                    "表格、示意图结构与公式。只抄录清楚可见内容，保留原语言和数学符号；不要猜测模糊字符，"
                    "不要根据常识补值，也不要擅自求解公式。返回 JSON 数组，每项包含 id、screen_text 和 "
                    "technical_visual；technical_visual 沿用 type、summary、facts、uncertainty、importance 字段，"
                    "并设置 needs_high_detail_review=false、high_detail_reviewed=true。"
                ),
            }
        ]
        for event, frame in batch:
            content.append({"type": "text", "text": f"事件 {event['id']} 高清帧："})
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _image_data_url(frame), "detail": "high"},
                }
            )
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                temperature=0.0,
            )
        except Exception as exc:
            raise friendly_api_error(exc, base_url, "技术画面高清复查") from exc
        items = _parse_json_array(str(response.choices[0].message.content or ""))
        by_id = {int(item.get("id", 0)): item for item in items}
        for event, _frame in batch:
            item = by_id.get(int(event.get("id", 0)))
            if not item:
                continue
            screen_text = _normalize_screen_text(item.get("screen_text", []))
            if screen_text:
                event["screen_text"] = screen_text
            technical = _normalize_technical_visual(item.get("technical_visual", {}))
            technical["needs_high_detail_review"] = False
            technical["high_detail_reviewed"] = True
            event["technical_visual"] = technical
            reviewed += 1
        progress(
            0.84 + 0.16 * min(1.0, (offset + len(batch)) / len(extracted)),
            f"已高清复查 {min(offset + len(batch), len(extracted))}/{len(extracted)} 个技术画面",
        )
    return reviewed


def friendly_api_error(exc: Exception, base_url: str | None, operation: str) -> RuntimeError:
    message = str(exc).strip()
    status_code = getattr(exc, "status_code", None)
    if is_content_policy_error(exc):
        return RuntimeError(
            f"{operation}被内容安全规则限制。StoryCut 会隔离受限画面并继续处理其余关键帧；"
            "若仍然出现此提示，请重新抽取该场景的关键帧。"
        )
    if status_code == 405 or "405 Not Allowed" in message or "405 Method Not Allowed" in message:
        endpoint = base_url or "OpenAI 官方接口"
        return RuntimeError(
            f"{operation}接口返回 405：当前地址“{endpoint}”拼接出的 /chat/completions 路由不接受 POST 请求。"
            "可能是地址并非 OpenAI 兼容 API 根地址、服务商路由发生变化，或当前渠道不支持视觉对话。"
            "请打开首页“API 设置”核对服务商提供的根地址（通常以 /v1 结尾）；"
            "不要填写服务商网页、管理后台或以 /chat/completions 结尾的完整请求地址。"
        )
    return RuntimeError(f"{operation}接口请求失败：{message or type(exc).__name__}")


def is_content_policy_error(exc: Exception) -> bool:
    message = str(exc).lower()
    code = str(getattr(exc, "code", "")).lower()
    return "content_policy_violation" in message or "content_policy_violation" in code


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
    items, _response_tail = _parse_json_array_details(text)
    return items


def _parse_json_array_details(text: str) -> tuple[list[dict[str, Any]], str]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    array_start = cleaned.find("[")
    if array_start < 0:
        raise ValueError("视觉模型未返回可解析的 JSON 数组")
    try:
        value, _end = json.JSONDecoder().raw_decode(cleaned[array_start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"视觉模型返回的 JSON 数组不完整：{exc.msg}") from exc
    if not isinstance(value, list):
        raise ValueError("视觉模型返回结果不是数组")
    items = [item for item in value if isinstance(item, dict)]
    response_tail = cleaned[array_start + _end :].strip()
    meaningful_tail = re.sub(
        r"```(?:json)?", " ", response_tail, flags=re.IGNORECASE
    ).strip()
    cursor = 0
    decoder = json.JSONDecoder()
    while meaningful_tail:
        next_array = meaningful_tail.find("[", cursor)
        if next_array < 0:
            break
        try:
            extra_value, extra_end = decoder.raw_decode(meaningful_tail[next_array:])
        except json.JSONDecodeError:
            cursor = next_array + 1
            continue
        if isinstance(extra_value, list):
            items.extend(item for item in extra_value if isinstance(item, dict))
        cursor = next_array + extra_end
    return items, meaningful_tail


def _record_response_anomaly(
    path: Path,
    batch: list[dict[str, Any]],
    raw_response: str,
    response_tail: str,
) -> None:
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            payload = {"schema_version": 1, "responses": []}
        responses = payload.get("responses", [])
        if not isinstance(responses, list):
            responses = []
        responses.append(
            {
                "event_ids": [int(event.get("id", 0)) for event in batch],
                "tail": response_tail[:12000],
                "raw_response": raw_response[:50000],
            }
        )
        payload["responses"] = responses[-12:]
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except (OSError, ValueError, TypeError):
        return
