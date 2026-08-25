from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from .vision_service import api_configuration, friendly_api_error
from .voice_service import (
    SHORTS_MAX_DURATION_SEC,
    estimate_tts_unit_duration,
    split_gpt_sovits_units,
)


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
    content_mode = str(events_payload.get("content_mode", "speech"))
    if not events:
        raise ValueError("events.json 中没有可用于组织故事的事件")

    compact_events = [
        {
            "id": int(event.get("id", 0)),
            "start": event.get("start", 0),
            "end": event.get("end", 0),
            "transcript": str(event.get("transcript", "")),
            "visual": str(event.get("visual_description", "")),
            "story_value": str(event.get("story_value", "")),
            "continuity": str(event.get("continuity", "")),
            "uncertainty": str(event.get("visual_uncertainty", "")),
            "screen_text": event.get("screen_text", []),
            "technical_visual": event.get("technical_visual", {}),
        }
        for event in events
    ]
    shorts_max_words = round(SHORTS_MAX_DURATION_SEC * 1.85)
    max_words = min(shorts_max_words, max(30, round(target_duration_sec * 1.9)))
    described_event_ids = [
        int(event.get("id", 0))
        for event in events
        if str(event.get("visual_description", "")).strip()
    ]
    minimum_words = (
        min(max_words, max(60, round(target_duration_sec * 1.55)))
        if content_mode == "visual"
        else 0
    )
    minimum_event_coverage = (
        min(len(described_event_ids), max(12, round(target_duration_sec / 6)))
        if content_mode == "visual"
        else 0
    )
    maximum_event_coverage = (
        min(
            len(described_event_ids),
            max(minimum_event_coverage, round(target_duration_sec / 3)),
        )
        if content_mode == "visual"
        else 0
    )
    outline_target = max(6, min(12, round(target_duration_sec / 20)))
    client = OpenAI(api_key=api_key, base_url=base_url)
    model = str(story_config.get("model", "gpt-4o-mini"))
    editor_model = str(story_config.get("editor_model", "")).strip() or model
    temperature = max(0.0, min(1.2, float(story_config.get("temperature", 0.55))))
    plan: dict[str, Any] = {}
    if content_mode == "visual":
        progress(0.08, f"正在使用 {model} 通读全片并规划故事弧…")
        plan_prompt = _build_visual_plan_prompt(
            compact_events,
            target_duration_sec,
            minimum_event_coverage,
            maximum_event_coverage,
            outline_target,
        )
        plan = _chat_json(client, model, plan_prompt, temperature, base_url, "全片故事规划")
        plan = _normalize_visual_plan(plan, events)
        plan_path = story_json.with_name("story_plan.json")
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "planner_model": model,
                    "target_duration_sec": target_duration_sec,
                    **plan,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        progress(0.36, f"故事弧已完成，正在由 {editor_model} 进行最终编辑…")
        prompt = _build_visual_editor_prompt(
            compact_events,
            plan,
            target_duration_sec,
            minimum_words,
            max_words,
            shorts_max_words,
            minimum_event_coverage,
            maximum_event_coverage,
            outline_target,
        )
        result = _chat_json(client, editor_model, prompt, temperature, base_url, "最终故事编辑")
        normalized = _normalize_story(result, events, target_duration_sec, editor_model)
        normalized["planner_model"] = model
        normalized["editor_model"] = editor_model
        normalized["workflow"] = "visual_story_editor_v2"
    else:
        progress(0.1, f"正在使用 {model} 组织语音与画面故事…")
        prompt = _build_speech_story_prompt(
            compact_events,
            target_duration_sec,
            max_words,
            str(story_config.get("narrative_style", "natural_science_youtube")),
        )
        result = _chat_json(client, model, prompt, temperature, base_url, "故事生成")
        normalized = _normalize_story(result, events, target_duration_sec, model)

    progress(0.68, "正在校验事件覆盖率、故事阶段和解说长度…")
    bound_event_count = len(_narration_event_ids(normalized))
    if content_mode == "visual" and (
        int(normalized.get("word_count", 0)) < minimum_words
        or bound_event_count < minimum_event_coverage
        or len(normalized.get("outline", [])) < max(5, round(outline_target * 0.7))
        or not _covers_timeline_sections(normalized, events)
        or float(normalized.get("estimated_duration_sec", 0)) >= SHORTS_MAX_DURATION_SEC
    ):
        draft_duration = float(normalized.get("estimated_duration_sec", 0))
        length_problem = "超过 Shorts 三分钟限制" if draft_duration >= SHORTS_MAX_DURATION_SEC else "偏短"
        progress(
            0.74,
            f"最终稿{length_problem}或故事结构不完整，正在由 {editor_model} 整篇重编…",
        )
        retry_prompt = _build_visual_rewrite_prompt(
            compact_events,
            plan,
            result,
            target_duration_sec,
            minimum_words,
            max_words,
            shorts_max_words,
            minimum_event_coverage,
            maximum_event_coverage,
            outline_target,
        )
        result = _chat_json(client, editor_model, retry_prompt, temperature, base_url, "最终故事重编")
        normalized = _normalize_story(result, events, target_duration_sec, editor_model)
        normalized["planner_model"] = model
        normalized["editor_model"] = editor_model
        normalized["workflow"] = "visual_story_editor_v2"
        bound_event_count = len(_narration_event_ids(normalized))
        minimum_acceptable_words = round(minimum_words * 0.85)
        minimum_acceptable_coverage = max(8, round(minimum_event_coverage * 0.8))
        minimum_acceptable_outline = max(5, round(outline_target * 0.7))
        if (
            int(normalized.get("word_count", 0)) < minimum_acceptable_words
            or bound_event_count < minimum_acceptable_coverage
            or len(normalized.get("outline", [])) < minimum_acceptable_outline
            or not _covers_timeline_sections(normalized, events)
            or float(normalized.get("estimated_duration_sec", 0)) >= SHORTS_MAX_DURATION_SEC
        ):
            raise RuntimeError(
                "最终故事编辑后仍未达到可用标准："
                f"当前 {normalized.get('word_count', 0)} 词、覆盖 "
                f"{bound_event_count} 个实际旁白事件、"
                f"{len(normalized.get('outline', []))} 个故事阶段；"
                f"至少需要约 {minimum_acceptable_words} 词、覆盖 "
                f"{minimum_acceptable_coverage} 个实际事件并包含 {minimum_acceptable_outline} 个阶段；"
                f"同时预计成片必须低于 {SHORTS_MAX_DURATION_SEC:.0f} 秒。"
                "请在 API 设置中选择能力更强的故事生成或最终编辑模型后重试。"
            )
    progress(0.88, "正在整理解说断句与镜头绑定…")
    if float(normalized.get("estimated_duration_sec", 0)) >= SHORTS_MAX_DURATION_SEC:
        raise RuntimeError(
            f"预计旁白约 {normalized.get('estimated_duration_sec', 0)} 秒，超过 Shorts 三分钟限制。"
            "请缩短故事后重试。"
        )
    normalized["content_mode"] = content_mode
    story_json.parent.mkdir(parents=True, exist_ok=True)
    story_json.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    progress(1.0, f"故事组织完成，共 {len(normalized['narration'])} 句解说")
    return normalized


