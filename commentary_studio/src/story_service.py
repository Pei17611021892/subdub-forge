from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from .vision_service import _load_env_file


ProgressCallback = Callable[[float, str], None]


def generate_story_script(
    events_json: Path,
    story_json: Path,
    target_duration_sec: int,
    config: dict[str, Any],
    app_root: Path,
    progress: ProgressCallback,
) -> dict[str, Any]:
    from openai import OpenAI

    _load_env_file(app_root, str(config.get("shared", {}).get("env_file", "../.env")))
    api_key = str(os.getenv("OPENAI_API_KEY", "")).strip()
    story_config = config.get("story", {})
    base_url = str(story_config.get("base_url") or os.getenv("OPENAI_BASE_URL", "")).strip() or None
    if not api_key:
        raise RuntimeError("未配置 OPENAI_API_KEY，无法组织故事")

    events_payload = json.loads(events_json.read_text(encoding="utf-8"))
    events = list(events_payload.get("events", []))
    if not events:
        raise ValueError("events.json 中没有可用于组织故事的事件")

    compact_events = [
        {
            "id": int(event.get("id", 0)),
            "start": event.get("start", 0),
            "end": event.get("end", 0),
            "transcript": str(event.get("transcript", "")),
            "visual": str(event.get("visual_description", "")),
        }
        for event in events
    ]
    max_words = max(30, round(target_duration_sec * 2.25))
    progress(0.1, "正在评估事件重要度和故事角度…")
    prompt = f"""
你是一名擅长 YouTube Shorts 的英文解说编剧。根据给定的原片事件，重新组织一个紧凑、准确、具有吸引力的故事。

目标时长：最多 {target_duration_sec} 秒
英文旁白总词数：最多 {max_words} 词
叙事风格：{story_config.get('narrative_style', 'concise')}

规则：
1. 只能使用事件中明确出现的信息，不得编造事实。
2. 允许舍弃重复或无关事件，但故事因果必须清楚。
3. 开头 1～2 句直接提出冲突、问题或最吸引人的结果。
4. narration 必须拆成短句，每句绑定 event_ids，供后续匹配镜头。
5. visual_query 用中文描述这句旁白需要什么画面。
6. 原片信息不足时宁可缩短成片，不要为了填满目标时长重复或虚构。
7. text_en 必须是自然、口语化、适合配音的英文。

严格返回一个 JSON 对象，不要 Markdown，格式：
{{
  "title": "英文标题",
  "angle": "中文说明故事切入角度",
  "hook": "英文开场钩子",
  "selected_event_ids": [1, 2],
  "omitted_event_ids": [3],
  "outline": [
    {{"order": 1, "event_ids": [1], "purpose": "hook", "summary": "中文段落摘要"}}
  ],
  "narration": [
    {{"id": 1, "event_ids": [1], "text_en": "English narration.", "visual_query": "需要的画面", "estimated_duration_sec": 3.2}}
  ]
}}

原片事件：
{json.dumps(compact_events, ensure_ascii=False)}
""".strip()

    client = OpenAI(api_key=api_key, base_url=base_url)
    model = str(story_config.get("model", "gpt-4o-mini"))
    progress(0.35, f"正在使用 {model} 生成故事大纲…")
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.35,
    )
    result = _parse_json_object(str(response.choices[0].message.content or ""))
    progress(0.8, "正在校验事件引用和解说时长…")
    normalized = _normalize_story(result, events, target_duration_sec, model)
    story_json.parent.mkdir(parents=True, exist_ok=True)
    story_json.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    progress(1.0, f"故事组织完成，共 {len(normalized['narration'])} 句解说")
    return normalized


def _normalize_story(
    result: dict[str, Any],
    events: list[dict[str, Any]],
    target_duration_sec: int,
    model: str,
) -> dict[str, Any]:
    valid_ids = {int(event.get("id", 0)) for event in events}

    def valid_event_ids(value: Any) -> list[int]:
        if not isinstance(value, list):
            return []
        return [int(item) for item in value if str(item).isdigit() and int(item) in valid_ids]

    narration = []
    total_words = 0
    total_duration = 0.0
    for index, item in enumerate(result.get("narration", []), start=1):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text_en", "")).strip()
        if not text:
            continue
        word_count = len(re.findall(r"\b[\w'-]+\b", text))
        duration = max(0.8, float(item.get("estimated_duration_sec", 0) or word_count / 2.25))
        total_words += word_count
        total_duration += duration
        narration.append(
            {
                "id": index,
                "event_ids": valid_event_ids(item.get("event_ids")),
                "text_en": text,
                "visual_query": str(item.get("visual_query", "")).strip(),
                "estimated_duration_sec": round(duration, 2),
                "word_count": word_count,
            }
        )

    selected = valid_event_ids(result.get("selected_event_ids"))
    omitted = valid_event_ids(result.get("omitted_event_ids"))
    if not selected:
        selected = sorted({event_id for item in narration for event_id in item["event_ids"]})
    if not omitted:
        omitted = sorted(valid_ids - set(selected))

    outline = []
    for index, item in enumerate(result.get("outline", []), start=1):
        if not isinstance(item, dict):
            continue
        outline.append(
            {
                "order": index,
                "event_ids": valid_event_ids(item.get("event_ids")),
                "purpose": str(item.get("purpose", "body")),
                "summary": str(item.get("summary", "")).strip(),
            }
        )

    return {
        "schema_version": 1,
        "model": model,
        "target_duration_sec": target_duration_sec,
        "estimated_duration_sec": round(total_duration, 2),
        "word_count": total_words,
        "title": str(result.get("title", "Untitled Story")).strip(),
        "angle": str(result.get("angle", "")).strip(),
        "hook": str(result.get("hook", "")).strip(),
        "selected_event_ids": selected,
        "omitted_event_ids": omitted,
        "outline": outline,
        "narration": narration,
    }


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise ValueError("故事模型未返回可解析的 JSON 对象")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("故事模型返回结果不是对象")
    return value
