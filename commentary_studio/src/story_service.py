from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from .vision_service import api_configuration
from .voice_service import MIN_TTS_UNIT_DURATION_SEC, split_gpt_sovits_units


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

    story_config = config.get("story", {})
    api = api_configuration(config, app_root, "story")
    api_key = str(api["api_key"])
    base_url = str(api["base_url"]).strip() or None
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
You are a native English science YouTube writer and editor.
Write like a knowledgeable person explaining something interesting to a curious audience.
Do not sound like a documentary narrator, trailer writer, marketer, lecturer, or essayist.
Turn the source events into narration that sounds as if it was originally written and spoken by a native English creator.
The subject may involve science, technology, engineering, astronomy, geography, biology, nature, history, or another factual topic.

TARGET
- Maximum duration: {target_duration_sec} seconds.
- Maximum narration length: {max_words} English words.
- Requested style profile: {story_config.get('narrative_style', 'natural_science_youtube')}.
- These limits are ceilings, not targets. Prefer the shortest script that preserves the useful story.

CONTENT DISCIPLINE
1. Use only facts supported by the supplied events. Never invent a fact, cause, result, quotation, or unseen action.
2. You may omit repetitive or unimportant events, but preserve a clear and accurate line of thought.
3. If the source does not contain enough information, make the result shorter instead of padding or repeating it.
4. Technical terms must remain accurate. Explain them naturally only when the source provides enough context.
5. Do not expand the source merely to approach the duration or word limit.
6. Every sentence must introduce a fact, explain a mechanism, or create a necessary logical transition.

NARRATION STYLE
1. Do not translate literally. Understand the core idea and intended feeling, then rewrite it for a native English audience.
2. Sound like a natural science YouTuber speaking directly and clearly, not a polished TV documentary or generic AI article.
3. Use natural spoken English with varied rhythm. Mix concise statements with longer flowing thoughts when the subject benefits from it.
4. Let concrete facts create curiosity. Do not force a dramatic question, conflict, revelation, or exaggerated hook.
5. Avoid generic AI transitions and repeated opening patterns, especially:
   "Let's explore", "Let's take a look", "Let's break it down", "In this video",
   "You will see", "The answer is", "This is why", and "But here's the thing".
   A common phrase may appear when it is genuinely natural, but never as a reusable template.
6. Do not add cinematic narration, emotional filler, abstract praise, fake suspense, unnecessary rhetorical questions, or conclusions that repeat the opening.
7. Natural short expressions such as "Sure.", "Of course.", or "All right," are allowed when they genuinely fit the narration.
8. Avoid empty uses of "journey into", "the heart of", "fascinating", "remarkable", "innovation", "uncover", and "unique design". Use such wording only when it conveys specific information.
9. Favor concrete nouns and direct verbs over polished but vague phrases.

INTERNAL WRITING PROCESS
Perform these steps silently:
1. Understand the core facts, the most useful story angle, and the intended emotional tone.
2. Draft the English narration as a coherent whole. Do not make the prose choppy merely for subtitles or TTS.
3. Remove anything that sounds translated, cinematic, formulaic, promotional, like a school essay, or generically AI-written.
4. For every sentence, ask: "Does this add information or move the explanation forward?" Delete it if not.
5. Ask: "Would a knowledgeable native English science YouTuber actually say this aloud?" Rewrite any line that fails.

STRUCTURED OUTPUT
1. narration items are semantic story beats, not artificial TTS fragments. A beat may contain natural punctuation and more than one clause.
2. Each narration item must bind to supported event_ids and include a Chinese visual_query for shot matching.
3. The application will split final text at GPT-SoVITS punctuation afterward. Do not distort the prose to perform that technical split yourself.
4. Return exactly one JSON object and no Markdown or commentary, using this structure:
{{
  "title": "英文标题",
  "angle": "中文说明故事切入角度",
  "hook": "Natural English opening line; it does not need to be dramatic",
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
    temperature = max(0.0, min(1.2, float(story_config.get("temperature", 0.55))))
    progress(0.35, f"正在使用 {model} 生成故事大纲…")
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
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
    for item in result.get("narration", []):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text_en", "")).strip()
        if not text:
            continue
        units = split_gpt_sovits_units(text)
        unit_word_counts = [max(1, len(re.findall(r"\b[\w'-]+\b", unit))) for unit in units]
        word_count = sum(unit_word_counts)
        duration = max(0.8, float(item.get("estimated_duration_sec", 0) or word_count / 2.25))
        for unit, unit_words in zip(units, unit_word_counts):
            unit_duration = max(MIN_TTS_UNIT_DURATION_SEC, duration * unit_words / word_count)
            total_words += unit_words
            total_duration += unit_duration
            narration.append(
                {
                    "id": len(narration) + 1,
                    "event_ids": valid_event_ids(item.get("event_ids")),
                    "text_en": unit,
                    "visual_query": str(item.get("visual_query", "")).strip(),
                    "estimated_duration_sec": round(unit_duration, 2),
                    "word_count": unit_words,
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