def _chat_json(
    client: Any,
    model: str,
    prompt: str,
    temperature: float,
    base_url: str | None,
    operation: str,
) -> dict[str, Any]:
    try:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )
        except Exception as exc:
            message = str(exc).lower()
            status_code = getattr(exc, "status_code", None)
            if status_code == 400 and "temperature" in message:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                )
            else:
                raise
    except Exception as exc:
        raise friendly_api_error(exc, base_url, operation) from exc
    return _parse_json_object(str(response.choices[0].message.content or ""))


def _build_visual_plan_prompt(
    events: list[dict[str, Any]],
    target_duration_sec: int,
    minimum_event_coverage: int,
    maximum_event_coverage: int,
    outline_target: int,
) -> str:
    return f"""
You are the senior story producer for a short-form narrated video. Do not write the final narration yet.
Read the entire chronological event list and discover the strongest truthful story hidden inside it.

The source has no useful speech. It must be understood from visible actions and state changes.

EDITORIAL GOAL
- Treat about {target_duration_sec} seconds as the preferred narration length. Preserve coherence when a long source genuinely needs more room, but the finished story must still fit within a three-minute Short.
- Aim for roughly {outline_target} chronological stages and about {minimum_event_coverage}-{maximum_event_coverage} highlighted event IDs across the beginning, middle, and end. These are editorial targets rather than reasons to damage a strong story.
- Find a concrete progression: initial situation, immediate goal, resistance or difficulty, attempts and adjustments, visible turning points, result, and an earned final feeling.
- Prioritize visible state changes, effort, reactions, tool changes, failed or repeated attempts, before/after contrast, and clear payoff.
- Select highlights rather than cataloguing the whole process. Routine repetitions may support a stage without becoming their own beat.
- Combine repetitive events into stages, but preserve the strongest turning points from the beginning, middle, and end.

GROUNDING
- Separate observation from interpretation.
- You may infer an immediate short-term goal only when several consecutive events support it. Mark it as interpretation, not fact.
- Do not invent identity, occupation, backstory, time of day, weather, sound, smell, dialogue, exact attempt count, hidden damage, motivation, or outcome.
- Uncertainty in the source must remain uncertainty.
- Treat clearly transcribed on-screen text, chart labels, diagram structure, and formulas as evidence. Preserve units and symbols exactly; never fill in blurred text or derive claims that the visual does not state.
- Do not create generic heroism, environmental messaging, determination, adventure, or life lessons.

Return exactly one JSON object and no Markdown:
{{
  "title": "working English title",
  "angle": "中文说明最值得讲的故事角度",
  "premise": "中文说明人物正在完成什么可见任务",
  "central_question": "中文说明观众会关心的具体问题，不必写成问句",
  "emotional_curve": "中文说明从开头到结尾的情绪变化",
  "highlight_event_ids": [1, 2],
  "outline": [
    {{
      "order": 1,
      "event_ids": [1, 2],
      "stage": "setup|goal|obstacle|attempt|turn|payoff|aftertaste",
      "visible_change": "中文可见事实与前后变化",
      "story_function": "这一阶段为什么值得保留",
      "subtext": "可以由连续画面支持的柔性潜台词；没有则留空",
      "transition": "如何自然进入下一阶段"
    }}
  ]
}}

CHRONOLOGICAL SOURCE EVENTS:
{json.dumps(events, ensure_ascii=False)}
""".strip()


