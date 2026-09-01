from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .story_service import _chat_json
from .vision_service import api_configuration


ProgressCallback = Callable[[float, str], None]


def analyze_layered_structure(
    events_json: Path,
    output_json: Path,
    config: dict[str, Any],
    app_root: Path,
    progress: ProgressCallback,
) -> dict[str, Any]:
    """Summarize event chunks first, then synthesize one whole-video structure."""
    from openai import OpenAI

    payload = json.loads(events_json.read_text(encoding="utf-8"))
    events = [dict(item) for item in payload.get("events", []) if isinstance(item, dict)]
    if not events:
        raise ValueError("没有可用于分层理解的原片事件")

    settings = config.get("layered_analysis", {})
    story_settings = config.get("story", {})
    api = api_configuration(config, app_root, "story")
    api_key = str(api.get("api_key", "")).strip()
    if not api_key:
        raise RuntimeError("未配置 OPENAI_API_KEY，无法执行分层理解")
    base_url = str(api.get("base_url", "")).strip() or None
    model = (
        str(settings.get("model", "")).strip()
        or str(story_settings.get("editor_model", "")).strip()
        or str(story_settings.get("model", "gpt-4o-mini")).strip()
    )
    temperature = max(0.0, min(1.0, float(settings.get("temperature", 0.25) or 0.25)))
    chunk_duration = max(60.0, float(settings.get("chunk_duration_sec", 180) or 180))
    max_events = max(12, int(settings.get("max_events_per_chunk", 45) or 45))
    max_chunks = max(2, int(settings.get("max_chunks", 10) or 10))
    chunks = _chunk_events(events, chunk_duration, max_events, max_chunks)
    client = OpenAI(api_key=api_key, base_url=base_url)

    chapter_summaries: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks, start=1):
        progress(
            (index - 1) / (len(chunks) + 1),
            f"分层理解：正在分析第 {index}/{len(chunks)} 个片段…",
        )
        result = _chat_json(
            client,
            model,
            _chapter_prompt(index, len(chunks), chunk, str(payload.get("content_mode", "speech"))),
            temperature,
            base_url,
            f"分层理解片段 {index}",
        )
        chapter_summaries.append(_normalize_chapter(result, index, chunk))

    if len(chapter_summaries) == 1:
        progress(0.9, "分层理解：正在整理全片重点…")
        normalized = _single_chapter_structure(chapter_summaries[0])
    else:
        progress(
            len(chunks) / (len(chunks) + 1),
            "分层理解：正在连接全片章节与故事线…",
        )
        synthesis = _chat_json(
            client,
            model,
            _synthesis_prompt(chapter_summaries),
            temperature,
            base_url,
            "分层理解全片综合",
        )
        normalized = _normalize_synthesis(synthesis, chapter_summaries, events)
    result = {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "workflow": "automatic_layered_analysis_v2",
        "source_event_count": len(events),
        "chunk_count": len(chunks),
        "chapters": chapter_summaries,
        **normalized,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    progress(1.0, f"分层理解完成：整理出 {len(chapter_summaries)} 个章节")
    return result


def _single_chapter_structure(chapter: dict[str, Any]) -> dict[str, Any]:
    highlights = sorted(
        {
            int(event_id)
            for key in ("highlight_event_ids", "turning_point_event_ids")
            for event_id in chapter.get(key, [])
            if str(event_id).isdigit()
        }
    )
    summary = str(chapter.get("summary_zh", "")).strip()
    progression = str(chapter.get("progression_zh", "")).strip()
    return {
        "whole_video_summary_zh": summary,
        "central_thread_zh": progression or summary,
        "global_progression_zh": progression,
        "cross_chapter_connections": [],
        "global_turning_point_event_ids": list(chapter.get("turning_point_event_ids", [])),
        "recommended_highlight_event_ids": highlights,
        "routine_or_repetitive_event_ids": [],
        "story_angles": [],
        "editorial_cautions_zh": [],
    }


def _chunk_events(
    events: list[dict[str, Any]],
    target_duration: float,
    max_events: int,
    max_chunks: int,
) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    chunk_start = 0.0
    for event in events:
        start = float(event.get("start", 0) or 0)
        if not current:
            chunk_start = start
        if current and (len(current) >= max_events or start - chunk_start >= target_duration):
            chunks.append(current)
            current = []
            chunk_start = start
        current.append(event)
    if current:
        chunks.append(current)
    if len(chunks) > max_chunks:
        original = chunks
        chunks = [[] for _ in range(max_chunks)]
        for index, chunk in enumerate(original):
            bucket = min(max_chunks - 1, index * max_chunks // len(original))
            chunks[bucket].extend(chunk)
    return chunks


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(event.get("id", 0) or 0),
        "start": round(float(event.get("start", 0) or 0), 2),
        "end": round(float(event.get("end", 0) or 0), 2),
        "transcript": str(event.get("transcript", "")).strip()[:700],
        "visual": str(event.get("visual_description", "")).strip()[:500],
        "story_value": str(event.get("story_value", "")).strip()[:260],
        "continuity": str(event.get("continuity", "")).strip()[:220],
        "screen_text": event.get("screen_text", []),
        "technical_visual": event.get("technical_visual", {}),
    }


def _chapter_prompt(
    index: int,
    total: int,
    events: list[dict[str, Any]],
    content_mode: str,
) -> str:
    compact = [_compact_event(event) for event in events]
    return f"""
You are analyzing chapter {index} of {total} from a long source video before any short script is written.
Content mode: {content_mode}.

Identify what changes across this chapter rather than listing frames. Preserve exact evidence and uncertainty.
Connect visible actions, spoken facts, practical purpose, obstacles, reactions, before/after states, diagrams and on-screen labels.
Do not invent motivation, identity, causal claims, results, sounds, weather or private thoughts.

Return one JSON object and no Markdown:
{{
  "summary_zh": "本段发生了什么以及为何重要",
  "progression_zh": "本段内部的起因、变化、转折和结果",
  "key_facts": ["有原片证据支持的事实"],
  "highlight_event_ids": [1, 2],
  "turning_point_event_ids": [2],
  "open_threads_zh": ["需要与前后片段连接的问题或线索"],
  "continuity_zh": "人物、对象、地点、任务或论证如何延续"
}}

CHRONOLOGICAL EVENTS:
{json.dumps(compact, ensure_ascii=False)}
""".strip()


def _synthesis_prompt(chapters: list[dict[str, Any]]) -> str:
    return f"""
You are the senior producer synthesizing chapter-level analysis of an entire long video.
Do not write final narration. Discover the strongest truthful global structure that a later Shorts editor can use.

- Connect causes, consequences, repeated attempts, changing states, recurring subjects and cross-chapter payoffs.
- Distinguish central story from routine repetition and side material.
- Preserve chronology when it matters, but identify callbacks and evidence that explain earlier scenes.
- Suggest several viable story angles without forcing emotion or inventing motivation.
- Highlight the strongest events across the beginning, middle and end.

Return one JSON object and no Markdown:
{{
  "whole_video_summary_zh": "全片内容与变化的准确摘要",
  "central_thread_zh": "贯穿全片的核心问题、任务或论证",
  "global_progression_zh": "全片从开始到结束的阶段变化",
  "cross_chapter_connections": [
    {{"from_chapter": 1, "to_chapter": 2, "connection_zh": "因果、延续、对照或回收关系"}}
  ],
  "global_turning_point_event_ids": [1, 2],
  "recommended_highlight_event_ids": [1, 2],
  "routine_or_repetitive_event_ids": [3],
  "story_angles": [
    {{"title_zh": "角度", "reason_zh": "为何适合", "event_ids": [1, 2]}}
  ],
  "editorial_cautions_zh": ["容易误读或不应推断的内容"]
}}

CHAPTER ANALYSIS:
{json.dumps(chapters, ensure_ascii=False)}
""".strip()


def _valid_ids(values: Any, valid: set[int]) -> list[int]:
    if not isinstance(values, list):
        return []
    return sorted({int(value) for value in values if str(value).isdigit() and int(value) in valid})


def _normalize_chapter(
    result: dict[str, Any], index: int, events: list[dict[str, Any]]
) -> dict[str, Any]:
    valid = {int(item.get("id", 0) or 0) for item in events}
    start = min((float(item.get("start", 0) or 0) for item in events), default=0.0)
    end = max((float(item.get("end", 0) or 0) for item in events), default=start)
    return {
        "chapter_id": index,
        "start": round(start, 2),
        "end": round(end, 2),
        "event_ids": sorted(valid),
        "summary_zh": str(result.get("summary_zh", "")).strip(),
        "progression_zh": str(result.get("progression_zh", "")).strip(),
        "key_facts": [str(item).strip() for item in result.get("key_facts", []) if str(item).strip()],
        "highlight_event_ids": _valid_ids(result.get("highlight_event_ids"), valid),
        "turning_point_event_ids": _valid_ids(result.get("turning_point_event_ids"), valid),
        "open_threads_zh": [str(item).strip() for item in result.get("open_threads_zh", []) if str(item).strip()],
        "continuity_zh": str(result.get("continuity_zh", "")).strip(),
    }


def _normalize_synthesis(
    result: dict[str, Any],
    chapters: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    valid = {int(item.get("id", 0) or 0) for item in events}
    chapter_ids = {int(item.get("chapter_id", 0) or 0) for item in chapters}
    connections = []
    for item in result.get("cross_chapter_connections", []):
        if not isinstance(item, dict):
            continue
        source = int(item.get("from_chapter", 0) or 0)
        target = int(item.get("to_chapter", 0) or 0)
        if source in chapter_ids and target in chapter_ids:
            connections.append(
                {
                    "from_chapter": source,
                    "to_chapter": target,
                    "connection_zh": str(item.get("connection_zh", "")).strip(),
                }
            )
    angles = []
    for item in result.get("story_angles", []):
        if isinstance(item, dict):
            angles.append(
                {
                    "title_zh": str(item.get("title_zh", "")).strip(),
                    "reason_zh": str(item.get("reason_zh", "")).strip(),
                    "event_ids": _valid_ids(item.get("event_ids"), valid),
                }
            )
    return {
        "whole_video_summary_zh": str(result.get("whole_video_summary_zh", "")).strip(),
        "central_thread_zh": str(result.get("central_thread_zh", "")).strip(),
        "global_progression_zh": str(result.get("global_progression_zh", "")).strip(),
        "cross_chapter_connections": connections,
        "global_turning_point_event_ids": _valid_ids(result.get("global_turning_point_event_ids"), valid),
        "recommended_highlight_event_ids": _valid_ids(result.get("recommended_highlight_event_ids"), valid),
        "routine_or_repetitive_event_ids": _valid_ids(result.get("routine_or_repetitive_event_ids"), valid),
        "story_angles": angles,
        "editorial_cautions_zh": [
            str(item).strip() for item in result.get("editorial_cautions_zh", []) if str(item).strip()
        ],
    }
