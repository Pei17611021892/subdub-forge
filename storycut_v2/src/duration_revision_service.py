from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Callable

from .story_service import _chat_json, _normalize_story
from .vision_service import api_configuration
from .voice_service import SHORTS_MAX_DURATION_SEC


ProgressCallback = Callable[[float, str], None]


def propose_duration_revision(
    events_json: Path,
    story_json: Path,
    proposal_json: Path,
    actual_duration_sec: float,
    config: dict[str, Any],
    app_root: Path,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Create, but do not apply, a shorter story based on measured TTS speed."""
    from openai import OpenAI

    if actual_duration_sec <= SHORTS_MAX_DURATION_SEC:
        raise ValueError("当前真实配音未超过 Shorts 三分钟限制")

    events_payload = json.loads(events_json.read_text(encoding="utf-8"))
    story = json.loads(story_json.read_text(encoding="utf-8"))
    events = [dict(item) for item in events_payload.get("events", []) if isinstance(item, dict)]
    narration = [dict(item) for item in story.get("narration", []) if isinstance(item, dict)]
    if not events or not narration:
        raise ValueError("项目中没有可用于精简的事件或故事稿")

    current_words = max(
        1,
        sum(
            len(re.findall(r"\b[A-Za-z][A-Za-z'-]*\b", str(item.get("text_en", ""))))
            for item in narration
        ),
    )
    observed_words_per_second = current_words / actual_duration_sec
    target_duration_sec = 176.0
    target_words = max(
        24,
        min(current_words - 1, math.floor(target_duration_sec * observed_words_per_second * 0.97)),
    )
    minimum_duration_sec = 160.0
    minimum_words = min(
        target_words,
        max(20, math.ceil(minimum_duration_sec * observed_words_per_second)),
    )
    if target_words >= current_words:
        raise ValueError("当前文案无需 AI 精简，可使用安全加速适配 Shorts")

    bound_ids = {
        int(event_id)
        for item in narration
        for event_id in item.get("event_ids", [])
        if str(event_id).isdigit()
    }
    compact_events = [
        {
            "id": int(event.get("id", 0) or 0),
            "start": event.get("start", 0),
            "end": event.get("end", 0),
            "transcript": str(event.get("transcript", "")),
            "visual": str(event.get("visual_description", "")),
            "story_value": str(event.get("story_value", "")),
            "screen_text": event.get("screen_text", []),
        }
        for event in events
        if int(event.get("id", 0) or 0) in bound_ids
    ]

    story_config = config.get("story", {})
    api = api_configuration(config, app_root, "story")
    if not str(api.get("api_key", "")).strip():
        raise RuntimeError("未配置 OPENAI_API_KEY，无法生成配音超时精简方案")
    model = str(story_config.get("editor_model", "")).strip() or str(
        story_config.get("model", "gpt-4o-mini")
    )
    temperature = max(0.0, min(1.0, float(story_config.get("temperature", 0.55))))
    client = OpenAI(api_key=str(api["api_key"]), base_url=str(api.get("base_url", "")).strip() or None)

    if progress:
        progress(
            0.1,
            f"正在根据真实配音速度计算删减量，目标 {minimum_words}–{target_words} 词…",
        )
    result = _chat_json(
        client,
        model,
        _build_revision_prompt(
            story,
            compact_events,
            actual_duration_sec,
            minimum_words,
            target_words,
        ),
        temperature,
        str(api.get("base_url", "")).strip() or None,
        "配音超时精简",
    )
    revised_raw = result.get("revised_story", result)
    if not isinstance(revised_raw, dict):
        raise RuntimeError("故事编辑模型未返回完整的新故事稿")
    revised = _normalize_story(revised_raw, events, round(target_duration_sec), model)
    projected_duration = revised.get("word_count", 0) / max(observed_words_per_second, 0.01)
    for retry_index in range(1, 3):
        revised_words = int(revised.get("word_count", 0) or 0)
        too_short = revised_words < minimum_words or projected_duration < minimum_duration_sec
        too_long = (
            revised_words > math.ceil(target_words * 1.03)
            or projected_duration >= SHORTS_MAX_DURATION_SEC
        )
        if not too_short and not too_long:
            break
        issue = "压缩过度、损失内容太多" if too_short else "仍可能超过 Shorts 安全线"
        if progress:
            progress(
                0.48 + retry_index * 0.2,
                f"第 {retry_index} 版{issue}，正在整篇重新平衡…",
            )
        retry = _chat_json(
            client,
            model,
            _build_retry_prompt(
                revised,
                compact_events,
                minimum_words,
                target_words,
                issue,
            ),
            max(0.1, temperature - 0.1),
            str(api.get("base_url", "")).strip() or None,
            "配音超时平衡重编",
        )
        retry_raw = retry.get("revised_story", retry)
        if not isinstance(retry_raw, dict):
            raise RuntimeError("故事编辑模型未返回可用的平衡重编稿")
        revised = _normalize_story(retry_raw, events, round(target_duration_sec), model)
        projected_duration = revised.get("word_count", 0) / max(observed_words_per_second, 0.01)

    revised_words = int(revised.get("word_count", 0) or 0)
    if (
        revised_words < minimum_words
        or projected_duration < minimum_duration_sec
        or projected_duration >= SHORTS_MAX_DURATION_SEC
    ):
        raise RuntimeError(
            "AI 两次平衡重编后仍未进入可用区间："
            f"当前 {revised_words} 词、按本次真实语速约 {projected_duration:.0f} 秒；"
            f"需要约 {minimum_words}–{target_words} 词并控制在 "
            f"{minimum_duration_sec:.0f}–{SHORTS_MAX_DURATION_SEC:.0f} 秒。"
            "请选择更强的最终故事编辑模型后重试。"
        )

    _apply_measured_voice_timing(revised, projected_duration)

    changes = result.get("removed_or_merged", [])
    if not isinstance(changes, list):
        changes = []
    proposal = {
        "schema_version": 2,
        "model": model,
        "actual_duration_sec": round(actual_duration_sec, 2),
        "target_duration_sec": target_duration_sec,
        "current_word_count": current_words,
        "target_word_count": target_words,
        "minimum_word_count": minimum_words,
        "minimum_duration_sec": minimum_duration_sec,
        "revised_word_count": int(revised.get("word_count", 0)),
        "projected_duration_sec": round(projected_duration, 2),
        "summary_zh": str(result.get("summary_zh", "已保留主线并压缩重复、铺垫与次要说明。")),
        "removed_or_merged": [str(item).strip() for item in changes if str(item).strip()],
        "revised_story": revised,
    }
    proposal_json.parent.mkdir(parents=True, exist_ok=True)
    proposal_json.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
    if progress:
        progress(1.0, "精简方案已生成，请确认前后对比")
    return proposal


def _apply_measured_voice_timing(story: dict[str, Any], projected_duration: float) -> None:
    """Scale every narration beat to the speaking rate measured from real TTS."""
    narration = [item for item in story.get("narration", []) if isinstance(item, dict)]
    current_total = sum(float(item.get("estimated_duration_sec", 0) or 0) for item in narration)
    if not narration or current_total <= 0 or projected_duration <= 0:
        return
    scale = projected_duration / current_total
    running_total = 0.0
    for index, item in enumerate(narration):
        if index == len(narration) - 1:
            duration = max(0.75, projected_duration - running_total)
        else:
            duration = max(0.75, float(item.get("estimated_duration_sec", 0) or 0) * scale)
        item["estimated_duration_sec"] = round(duration, 2)
        running_total += duration
    story["estimated_duration_sec"] = round(
        sum(float(item.get("estimated_duration_sec", 0) or 0) for item in narration),
        2,
    )
    story["timing_model"] = "measured_voice_projection_v1"


def _build_revision_prompt(
    story: dict[str, Any],
    events: list[dict[str, Any]],
    actual_duration_sec: float,
    minimum_words: int,
    target_words: int,
) -> str:
    return f"""You are the final English documentary editor for a YouTube Shorts video.
The current real TTS lasts {actual_duration_sec:.1f} seconds and is too long. Rewrite the WHOLE narration to {minimum_words}-{target_words} English words. Staying inside this range is mandatory: do not over-compress it into a much shorter script.

Requirements:
- Preserve the central question, hook, causal chain, emotional arc, strongest visual moments, factual meaning, chronology, and valid event IDs.
- Remove repetition, throat-clearing, decorative lines that do not advance the story, and secondary explanations first.
- Merge adjacent ideas when the same shot can support them. Do not merely truncate the ending.
- Keep enough supported detail to use roughly 160-176 seconds at the measured speaking speed. A result below {minimum_words} words is a failure, not an improvement.
- Never invent facts, motives, dialogue, sounds, or actions not supported by the evidence.
- Write natural spoken English, not a dry screen description and not generic AI prose.
- Each narration item should usually contain 5-16 words. Avoid isolated connectors such as "Of course," or "All right,".
- Every narration item must include event_ids, text_en, visual_query and estimated_duration_sec.
- Return JSON only with summary_zh, removed_or_merged, and revised_story.
- revised_story must contain title, angle, hook, outline and narration.

CURRENT STORY:
{json.dumps(story, ensure_ascii=False)}

BOUND SOURCE EVIDENCE:
{json.dumps(events, ensure_ascii=False)}
"""


def _build_retry_prompt(
    story: dict[str, Any],
    events: list[dict[str, Any]],
    minimum_words: int,
    target_words: int,
    issue: str,
) -> str:
    return f"""The revised English documentary narration was rejected because it was {issue}. Rewrite the WHOLE story to STRICTLY {minimum_words}-{target_words} English words while preserving its hook, complete ending, causal chain, strongest supported details and valid event IDs. If the draft is too short, restore useful mechanisms, consequences and strong examples from the source without filler. If it is too long, remove repetition and secondary detail. Never invent facts. Return JSON only as revised_story with title, angle, hook, outline and narration. Each narration item needs event_ids, text_en, visual_query and estimated_duration_sec.

DRAFT:
{json.dumps(story, ensure_ascii=False)}

SOURCE EVIDENCE:
{json.dumps(events, ensure_ascii=False)}
"""