def _build_visual_editor_prompt(
    events: list[dict[str, Any]],
    plan: dict[str, Any],
    target_duration_sec: int,
    minimum_words: int,
    max_words: int,
    shorts_max_words: int,
    minimum_event_coverage: int,
    maximum_event_coverage: int,
    outline_target: int,
) -> str:
    return f"""
You are the final story editor and native English narrator for an immersive observational video.
Use the producer's plan and the evidence timeline to write the complete final narration.

You are not a screen reader. Do not merely announce clothing, posture, or every object the viewer can already see.
For every story stage, connect three layers whenever evidence permits:
1. the visible action or state change;
2. the practical difficulty, immediate purpose, or consequence;
3. the feeling or subtext created by that progression.

STORY SHAPE
- Treat {target_duration_sec} seconds and roughly {minimum_words}-{max_words} English words as the preferred length, not a rigid cap.
- If a long source would become incoherent at the preferred length, keep the stronger story arc and allow it to run longer.
- The only absolute delivery limit is YouTube Shorts: the completed narration must remain below {SHORTS_MAX_DURATION_SEC:.0f} seconds. Stay below about {shorts_max_words} words to leave room for natural pauses.
- Use roughly {outline_target} chronological outline stages.
- Bind narration to about {minimum_event_coverage}-{maximum_event_coverage} distinct valid event IDs distributed across the whole source.
- Establish an immediate goal, develop resistance and adjustments, preserve turning points, and earn the ending from a visible result.
- Spend words on changes and consequences, not repeated descriptions of similar actions.
- Select only the strongest actions needed for the arc. Do not narrate every cleaning, checking, opening, or tool-handling step.

VOICE AND RHYTHM
- Write in an immersive third-person observational voice with warmth, breath, and human presence.
- Use concrete sensory-looking details that are actually visible: balance, weight, distance, texture, light, water movement, effort, hesitation, reaction, and before/after contrast.
- A grounded metaphor, personification, onomatopoeia, or cross-sensory image is allowed when supported, but use at most one conspicuous literary device per story stage.
- Vary sentence rhythm. Most final sentences should be about 8-18 English words; an occasional shorter line may create emphasis.
- Every comma is also a TTS boundary. Each comma-delimited clause must be independently speakable and normally contain at least five words. Prefer complete sentences over chains of short comma fragments.
- Never create standalone fragments such as "Next," "Finally," or "A woman,".
- Avoid generic AI emotion and documentary filler such as "showing her determination", "confidence and skill", "adventure awaits", "this moment encapsulates", or "a testament to".
- Do not force a rhetorical question as the opening. Begin with the most specific atmosphere, action, contrast, or unresolved practical situation.

INTERPRETATION BOUNDARY
- Reasonable immediate intention may be phrased softly: "she seems to be", "as if", "the task now is", or an equivalent natural construction.
- Never turn uncertainty into fact.
- Treat clearly transcribed on-screen text, chart labels, diagram relationships, tables, and formulas as evidence. Preserve units and symbols; never guess blurred values or derive claims the visual does not support.
- Never invent time of day, fog, weather, sound, smell, dialogue, exact attempt count, identity, occupation, backstory, private thoughts, machine condition, or unseen result unless explicitly supported by the source.
- Emotional meaning must be earned by visible effort, reaction, repetition, contrast, or outcome.

OUTPUT
- Draft the narration as a coherent whole, then divide it into semantic beats for editing.
- Each narration beat must bind to the event IDs that support it and include a concise Chinese visual_query.
- event_ids are evidence references, not decoration. Do not bind a line to an unrelated highlight.
- Return exactly one JSON object and no Markdown:
{{
  "title": "English title",
  "angle": "中文说明最终故事角度",
  "hook": "first English narration line",
  "selected_event_ids": [1, 2],
  "omitted_event_ids": [3],
  "outline": [
    {{"order": 1, "event_ids": [1, 2], "purpose": "setup|goal|obstacle|attempt|turn|payoff|aftertaste", "summary": "中文阶段摘要"}}
  ],
  "narration": [
    {{"id": 1, "event_ids": [1, 2], "text_en": "Natural English narration.", "visual_query": "对应的中文画面需求", "estimated_duration_sec": 4.0}}
  ]
}}

PRODUCER'S STORY PLAN:
{json.dumps(plan, ensure_ascii=False)}

FULL EVIDENCE TIMELINE:
{json.dumps(events, ensure_ascii=False)}
""".strip()


