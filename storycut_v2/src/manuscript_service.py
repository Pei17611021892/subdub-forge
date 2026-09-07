from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Callable

from .story_service import (
    narrative_strategy_label,
    normalize_narrative_strategy,
)
from .vision_service import api_configuration, friendly_api_error
from .voice_service import (
    SHORTS_MAX_DURATION_SEC,
    estimate_tts_unit_duration,
    split_gpt_sovits_units,
)


ProgressCallback = Callable[[float, str], None]

SHOT_ROLES = {
    "hook",
    "establishing",
    "subject_behavior",
    "detail",
    "evidence",
    "mechanism",
    "diagram",
    "transition",
    "payoff",
}
MEDIA_KINDS = {"video", "photo", "diagram", "text_card", "either"}


def generate_manuscript_storyboard(
    manuscript_file: Path,
    story_json: Path,
    storyboard_json: Path,
    events_json: Path,
    target_duration_sec: int,
    config: dict[str, Any],
    app_root: Path,
    progress: ProgressCallback,
    narrative_strategy: str = "auto",
    planning_words_per_second: float = 1.45,
) -> dict[str, Any]:
    """Create an English narration and shot requirements with one text-model request."""
    manuscript = manuscript_file.read_text(encoding="utf-8-sig").strip()
    if not manuscript:
        raise ValueError("文稿为空，请先在第一步填写并保存文稿")

    api = api_configuration(config, app_root, "story")
    api_key = str(api["api_key"])
    base_url = str(api["base_url"]).strip() or None
    if not api_key:
        raise RuntimeError("未配置 OPENAI_API_KEY，无法规划分镜")

    story_config = config.get("story", {})
    model = (
        str(story_config.get("editor_model", "")).strip()
        or str(story_config.get("model", "gpt-4o-mini")).strip()
    )
    temperature = max(0.0, min(1.0, float(story_config.get("temperature", 0.45))))
    requested_strategy = normalize_narrative_strategy(narrative_strategy)
    effective_wps = max(0.9, min(2.2, float(planning_words_per_second or 1.45)))
    target_duration = max(20, min(int(target_duration_sec), 180))
    safe_duration = min(float(target_duration), SHORTS_MAX_DURATION_SEC - 5.0)
    max_words = max(35, int(safe_duration * effective_wps))
    # A narration beat is allowed to use more than one cut later.  Asking the
    # model for one fully-described beat every five seconds made a 180-second
    # plan balloon to 36 large JSON objects and could leave slower reasoning
    # models waiting for a very long response.
    suggested_beats = max(5, min(24, round(safe_duration / 8.0)))
    request_timeout_sec = max(
        30.0,
        min(600.0, float(story_config.get("request_timeout_sec", 240) or 240)),
    )

    progress(
        0.08,
        f"正在使用 {model} 理解文稿并提取叙事结构；模型生成通常需要 1–3 分钟…",
    )
    prompt = _build_prompt(
        manuscript,
        target_duration,
        max_words,
        suggested_beats,
        requested_strategy,
    )

    from openai import OpenAI

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=request_timeout_sec,
        max_retries=1,
    )
    result = _chat_json(client, model, prompt, temperature, base_url)
    progress(0.68, "正在校验英文断句和逐段镜头需求…")
    story, storyboard = _normalize_result(
        result,
        target_duration,
        model,
        effective_wps,
        requested_strategy,
    )
    if not story["narration"]:
        raise RuntimeError("模型没有返回可用的英文解说与分镜")
    story_json.parent.mkdir(parents=True, exist_ok=True)
    storyboard_json.parent.mkdir(parents=True, exist_ok=True)
    events_json.parent.mkdir(parents=True, exist_ok=True)
    story_json.write_text(
        json.dumps(story, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    storyboard_json.write_text(
        json.dumps(storyboard, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    events_json.write_text(
        json.dumps(
            _build_manuscript_events(manuscript),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    progress(
        1.0,
        f"分镜规划完成：{storyboard['shot_segment_count']} 个镜头段，"
        f"建议至少准备 {storyboard['recommended_asset_count']} 个素材文件",
    )
    return {"story": story, "storyboard": storyboard}


def _build_prompt(
    manuscript: str,
    target_duration_sec: int,
    max_words: int,
    suggested_beats: int,
    narrative_strategy: str,
) -> str:
    strategy_instruction = (
        "Choose the best strategy automatically and return its key."
        if narrative_strategy == "auto"
        else "Do not impose a named strategy; preserve the manuscript's own structure."
        if narrative_strategy == "none"
        else f"Use the {narrative_strategy} strategy as a flexible editorial skeleton."
    )
    return f"""
You are a senior documentary editor preparing an English YouTube Shorts video from a Chinese manuscript.
This is a script-to-video planning task. There is no original source video.

PRIMARY GOAL
- Preserve the manuscript's factual meaning, central logic, uncertainty, important mechanism, and conclusion.
- Produce natural spoken English for a finished narration of about {target_duration_sec} seconds.
- Stay at or below {max_words} English words and safely below three minutes.
- Create roughly {suggested_beats} narration beats, but use fewer when the source is short.
- One narration beat may later use multiple cuts. Do not inflate the JSON merely to create more cuts.
- {strategy_instruction}

NARRATION RULES
- Do not add facts, studies, statistics, identities, quotations, dates, motives, or conclusions absent from the manuscript.
- Improve spoken flow without turning the text into generic AI hype.
- Each beat must contain exactly one independently speakable GPT-SoVITS unit.
- Commas, periods, question marks, exclamation marks, colons and semicolons all split TTS units.
- Avoid commas inside a beat. Never create one-word reactions or dependent fragments.
- Most beats should contain 8-18 English words and express one complete idea.

VISUAL PLANNING RULES
- Do not ask only for generic subject footage. State the action, framing, environment, relationship, or explanatory purpose needed.
- Use evidence footage only when the image can genuinely support the claim.
- Invisible mechanisms, comparisons, sound, scale, chronology, numbers, or disputed concepts should prefer a diagram, text card, map, waveform, or labeled graphic.
- For each beat provide an exact ideal shot plus only the most important required elements, fallback, and content to avoid.
- Keep must_show_zh, acceptable_fallbacks_zh and avoid_zh to 1-3 short items each.
- Search queries must be concise English stock-footage queries. Provide exactly 2 queries when possible.
- shot_role must be one of: hook, establishing, subject_behavior, detail, evidence, mechanism, diagram, transition, payoff.
- media_kind must be one of: video, photo, diagram, text_card, either.

Return exactly one JSON object and no Markdown:
{{
  "title_zh": "中文标题",
  "title_en": "English title",
  "summary_zh": "一句中文说明本片的叙事主线",
  "narrative_strategy": "science_explainer|engineering_process|nature_observation|human_documentary|cosmic_history|general_story|none",
  "narrative_strategy_reason_zh": "一句中文原因",
  "outline": [
    {{"purpose": "hook|setup|mechanism|evidence|turn|conclusion", "summary_zh": "中文阶段摘要"}}
  ],
  "beats": [
    {{
      "source_excerpt_zh": "本段对应的原稿短句",
      "text_en": "One complete spoken English sentence.",
      "section_title_zh": "本段所属叙事章节；同章节使用完全相同的短标题",
      "shot_role": "evidence",
      "media_kind": "video",
      "ideal_shot_zh": "具体理想画面",
      "must_show_zh": ["必须可见内容"],
      "acceptable_fallbacks_zh": ["可接受的替代画面"],
      "avoid_zh": ["容易误导或不应使用的画面"],
      "search_queries_en": ["concise stock footage query"],
      "editor_note_zh": "仅在必须解释选镜理由时填写；否则留空"
    }}
  ]
}}

CHINESE MANUSCRIPT:
{manuscript}
""".strip()


def _chat_json(
    client: Any,
    model: str,
    prompt: str,
    temperature: float,
    base_url: str | None,
) -> dict[str, Any]:
    try:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
            )
        except Exception as exc:
            if getattr(exc, "status_code", None) == 400 and "temperature" in str(exc).lower():
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                )
            else:
                raise
    except Exception as exc:
        raise friendly_api_error(exc, base_url, "纯文稿分镜规划") from exc
    return _parse_json_object(str(response.choices[0].message.content or ""))


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, flags=re.DOTALL)
    if fenced:
        cleaned = fenced.group(1)
    if not cleaned.startswith("{"):
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("分镜规划接口没有返回 JSON 对象")
    return value


def _normalize_result(
    result: dict[str, Any],
    target_duration_sec: int,
    model: str,
    planning_words_per_second: float,
    requested_strategy: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    narration: list[dict[str, Any]] = []
    beats: list[dict[str, Any]] = []
    for raw in result.get("beats", []):
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text_en", "")).strip()
        if not text:
            continue
        units = split_gpt_sovits_units(text)
        if not units:
            continue
        for unit in units:
            words = max(1, len(re.findall(r"\b[\w'-]+\b", unit)))
            duration = estimate_tts_unit_duration(unit)
            queries = _string_list(raw.get("search_queries_en"), limit=4)
            narration_id = len(narration) + 1
            narration.append(
                {
                    "id": narration_id,
                    "event_ids": [],
                    "text_en": unit,
                    "visual_query": queries[0] if queries else str(raw.get("ideal_shot_zh", "")).strip(),
                    "estimated_duration_sec": round(duration, 2),
                    "word_count": words,
                }
            )
            role = str(raw.get("shot_role", "subject_behavior")).strip()
            media_kind = str(raw.get("media_kind", "video")).strip()
            beats.append(
                {
                    "id": narration_id,
                    "narration_id": narration_id,
                    "source_excerpt_zh": str(raw.get("source_excerpt_zh", "")).strip(),
                    "section_title_zh": str(raw.get("section_title_zh", "")).strip(),
                    "text_en": unit,
                    "estimated_duration_sec": round(duration, 2),
                    "shot_role": role if role in SHOT_ROLES else "subject_behavior",
                    "media_kind": media_kind if media_kind in MEDIA_KINDS else "video",
                    "ideal_shot_zh": str(raw.get("ideal_shot_zh", "")).strip(),
                    "must_show_zh": _string_list(raw.get("must_show_zh"), limit=6),
                    "acceptable_fallbacks_zh": _string_list(
                        raw.get("acceptable_fallbacks_zh"), limit=5
                    ),
                    "avoid_zh": _string_list(raw.get("avoid_zh"), limit=5),
                    "search_queries_en": queries,
                    "editor_note_zh": str(raw.get("editor_note_zh", "")).strip(),
                    "match_status": "missing",
                    "match_confidence": 0,
                    "selected_asset_id": "",
                }
            )

    if narration:
        natural_total = sum(float(item["estimated_duration_sec"]) for item in narration)
        words_total = sum(int(item["word_count"]) for item in narration)
        planned_total = words_total / planning_words_per_second
        if natural_total > 0:
            scale = planned_total / natural_total
            for item, beat in zip(narration, beats):
                adjusted = round(float(item["estimated_duration_sec"]) * scale, 2)
                item["estimated_duration_sec"] = adjusted
                beat["estimated_duration_sec"] = adjusted

    resolved_strategy = normalize_narrative_strategy(
        result.get("narrative_strategy", requested_strategy)
    )
    if resolved_strategy in {"auto"}:
        resolved_strategy = "general_story"
    outline = []
    for index, raw in enumerate(result.get("outline", []), start=1):
        if not isinstance(raw, dict):
            continue
        outline.append(
            {
                "order": index,
                "event_ids": [],
                "purpose": str(raw.get("purpose", "body")).strip(),
                "summary": str(raw.get("summary_zh", "")).strip(),
            }
        )
    total_duration = round(
        sum(float(item["estimated_duration_sec"]) for item in narration), 2
    )
    total_words = sum(int(item["word_count"]) for item in narration)
    title_en = str(result.get("title_en", "Untitled Story")).strip()
    story = {
        "schema_version": 2,
        "content_mode": "manuscript",
        "workflow": "manuscript_storyboard_v1",
        "timing_model": "planning_voice_rate_v1",
        "model": model,
        "target_duration_sec": target_duration_sec,
        "estimated_duration_sec": total_duration,
        "word_count": total_words,
        "title": title_en,
        "title_zh": str(result.get("title_zh", "")).strip(),
        "angle": str(result.get("summary_zh", "")).strip(),
        "hook": str(narration[0]["text_en"]) if narration else "",
        "selected_event_ids": [],
        "omitted_event_ids": [],
        "outline": outline,
        "narration": narration,
        "planning_words_per_second": round(planning_words_per_second, 3),
        "narrative_strategy_requested": requested_strategy,
        "narrative_strategy": resolved_strategy,
        "narrative_strategy_label": narrative_strategy_label(resolved_strategy),
        "narrative_strategy_reason_zh": str(
            result.get("narrative_strategy_reason_zh", "")
        ).strip(),
    }
    recommended_assets = (
        max(1, min(len(beats), math.ceil(total_duration / 10.0))) if beats else 0
    )
    storyboard = {
        "schema_version": 1,
        "model": model,
        "target_duration_sec": target_duration_sec,
        "title_zh": str(result.get("title_zh", "")).strip(),
        "title_en": title_en,
        "summary_zh": str(result.get("summary_zh", "")).strip(),
        "shot_segment_count": len(beats),
        "recommended_asset_count": recommended_assets,
        "matched_count": 0,
        "coverage_percent": 0,
        "stale": False,
        "beats": beats,
    }
    return story, storyboard


def split_overlong_manuscript_result(
    result: dict[str, Any],
    max_duration_sec: float = 174.0,
    max_parts: int = 6,
) -> list[dict[str, Any]]:
    """Split an overlong manuscript result without another model request.

    The split is contiguous and balanced, so it never breaks a spoken unit or
    changes narrative order. Nearby section boundaries are preferred when they
    keep every episode inside the requested limit.
    """
    story = dict(result.get("story", {}))
    storyboard = dict(result.get("storyboard", {}))
    narration = [dict(item) for item in story.get("narration", []) if isinstance(item, dict)]
    beats = [dict(item) for item in storyboard.get("beats", []) if isinstance(item, dict)]
    if not narration or len(narration) != len(beats):
        raise ValueError("纯文稿旁白与分镜数量不一致，无法安全自动拆分")
    total = sum(float(item.get("estimated_duration_sec", 0) or 0) for item in narration)
    if total < SHORTS_MAX_DURATION_SEC:
        return [result]
    safe_limit = max(60.0, min(float(max_duration_sec), SHORTS_MAX_DURATION_SEC - 1.0))
    part_count = max(2, math.ceil(total / safe_limit))
    if part_count > max(2, int(max_parts)):
        raise RuntimeError(
            f"当前旁白约 {total:.0f} 秒，至少需要拆成 {part_count} 集，超过当前最多 {max_parts} 集限制"
        )

    boundaries = _balanced_part_boundaries(narration, beats, part_count, safe_limit)
    parts: list[dict[str, Any]] = []
    start = 0
    for part_index, end in enumerate(boundaries + [len(narration)], start=1):
        part_narration = [dict(item) for item in narration[start:end]]
        part_beats = [dict(item) for item in beats[start:end]]
        for new_id, (narration_item, beat) in enumerate(
            zip(part_narration, part_beats), start=1
        ):
            narration_item["id"] = new_id
            beat["id"] = new_id
            beat["narration_id"] = new_id
        part_duration = round(
            sum(float(item.get("estimated_duration_sec", 0) or 0) for item in part_narration),
            2,
        )
        if part_duration >= SHORTS_MAX_DURATION_SEC:
            raise RuntimeError(
                f"第 {part_index} 集预计仍有 {part_duration:.0f} 秒，无法在完整句边界内安全拆分"
            )
        part_words = sum(int(item.get("word_count", 0) or 0) for item in part_narration)
        part_story = dict(story)
        part_story.update(
            {
                "narration": part_narration,
                "word_count": part_words,
                "estimated_duration_sec": part_duration,
                "hook": str(part_narration[0].get("text_en", "")),
                "series_part_index": part_index,
                "series_part_count": part_count,
                "series_title_zh": _part_title(part_beats, part_index),
            }
        )
        part_storyboard = dict(storyboard)
        part_storyboard.update(
            {
                "beats": part_beats,
                "shot_segment_count": len(part_beats),
                "recommended_asset_count": max(
                    1, min(len(part_beats), math.ceil(part_duration / 10.0))
                ),
                "title_zh": _part_title(part_beats, part_index),
                "matched_count": 0,
                "coverage_percent": 0,
            }
        )
        source_excerpt = "\n".join(
            dict.fromkeys(
                str(item.get("source_excerpt_zh", "")).strip()
                for item in part_beats
                if str(item.get("source_excerpt_zh", "")).strip()
            )
        )
        part_events = _build_manuscript_events(source_excerpt)
        event_ids = [int(item.get("id", 0) or 0) for item in part_events["events"]]
        parts.append(
            {
                "plan": {
                    "part_index": part_index,
                    "title_zh": _part_title(part_beats, part_index),
                    "purpose_zh": str(part_story.get("angle", "")),
                    "event_ids": event_ids,
                },
                "story": part_story,
                "storyboard": part_storyboard,
                "events": part_events,
                "source_manuscript": source_excerpt,
            }
        )
        start = end
    return parts


def _balanced_part_boundaries(
    narration: list[dict[str, Any]],
    beats: list[dict[str, Any]],
    part_count: int,
    safe_limit: float,
) -> list[int]:
    durations = [float(item.get("estimated_duration_sec", 0) or 0) for item in narration]
    total = sum(durations)
    cumulative: list[float] = []
    running = 0.0
    for duration in durations:
        running += duration
        cumulative.append(running)
    boundaries: list[int] = []
    previous = 0
    for part_number in range(1, part_count):
        ideal = total * part_number / part_count
        remaining_parts = part_count - part_number
        candidates = range(previous + 1, len(narration) - remaining_parts + 1)

        def score(index: int) -> tuple[float, float]:
            section_before = str(beats[index - 1].get("section_title_zh", "")).strip()
            section_after = str(beats[index].get("section_title_zh", "")).strip()
            section_bonus = 12.0 if section_before and section_after and section_before != section_after else 0.0
            return (abs(cumulative[index - 1] - ideal) - section_bonus, abs(cumulative[index - 1] - ideal))

        valid = [
            index
            for index in candidates
            if cumulative[index - 1] - (cumulative[previous - 1] if previous else 0.0)
            <= safe_limit
        ]
        chosen = min(valid or list(candidates), key=score)
        boundaries.append(chosen)
        previous = chosen
    return boundaries


def _part_title(beats: list[dict[str, Any]], part_index: int) -> str:
    for beat in beats:
        title = str(beat.get("section_title_zh", "")).strip()
        if title:
            return title
    return f"第 {part_index} 集"


def _string_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:limit]


def _build_manuscript_events(manuscript: str) -> dict[str, Any]:
    chunks = [
        item.strip()
        for item in re.split(r"(?<=[。！？!?])\s*|\n{2,}", manuscript)
        if item.strip()
    ]
    events = [
        {
            "id": index,
            "start": 0.0,
            "end": 0.0,
            "transcript": text,
            "visual_description": "",
            "story_value": "source_manuscript",
            "continuity": "",
        }
        for index, text in enumerate(chunks, start=1)
    ]
    return {
        "schema_version": 1,
        "content_mode": "manuscript",
        "source": "source/manuscript.txt",
        "events": events,
    }
