from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


def generate_shot_matches(
    story_json: Path,
    events_json: Path,
    matches_json: Path,
) -> dict[str, Any]:
    """Build editable shot candidates without making another API request."""
    story = json.loads(story_json.read_text(encoding="utf-8"))
    events_payload = json.loads(events_json.read_text(encoding="utf-8"))
    events = [dict(item) for item in events_payload.get("events", []) if isinstance(item, dict)]
    if not events:
        raise ValueError("没有可用于匹配的原片事件")

    event_by_id = {int(event.get("id", 0)): event for event in events}
    matches: list[dict[str, Any]] = []
    used_counts: dict[int, int] = {}
    last_selected_start = -1.0
    for narration in story.get("narration", []):
        if not isinstance(narration, dict):
            continue
        bound_ids = {
            int(value)
            for value in narration.get("event_ids", [])
            if str(value).isdigit() and int(value) in event_by_id
        }
        query = " ".join(
            (
                str(narration.get("visual_query", "")),
                str(narration.get("text_en", "")),
            )
        ).strip()
        candidates = [
            _score_candidate(
                event,
                query,
                int(event.get("id", 0)) in bound_ids,
                used_counts.get(int(event.get("id", 0)), 0),
                last_selected_start,
            )
            for event in events
        ]
        candidates.sort(key=lambda item: (-float(item["score"]), float(item["start"])))
        candidates = candidates[: min(5, len(candidates))]
        selected = candidates[0]
        selected_clips = _fit_clips(candidates, int(selected["event_id"]), float(narration.get("estimated_duration_sec", 0) or 0))
        last_selected_start = float(selected["start"])
        for clip in selected_clips:
            event_id = int(clip.get("event_id", 0))
            used_counts[event_id] = used_counts.get(event_id, 0) + 1
        matches.append(
            {
                "narration_id": int(narration.get("id", len(matches) + 1)),
                "text_en": str(narration.get("text_en", "")),
                "visual_query": str(narration.get("visual_query", "")),
                "narration_duration_sec": float(narration.get("estimated_duration_sec", 0) or 0),
                "selected_event_id": int(selected["event_id"]),
                "selected_start": float(selected["start"]),
                "selected_end": float(selected_clips[0]["end"]),
                "selected_clips": selected_clips,
                "coverage_sec": round(sum(float(clip["end"]) - float(clip["start"]) for clip in selected_clips), 3),
                "candidates": candidates,
            }
        )

    payload = {
        "schema_version": 1,
        "strategy": "automatic story binding + visual similarity + chronology + reuse control",
        "items": matches,
    }
    matches_json.parent.mkdir(parents=True, exist_ok=True)
    matches_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def select_shot_match(matches_json: Path, narration_id: int, event_id: int) -> dict[str, Any]:
    payload = json.loads(matches_json.read_text(encoding="utf-8"))
    for item in payload.get("items", []):
        if int(item.get("narration_id", 0)) != narration_id:
            continue
        selected = next(
            (
                candidate
                for candidate in item.get("candidates", [])
                if int(candidate.get("event_id", 0)) == event_id
            ),
            None,
        )
        if selected is None:
            raise ValueError("所选镜头不在候选列表中")
        item["selected_event_id"] = event_id
        item["selected_start"] = float(selected.get("start", 0))
        clips = _fit_clips(
            list(item.get("candidates", [])),
            event_id,
            float(item.get("narration_duration_sec", 0) or 0),
        )
        item["selected_end"] = float(clips[0]["end"])
        item["selected_clips"] = clips
        item["coverage_sec"] = round(sum(float(clip["end"]) - float(clip["start"]) for clip in clips), 3)
        break
    else:
        raise ValueError("找不到对应的解说句")
    matches_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def adjust_shot_boundary(
    matches_json: Path,
    narration_id: int,
    boundary: str,
    delta_sec: float,
) -> dict[str, Any]:
    payload = json.loads(matches_json.read_text(encoding="utf-8"))
    for item in payload.get("items", []):
        if int(item.get("narration_id", 0)) != narration_id:
            continue
        event_id = int(item.get("selected_event_id", 0))
        candidate = next(
            (value for value in item.get("candidates", []) if int(value.get("event_id", 0)) == event_id),
            None,
        )
        if candidate is None:
            raise ValueError("当前镜头不在候选列表中")
        clips = list(item.get("selected_clips", []))
        if not clips:
            clips = _fit_clips(list(item.get("candidates", [])), event_id, float(item.get("narration_duration_sec", 0) or 0))
        primary = dict(clips[0])
        event_start = float(candidate.get("start", 0))
        event_end = float(candidate.get("end", 0))
        if boundary == "start":
            primary["start"] = round(min(max(float(primary["start"]) + delta_sec, event_start), float(primary["end"]) - 0.2), 3)
        elif boundary == "end":
            primary["end"] = round(max(min(float(primary["end"]) + delta_sec, event_end), float(primary["start"]) + 0.2), 3)
        else:
            raise ValueError("未知的镜头边界")
        clips[0] = primary
        item["selected_start"] = float(primary["start"])
        item["selected_end"] = float(primary["end"])
        item["selected_clips"] = clips
        item["coverage_sec"] = round(sum(float(clip["end"]) - float(clip["start"]) for clip in clips), 3)
        break
    else:
        raise ValueError("找不到对应的解说句")
    matches_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def build_rough_cut(matches_json: Path, rough_cut_json: Path) -> dict[str, Any]:
    matches = json.loads(matches_json.read_text(encoding="utf-8"))
    output_cursor = 0.0
    clips: list[dict[str, Any]] = []
    narration_items: list[dict[str, Any]] = []
    all_covered = True
    for item in matches.get("items", []):
        narration_start = output_cursor
        source_clips = list(item.get("selected_clips", []))
        if not source_clips:
            source_clips = [
                {
                    "event_id": int(item.get("selected_event_id", 0)),
                    "start": float(item.get("selected_start", 0)),
                    "end": float(item.get("selected_end", 0)),
                }
            ]
        for source in source_clips:
            duration = max(0.0, float(source.get("end", 0)) - float(source.get("start", 0)))
            clips.append(
                {
                    "id": len(clips) + 1,
                    "narration_id": int(item.get("narration_id", 0)),
                    "event_id": int(source.get("event_id", 0)),
                    "source_start": round(float(source.get("start", 0)), 3),
                    "source_end": round(float(source.get("end", 0)), 3),
                    "output_start": round(output_cursor, 3),
                    "output_end": round(output_cursor + duration, 3),
                }
            )
            output_cursor += duration
        required = float(item.get("narration_duration_sec", 0) or 0)
        coverage = output_cursor - narration_start
        covered = coverage + 0.05 >= required
        all_covered = all_covered and covered
        narration_items.append(
            {
                "narration_id": int(item.get("narration_id", 0)),
                "text_en": str(item.get("text_en", "")),
                "output_start": round(narration_start, 3),
                "output_end": round(output_cursor, 3),
                "required_duration_sec": round(required, 3),
                "coverage_sec": round(coverage, 3),
                "covered": covered,
            }
        )
    payload = {
        "schema_version": 1,
        "duration_sec": round(output_cursor, 3),
        "all_narration_covered": all_covered,
        "narration": narration_items,
        "clips": clips,
    }
    rough_cut_json.parent.mkdir(parents=True, exist_ok=True)
    rough_cut_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def apply_voice_timing(
    matches_json: Path,
    audio_duration_sec: float,
    synced_segments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = json.loads(matches_json.read_text(encoding="utf-8"))
    items = [item for item in payload.get("items", []) if isinstance(item, dict)]
    if not items:
        raise ValueError("没有可校准的镜头匹配")
    total = max(0.1, float(audio_duration_sec))
    segments = list(synced_segments or [])
    if len(segments) >= len(items):
        groups = _group_synced_segments(items, segments)
        starts = [max(0.0, float(group[0].get("start", 0))) for group in groups]
        boundaries = [0.0]
        boundaries.extend(starts[1:])
        boundaries.append(total)
        durations = [max(0.2, boundaries[index + 1] - boundaries[index]) for index in range(len(items))]
    else:
        estimates = [max(0.2, float(item.get("narration_duration_sec", 0) or 0)) for item in items]
        estimate_total = sum(estimates)
        durations = [total * value / estimate_total for value in estimates]

    voice_cursor = 0.0
    for item, duration in zip(items, durations):
        duration = round(duration, 3)
        item["estimated_narration_duration_sec"] = float(item.get("narration_duration_sec", 0) or 0)
        item["narration_duration_sec"] = duration
        item["voice_start"] = round(voice_cursor, 3)
        voice_cursor += duration
        item["voice_end"] = round(voice_cursor, 3)
        clips = _fit_clips(
            list(item.get("candidates", [])),
            int(item.get("selected_event_id", 0)),
            duration,
        )
        item["selected_clips"] = clips
        item["selected_start"] = float(clips[0]["start"])
        item["selected_end"] = float(clips[0]["end"])
        item["coverage_sec"] = round(sum(float(clip["end"]) - float(clip["start"]) for clip in clips), 3)
    payload["voice_timing_applied"] = True
    payload["voice_duration_sec"] = round(total, 3)
    matches_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _group_synced_segments(
    items: list[dict[str, Any]],
    segments: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Partition sequential TTS subtitles so punctuation splits map back to story lines."""
    groups: list[list[dict[str, Any]]] = []
    position = 0
    for item_index, item in enumerate(items):
        remaining_items = len(items) - item_index - 1
        if item_index == len(items) - 1:
            groups.append(segments[position:])
            break
        max_end = len(segments) - remaining_items
        target = _normalized_words(str(item.get("text_en", "")))
        best_end = position + 1
        best_score = -1.0
        for end in range(position + 1, max_end + 1):
            candidate = _normalized_words(" ".join(str(segment.get("text", "")) for segment in segments[position:end]))
            score = SequenceMatcher(None, target, candidate).ratio()
            if score > best_score:
                best_score = score
                best_end = end
        groups.append(segments[position:best_end])
        position = best_end
    return groups


def _normalized_words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.casefold()))


def _score_candidate(
    event: dict[str, Any],
    query: str,
    directly_bound: bool,
    used_count: int = 0,
    last_selected_start: float = -1.0,
) -> dict[str, Any]:
    event_text = " ".join(
        (
            str(event.get("visual_description", "")),
            str(event.get("transcript", "")),
            json.dumps(event.get("screen_text", []), ensure_ascii=False),
            json.dumps(event.get("technical_visual", {}), ensure_ascii=False),
        )
    ).strip()
    similarity = _text_similarity(query, event_text)
    score = (0.78 if directly_bound else 0.12) + similarity * (0.21 if directly_bound else 0.68)
    start = float(event.get("start", 0) or 0)
    if last_selected_start >= 0:
        score += 0.06 if start >= last_selected_start else -0.08
    score -= min(0.32, used_count * 0.14)
    score = min(0.99, max(0.01, score))
    reason = "故事稿已绑定此事件" if directly_bound else ("画面描述较相关" if similarity >= 0.18 else "备用原片场景")
    if used_count:
        reason += " · 已降低重复使用"
    return {
        "event_id": int(event.get("id", 0)),
        "start": round(start, 3),
        "end": round(float(event.get("end", 0) or 0), 3),
        "duration_sec": round(
            max(0.0, float(event.get("end", 0) or 0) - float(event.get("start", 0) or 0)),
            3,
        ),
        "keyframe": str(event.get("keyframe", "")),
        "score": round(score, 3),
        "reason": reason,
    }


def _fit_clips(candidates: list[dict[str, Any]], selected_event_id: int, target_sec: float) -> list[dict[str, Any]]:
    selected = next(candidate for candidate in candidates if int(candidate.get("event_id", 0)) == selected_event_id)
    ordered = [selected]
    remaining = sorted(
        (candidate for candidate in candidates if int(candidate.get("event_id", 0)) != selected_event_id),
        key=lambda candidate: (
            0 if float(candidate.get("start", 0)) >= float(selected.get("end", 0)) else 1,
            abs(float(candidate.get("start", 0)) - float(selected.get("end", 0))),
        ),
    )
    ordered.extend(remaining)
    clips: list[dict[str, Any]] = []
    needed = max(0.2, target_sec)
    for candidate in ordered:
        start = float(candidate.get("start", 0))
        available = max(0.0, float(candidate.get("end", 0)) - start)
        take = min(available, needed)
        if take <= 0:
            continue
        clips.append(
            {
                "event_id": int(candidate.get("event_id", 0)),
                "start": round(start, 3),
                "end": round(start + take, 3),
            }
        )
        needed -= take
        if needed <= 0.05:
            break
    return clips


def _text_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
    sequence = SequenceMatcher(None, left.casefold(), right.casefold()).ratio()
    return min(1.0, jaccard * 0.65 + sequence * 0.35)


def _tokens(text: str) -> set[str]:
    lowered = text.casefold()
    words = set(re.findall(r"[a-z0-9']+", lowered))
    cjk = "".join(re.findall(r"[\u3400-\u9fff]", lowered))
    grams = {cjk[index : index + 2] for index in range(max(0, len(cjk) - 1))}
    grams.update(cjk if len(cjk) == 1 else ())
    return words | grams