def _build_visual_rewrite_prompt(
    events: list[dict[str, Any]],
    plan: dict[str, Any],
    draft: dict[str, Any],
    target_duration_sec: int,
    minimum_words: int,
    max_words: int,
    shorts_max_words: int,
    minimum_event_coverage: int,
    maximum_event_coverage: int,
    outline_target: int,
) -> str:
    return f"""
Act as the final story editor. Replace the rejected draft completely; do not append filler to it.

The draft failed because it was too short, exceeded the Shorts delivery limit, covered too few source events, used too few story stages, or read the screen instead of telling a story.

NON-NEGOTIABLE ACCEPTANCE RULES
- Return one complete replacement JSON object in the same schema as the rejected draft.
- Treat {target_duration_sec} seconds and {minimum_words}-{max_words} words as the preferred length. A longer coherent story is allowed when the source needs it.
- The hard ceiling is below {SHORTS_MAX_DURATION_SEC:.0f} seconds and about {shorts_max_words} words including room for pauses.
- Bind the narration to about {minimum_event_coverage}-{maximum_event_coverage} distinct valid event IDs across the beginning, middle, and end.
- Use roughly {outline_target} chronological story stages.
- Build goal, resistance, attempts, visible changes, turning points, payoff, and aftertaste from supported evidence.
- Each beat must add meaning, consequence, subtext, or progression rather than simply restating the image.
- Compress aggressively by retaining only the strongest turning points. Do not enumerate routine maintenance steps.
- Count the narration words before returning. More than {shorts_max_words} words is a hard failure.
- Keep a warm immersive third-person voice, but never invent time, weather, sound, smell, dialogue, attempt count, identity, backstory, private thoughts, machine condition, or unseen outcome.
- Use clearly transcribed on-screen text, chart labels, diagram relationships, tables, and formulas as supporting evidence. Preserve uncertainty, units, and symbols; never guess unreadable values or invent a mathematical conclusion.
- Avoid generic claims about determination, confidence, skill, adventure, inspiration, or environmental virtue.
- Do not use standalone connector fragments.
- Every comma becomes a TTS boundary, so avoid short comma-delimited fragments.

PRODUCER'S PLAN:
{json.dumps(plan, ensure_ascii=False)}

REJECTED DRAFT:
{json.dumps(draft, ensure_ascii=False)}

FULL EVIDENCE TIMELINE:
{json.dumps(events, ensure_ascii=False)}
""".strip()


def _build_speech_story_prompt(
    events: list[dict[str, Any]],
    target_duration_sec: int,
    max_words: int,
    requested_style: str,
) -> str:
    return f"""
You are a native English science YouTube writer and editor. Write like a knowledgeable person explaining something interesting to a curious audience.
Do not sound like a documentary narrator, trailer writer, marketer, lecturer, or essayist.

TARGET
- Maximum duration: {target_duration_sec} seconds.
- Maximum narration length: {max_words} English words.
- Requested style profile: {requested_style}.
- These are ceilings, not targets. Prefer the shortest script that preserves the useful story.

SOURCE AND FACTS
- Treat the transcript as the main factual source and use visual descriptions to clarify actions, objects, setting, and shot selection.
- Use clearly visible labels, chart trends, diagram relationships, tables, and formulas as supporting evidence. Preserve uncertainty and never invent unreadable values or mathematical conclusions.
- Condense and rewrite the spoken material without losing its supported meaning.
- Never invent facts, causes, results, quotations, or unseen actions.
- Every sentence must add information, explain a mechanism, or create a necessary transition.

VOICE
- Write natural spoken English with varied rhythm, not literal translation or generic AI prose.
- Let concrete facts create curiosity; do not force drama, emotional filler, abstract praise, or a rhetorical question.
- Avoid "Let's explore", "Let's take a look", "In this video", "But here's the thing", "fascinating", "remarkable", and other reusable filler.
- Draft coherent narration first. Do not make it choppy for subtitles; the application splits GPT-SoVITS units afterward.

Return exactly one JSON object and no Markdown:
{{
  "title": "English title",
  "angle": "中文说明故事切入角度",
  "hook": "Natural English opening line",
  "selected_event_ids": [1, 2],
  "omitted_event_ids": [3],
  "outline": [{{"order": 1, "event_ids": [1], "purpose": "hook", "summary": "中文段落摘要"}}],
  "narration": [{{"id": 1, "event_ids": [1], "text_en": "English narration.", "visual_query": "需要的画面", "estimated_duration_sec": 3.2}}]
}}

SOURCE EVENTS:
{json.dumps(events, ensure_ascii=False)}
""".strip()


def _normalize_visual_plan(
    result: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    valid_ids = {int(event.get("id", 0)) for event in events}

    def ids(value: Any) -> list[int]:
        if not isinstance(value, list):
            return []
        return sorted({int(item) for item in value if str(item).isdigit() and int(item) in valid_ids})

    outline = []
    for index, item in enumerate(result.get("outline", []), start=1):
        if not isinstance(item, dict):
            continue
        event_ids = ids(item.get("event_ids"))
        if not event_ids:
            continue
        outline.append(
            {
                "order": index,
                "event_ids": event_ids,
                "stage": str(item.get("stage", "progression")).strip(),
                "visible_change": str(item.get("visible_change", "")).strip(),
                "story_function": str(item.get("story_function", "")).strip(),
                "subtext": str(item.get("subtext", "")).strip(),
                "transition": str(item.get("transition", "")).strip(),
            }
        )
    if not outline:
        raise ValueError("故事规划模型没有返回可用的全片故事阶段")
    outline_ids = {event_id for item in outline for event_id in item["event_ids"]}
    highlights = sorted(set(ids(result.get("highlight_event_ids"))) | outline_ids)
    return {
        "title": str(result.get("title", "Untitled Story")).strip(),
        "angle": str(result.get("angle", "")).strip(),
        "premise": str(result.get("premise", "")).strip(),
        "central_question": str(result.get("central_question", "")).strip(),
        "emotional_curve": str(result.get("emotional_curve", "")).strip(),
        "highlight_event_ids": highlights,
        "outline": outline,
    }


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
        natural_durations = [estimate_tts_unit_duration(unit) for unit in units]
        natural_total = sum(natural_durations)
        model_duration = float(item.get("estimated_duration_sec", 0) or 0)
        duration_scale = max(1.0, model_duration / natural_total) if natural_total else 1.0
        for unit, unit_words, natural_duration in zip(
            units,
            unit_word_counts,
            natural_durations,
        ):
            unit_duration = natural_duration * duration_scale
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

    narration = _merge_short_narration_items(narration)
    total_words = sum(int(item.get("word_count", 0)) for item in narration)
    total_duration = sum(float(item.get("estimated_duration_sec", 0)) for item in narration)

    narration_selected = {event_id for item in narration for event_id in item["event_ids"]}
    selected = sorted(narration_selected)
    omitted = sorted(valid_ids - set(selected))

    outline = []
    for index, item in enumerate(result.get("outline", []), start=1):
        if not isinstance(item, dict):
            continue
        outline_event_ids = valid_event_ids(item.get("event_ids"))
        if not outline_event_ids:
            continue
        outline.append(
            {
                "order": index,
                "event_ids": outline_event_ids,
                "purpose": str(item.get("purpose", "body")),
                "summary": str(item.get("summary", "")).strip(),
            }
        )

    return {
        "schema_version": 1,
        "timing_model": "english_word_syllable_v3",
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


def refresh_story_timing(story: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Upgrade stories saved with the old, overly-fast per-line estimates locally."""
    if not story or story.get("timing_model") == "english_word_syllable_v3":
        return story, False

    refreshed_items: list[dict[str, Any]] = []
    for raw_item in story.get("narration", []):
        if not isinstance(raw_item, dict):
            continue
        text = str(raw_item.get("text_en", "")).strip()
        if not text:
            continue
        for unit in split_gpt_sovits_units(text):
            word_count = max(1, len(re.findall(r"\b[\w'-]+\b", unit)))
            refreshed_items.append(
                {
                    "id": len(refreshed_items) + 1,
                    "event_ids": list(raw_item.get("event_ids", [])),
                    "text_en": unit,
                    "visual_query": str(raw_item.get("visual_query", "")).strip(),
                    "estimated_duration_sec": estimate_tts_unit_duration(unit),
                    "word_count": word_count,
                }
            )

    refreshed_items = _merge_short_narration_items(refreshed_items)
    refreshed = dict(story)
    refreshed["timing_model"] = "english_word_syllable_v3"
    refreshed["narration"] = refreshed_items
    refreshed["word_count"] = sum(int(item.get("word_count", 0)) for item in refreshed_items)
    refreshed["estimated_duration_sec"] = round(
        sum(float(item.get("estimated_duration_sec", 0)) for item in refreshed_items),
        2,
    )
    return refreshed, True


def _narration_event_ids(story: dict[str, Any]) -> set[int]:
    return {
        int(event_id)
        for item in story.get("narration", [])
        if isinstance(item, dict)
        for event_id in item.get("event_ids", [])
        if str(event_id).isdigit()
    }


def _covers_timeline_sections(
    story: dict[str, Any],
    events: list[dict[str, Any]],
) -> bool:
    bound_ids = _narration_event_ids(story)
    if not bound_ids:
        return False
    duration = max((float(event.get("end", 0) or 0) for event in events), default=0.0)
    if duration <= 0:
        return True
    sections = set()
    for event in events:
        event_id = int(event.get("id", 0) or 0)
        if event_id not in bound_ids:
            continue
        midpoint = (
            float(event.get("start", 0) or 0) + float(event.get("end", 0) or 0)
        ) / 2
        sections.add(min(2, int(midpoint / max(duration / 3, 0.001))))
    return sections == {0, 1, 2}


def _merge_short_narration_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None

    def combine(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
        left_text = str(left.get("text_en", "")).rstrip()
        right_text = str(right.get("text_en", "")).lstrip()
        separator = "" if left_text.endswith(("-", "—", "–")) else " "
        queries = [
            value for value in (
                str(left.get("visual_query", "")).strip(),
                str(right.get("visual_query", "")).strip(),
            )
            if value
        ]
        return {
            "id": 0,
            "event_ids": sorted(set(left.get("event_ids", [])) | set(right.get("event_ids", []))),
            "text_en": f"{left_text}{separator}{right_text}".strip(),
            "visual_query": "；".join(dict.fromkeys(queries)),
            "estimated_duration_sec": round(
                float(left.get("estimated_duration_sec", 0))
                + float(right.get("estimated_duration_sec", 0)),
                2,
            ),
            "word_count": int(left.get("word_count", 0)) + int(right.get("word_count", 0)),
        }

    for item in items:
        current = dict(item)
        if pending is not None:
            current = combine(pending, current)
            pending = None
        if int(current.get("word_count", 0)) < 3:
            if merged:
                merged[-1] = combine(merged[-1], current)
            else:
                pending = current
        else:
            merged.append(current)
    if pending is not None:
        if merged:
            merged[-1] = combine(merged[-1], pending)
        else:
            merged.append(pending)
    for index, item in enumerate(merged, start=1):
        item["id"] = index
    return merged


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
