from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

from .story_service import _chat_json
from .vision_service import api_configuration


def should_evaluate_story_series(
    events_payload: dict[str, Any],
    config: dict[str, Any],
    story: dict[str, Any] | None = None,
    layered: dict[str, Any] | None = None,
    planning_words_per_second: float | None = None,
) -> bool:
    return bool(
        story_series_evaluation_reasons(
            events_payload,
            config,
            story=story,
            layered=layered,
            planning_words_per_second=planning_words_per_second,
        )
    )


def story_series_evaluation_reasons(
    events_payload: dict[str, Any],
    config: dict[str, Any],
    story: dict[str, Any] | None = None,
    layered: dict[str, Any] | None = None,
    planning_words_per_second: float | None = None,
) -> list[dict[str, Any]]:
    """Return local signals that justify one AI preservation evaluation.

    These signals never split a project by themselves. They only decide whether
    the finished one-part draft deserves a semantic preservation check.
    """
    settings = config.get("series", {})
    if not bool(settings.get("auto_split", True)):
        return []
    events = [item for item in events_payload.get("events", []) if isinstance(item, dict)]
    if not events:
        return []
    duration = max(
        float(events_payload.get("duration_sec", 0) or 0),
        max((float(item.get("end", 0) or 0) for item in events), default=0.0),
    )
    duration_threshold = max(
        180.0,
        float(settings.get("candidate_min_source_duration_sec", 300) or 300),
    )
    event_threshold = max(
        30,
        int(settings.get("candidate_min_event_count", 80) or 80),
    )
    reasons: list[dict[str, Any]] = []
    if duration >= duration_threshold:
        reasons.append(
            _reason("source_duration", "原片时长较长", duration_sec=round(duration, 1))
        )
    if len(events) >= event_threshold:
        reasons.append(
            _reason("event_count", "原片事件数量较多", event_count=len(events))
        )

    transcript_units = _estimated_unique_transcript_units(events)
    story_settings = config.get("story", {})
    wps = max(
        0.9,
        min(
            2.2,
            float(
                planning_words_per_second
                or story_settings.get("planning_words_per_second", 1.45)
                or 1.45
            ),
        ),
    )
    source_word_ratio = max(
        0.35,
        min(
            1.2,
            float(settings.get("chinese_to_english_word_ratio", 0.62) or 0.62),
        ),
    )
    estimated_source_words = transcript_units * source_word_ratio
    short_capacity_words = max(
        30.0,
        float(settings.get("target_part_duration_sec", 174) or 174) * wps,
    )
    compression_ratio = estimated_source_words / short_capacity_words
    minimum_transcript_units = max(
        180,
        int(settings.get("candidate_min_transcript_units", 500) or 500),
    )
    maximum_compression = max(
        1.5,
        float(settings.get("candidate_max_compression_ratio", 2.8) or 2.8),
    )
    if (
        transcript_units >= minimum_transcript_units
        and compression_ratio >= maximum_compression
    ):
        reasons.append(
            _reason(
                "transcript_compression",
                "原始解说信息量相对三分钟容量过高",
                transcript_units=transcript_units,
                estimated_english_words=round(estimated_source_words),
                compression_ratio=round(compression_ratio, 2),
            )
        )

    speech_duration = sum(
        max(0.0, float(item.get("speech_duration", 0) or 0)) for item in events
    )
    speech_coverage = speech_duration / duration if duration > 0 else 0.0
    source_minutes = max(duration / 60.0, 0.01)
    english_words_per_minute = estimated_source_words / source_minutes
    min_dense_duration = max(
        45.0,
        float(settings.get("candidate_min_dense_duration_sec", 60) or 60),
    )
    min_speech_coverage = max(
        0.3,
        min(
            0.95,
            float(settings.get("candidate_min_speech_coverage", 0.65) or 0.65),
        ),
    )
    min_dense_words_per_minute = max(
        100.0,
        float(settings.get("candidate_min_dense_words_per_minute", 200) or 200),
    )
    if (
        duration >= min_dense_duration
        and transcript_units >= minimum_transcript_units
        and speech_coverage >= min_speech_coverage
        and english_words_per_minute >= min_dense_words_per_minute
    ):
        reasons.append(
            _reason(
                "dense_speech",
                "原片持续高密度讲解，留给压缩的冗余较少",
                speech_coverage=round(speech_coverage, 3),
                estimated_english_words_per_minute=round(english_words_per_minute),
            )
        )

    substantive_events = [item for item in events if _is_substantive_event(item)]
    substantive_threshold = max(
        20,
        int(settings.get("candidate_min_substantive_event_count", 42) or 42),
    )
    if len(substantive_events) >= substantive_threshold:
        reasons.append(
            _reason(
                "substantive_events",
                "独立有效信息点较多",
                substantive_event_count=len(substantive_events),
            )
        )

    technical_events = [item for item in events if _is_technical_event(item)]
    technical_threshold = max(
        4,
        int(settings.get("candidate_min_technical_event_count", 8) or 8),
    )
    if len(technical_events) >= technical_threshold:
        reasons.append(
            _reason(
                "technical_evidence",
                "图表、公式、参数或技术画面较多",
                technical_event_count=len(technical_events),
            )
        )

    layered = layered if isinstance(layered, dict) else {}
    critical_ids = {
        int(value)
        for key in (
            "global_turning_point_event_ids",
            "recommended_highlight_event_ids",
        )
        for value in layered.get(key, [])
        if str(value).isdigit()
    }
    key_fact_count = sum(
        len(item.get("key_facts", []))
        for item in layered.get("chapters", [])
        if isinstance(item, dict) and isinstance(item.get("key_facts", []), list)
    )
    critical_threshold = max(
        6,
        int(settings.get("candidate_min_layered_critical_count", 10) or 10),
    )
    key_fact_threshold = max(
        10,
        int(settings.get("candidate_min_layered_key_fact_count", 18) or 18),
    )
    if len(critical_ids) >= critical_threshold or key_fact_count >= key_fact_threshold:
        reasons.append(
            _reason(
                "layered_complexity",
                "全片包含较多关键事实、机制或转折",
                critical_event_count=len(critical_ids),
                layered_key_fact_count=key_fact_count,
            )
        )

    if isinstance(story, dict) and story.get("narration"):
        story_duration = float(story.get("estimated_duration_sec", 0) or 0)
        near_limit_ratio = max(
            0.75,
            min(
                0.98,
                float(settings.get("candidate_story_near_limit_ratio", 0.9) or 0.9),
            ),
        )
        selected_ids = _story_event_ids(story)
        substantive_ids = {
            int(item.get("id", 0) or 0) for item in substantive_events
        }
        missing_critical = critical_ids - selected_ids
        substantive_coverage = (
            len(selected_ids & substantive_ids) / len(substantive_ids)
            if substantive_ids
            else 1.0
        )
        max_coverage = max(
            0.15,
            min(
                0.8,
                float(settings.get("candidate_max_draft_event_coverage", 0.4) or 0.4),
            ),
        )
        near_limit = story_duration >= (
            float(settings.get("target_part_duration_sec", 174) or 174)
            * near_limit_ratio
        )
        if missing_critical:
            reasons.append(
                _reason(
                    "draft_missing_critical",
                    "单集草稿仍遗漏分层分析中的关键内容",
                    missing_critical_event_count=len(missing_critical),
                )
            )
        if (
            near_limit
            and len(substantive_events) >= 20
            and substantive_coverage <= max_coverage
        ):
            reasons.append(
                _reason(
                    "draft_capacity_pressure",
                    "单集草稿已接近时长上限但只覆盖少量有效信息点",
                    story_duration_sec=round(story_duration, 1),
                    substantive_event_coverage=round(substantive_coverage, 3),
                )
            )
    return reasons


def _reason(code: str, label_zh: str, **metrics: Any) -> dict[str, Any]:
    return {"code": code, "label_zh": label_zh, "metrics": metrics}


def _estimated_unique_transcript_units(events: list[dict[str, Any]]) -> int:
    indexed: dict[str, float] = {}
    unindexed_units = 0
    for event in events:
        text_units = len("".join(str(event.get("transcript", "")).split()))
        if text_units <= 0:
            continue
        indices = [str(value) for value in event.get("transcript_indices", [])]
        if not indices:
            unindexed_units += text_units
            continue
        share = text_units / len(indices)
        for index in indices:
            indexed[index] = max(indexed.get(index, 0.0), share)
    return round(sum(indexed.values()) + unindexed_units)


def _is_technical_event(event: dict[str, Any]) -> bool:
    technical = event.get("technical_visual", {})
    technical = technical if isinstance(technical, dict) else {}
    return bool(
        str(technical.get("type", "none")) not in {"", "none"}
        or float(technical.get("importance", 0) or 0) >= 1
        or technical.get("facts")
        or event.get("screen_text")
    )


def _is_substantive_event(event: dict[str, Any]) -> bool:
    transcript_length = len("".join(str(event.get("transcript", "")).split()))
    visual_length = len("".join(str(event.get("visual_description", "")).split()))
    return bool(
        transcript_length >= 18
        or visual_length >= 22
        or str(event.get("story_value", "")).strip()
        or _is_technical_event(event)
    )


def _story_event_ids(story: dict[str, Any]) -> set[int]:
    return {
        int(value)
        for item in story.get("narration", [])
        if isinstance(item, dict)
        for value in item.get("event_ids", [])
        if str(value).isdigit()
    }


def evaluate_story_preservation(
    events_json: Path,
    story_json: Path,
    layered_structure_json: Path | None,
    config: dict[str, Any],
    app_root: Path,
    trigger_reasons: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Decide whether one Short preserves the source or needs a multi-part series."""
    from openai import OpenAI

    events_payload = json.loads(events_json.read_text(encoding="utf-8"))
    events = [dict(item) for item in events_payload.get("events", []) if isinstance(item, dict)]
    story = json.loads(story_json.read_text(encoding="utf-8"))
    layered: dict[str, Any] = {}
    if layered_structure_json and layered_structure_json.exists():
        try:
            value = json.loads(layered_structure_json.read_text(encoding="utf-8"))
            layered = value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError):
            layered = {}
    if not events or not story.get("narration"):
        raise ValueError("缺少可用于自动拆分判断的原片事件或故事稿")

    settings = config.get("series", {})
    story_settings = config.get("story", {})
    api = api_configuration(config, app_root, "story")
    api_key = str(api.get("api_key", "")).strip()
    if not api_key:
        raise RuntimeError("未配置 OPENAI_API_KEY，无法判断故事是否需要拆分")
    base_url = str(api.get("base_url", "")).strip() or None
    model = (
        str(settings.get("evaluation_model", "")).strip()
        or str(story_settings.get("editor_model", "")).strip()
        or str(story_settings.get("model", "gpt-4o-mini")).strip()
    )
    max_parts = max(2, min(12, int(settings.get("max_parts", 6) or 6)))
    target_duration = max(
        120,
        min(174, int(settings.get("target_part_duration_sec", 174) or 174)),
    )
    max_events = max(
        80,
        min(600, int(settings.get("max_evaluation_events", 320) or 320)),
    )
    selected_events = _select_evaluation_events(events, layered, max_events)
    client = OpenAI(api_key=api_key, base_url=base_url)
    raw = _chat_json(
        client,
        model,
        _evaluation_prompt(
            selected_events,
            story,
            layered,
            target_duration,
            max_parts,
            trigger_reasons or [],
        ),
        0.15,
        base_url,
        "故事保真与自动拆分判断",
    )
    required_event_ids = {
        int(value)
        for key in (
            "global_turning_point_event_ids",
            "recommended_highlight_event_ids",
        )
        for value in layered.get(key, [])
        if str(value).isdigit()
    }
    normalized = _normalize_evaluation(
        raw, events, max_parts, required_event_ids=required_event_ids
    )
    normalized.update(
        {
            "schema_version": 1,
            "model": model,
            "source_event_count": len(events),
            "evaluated_event_count": len(selected_events),
            "single_story_word_count": int(story.get("word_count", 0) or 0),
            "single_story_duration_sec": float(
                story.get("estimated_duration_sec", 0) or 0
            ),
            "trigger_reasons": trigger_reasons or [],
        }
    )
    return normalized


def build_part_events_payload(
    events_payload: dict[str, Any], event_ids: list[int]
) -> dict[str, Any]:
    """Create a focused evidence timeline while retaining immediate visual context."""
    events = [dict(item) for item in events_payload.get("events", []) if isinstance(item, dict)]
    ordered_ids = [int(item.get("id", 0) or 0) for item in events]
    requested = {int(value) for value in event_ids if int(value) in set(ordered_ids)}
    expanded = set(requested)
    for index, event_id in enumerate(ordered_ids):
        if event_id not in requested:
            continue
        if index > 0:
            expanded.add(ordered_ids[index - 1])
        if index + 1 < len(ordered_ids):
            expanded.add(ordered_ids[index + 1])
    focused = [item for item in events if int(item.get("id", 0) or 0) in expanded]
    payload = dict(events_payload)
    payload["events"] = focused
    payload["event_count"] = len(focused)
    payload["series_selected_event_ids"] = sorted(requested)
    return payload


def filter_layered_structure(
    layered: dict[str, Any], event_ids: list[int], part_plan: dict[str, Any]
) -> dict[str, Any]:
    allowed = {int(value) for value in event_ids}
    result = dict(layered)
    for key in (
        "global_turning_point_event_ids",
        "recommended_highlight_event_ids",
        "routine_or_repetitive_event_ids",
    ):
        result[key] = [
            int(value)
            for value in layered.get(key, [])
            if str(value).isdigit() and int(value) in allowed
        ]
    chapters = []
    for raw in layered.get("chapters", []):
        if not isinstance(raw, dict):
            continue
        chapter = dict(raw)
        ids = [
            int(value)
            for value in raw.get("event_ids", [])
            if str(value).isdigit() and int(value) in allowed
        ]
        if ids:
            chapter["event_ids"] = ids
            chapters.append(chapter)
    result["chapters"] = chapters
    result["series_part_plan"] = dict(part_plan)
    return result


def new_series_id() -> str:
    return uuid4().hex


def series_source_events_file(project_file: Path) -> Path:
    full = project_file.parent / "analysis" / "events_full.json"
    return full if full.exists() else project_file.parent / "analysis" / "events.json"


def series_source_layered_file(project_file: Path) -> Path | None:
    full = project_file.parent / "analysis" / "layered_structure_full.json"
    normal = project_file.parent / "analysis" / "layered_structure.json"
    if full.exists():
        return full
    return normal if normal.exists() else None


def materialize_story_series(
    root_project_file: Path,
    evaluation: dict[str, Any],
    part_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Turn staged episode results into ordinary StoryCut projects.

    Every episode therefore keeps the existing matching, TTS, subtitle and export
    pipeline. The original full analysis remains in the first project for future
    regeneration.
    """
    if len(part_results) < 2:
        raise ValueError("自动拆分至少需要两个完整分集结果")
    root_dir = root_project_file.parent.resolve()
    projects_dir = root_dir.parent
    root_payload = json.loads(root_project_file.read_text(encoding="utf-8"))
    existing_series = root_payload.get("series", {})
    existing_series = existing_series if isinstance(existing_series, dict) else {}
    series_id = str(existing_series.get("id", "")).strip() or new_series_id()
    previous_members = {
        int(item.get("part_index", 0) or 0): str(item.get("directory", ""))
        for item in existing_series.get("members", [])
        if isinstance(item, dict)
    }

    full_events_file = series_source_events_file(root_project_file)
    full_events = json.loads(full_events_file.read_text(encoding="utf-8"))
    full_layered_file = series_source_layered_file(root_project_file)
    full_layered: dict[str, Any] = {}
    if full_layered_file and full_layered_file.exists():
        value = json.loads(full_layered_file.read_text(encoding="utf-8"))
        full_layered = value if isinstance(value, dict) else {}
    analysis_dir = root_dir / "analysis"
    if full_events_file.name != "events_full.json":
        shutil.copy2(full_events_file, analysis_dir / "events_full.json")
    if full_layered_file and full_layered_file.name != "layered_structure_full.json":
        shutil.copy2(full_layered_file, analysis_dir / "layered_structure_full.json")

    member_dirs: list[Path] = [root_dir]
    for index in range(2, len(part_results) + 1):
        previous = previous_members.get(index, "")
        candidate = projects_dir / previous if previous else projects_dir / f"{root_dir.name}-part-{index:02d}"
        if candidate.exists():
            try:
                candidate_payload = json.loads((candidate / "project.json").read_text(encoding="utf-8"))
                candidate_series = candidate_payload.get("series", {})
                if str(candidate_series.get("id", "")) != series_id:
                    candidate = _available_part_directory(projects_dir, root_dir.name, index)
            except (OSError, ValueError, TypeError):
                candidate = _available_part_directory(projects_dir, root_dir.name, index)
        member_dirs.append(candidate)

    members = [
        {
            "part_index": index,
            "directory": directory.name,
            "title_zh": str(part_results[index - 1]["plan"].get("title_zh", "")),
        }
        for index, directory in enumerate(member_dirs, start=1)
    ]
    now = __import__("datetime").datetime.now().isoformat(timespec="seconds")
    archive_stamp = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
    for index, (directory, result) in enumerate(zip(member_dirs, part_results), start=1):
        directory.mkdir(parents=True, exist_ok=True)
        for child in ("analysis", "script", "timeline", "audio", "cache"):
            (directory / child).mkdir(exist_ok=True)
        _archive_previous_part_outputs(directory, archive_stamp)
        part_plan = dict(result["plan"])
        part_events = build_part_events_payload(full_events, part_plan.get("event_ids", []))
        _make_keyframes_portable_from_root(part_events, root_dir)
        (directory / "analysis" / "events.json").write_text(
            json.dumps(part_events, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        scoped_layered = filter_layered_structure(
            full_layered,
            [int(item.get("id", 0) or 0) for item in part_events.get("events", [])],
            part_plan,
        )
        if scoped_layered:
            (directory / "analysis" / "layered_structure.json").write_text(
                json.dumps(scoped_layered, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        story = dict(result["story"])
        story["series_part_index"] = index
        story["series_part_count"] = len(part_results)
        story["series_title_zh"] = str(part_plan.get("title_zh", ""))
        (directory / "script" / "story.json").write_text(
            json.dumps(story, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        plan_source = result.get("story_plan_file")
        if plan_source and Path(plan_source).exists():
            shutil.copy2(Path(plan_source), directory / "script" / "story_plan.json")
        if index > 1:
            cover_source = root_dir / "cache" / "cover.jpg"
            if cover_source.exists():
                shutil.copy2(cover_source, directory / "cache" / "cover.jpg")
        project_payload = dict(root_payload)
        project_payload["name"] = (
            str(root_payload.get("name", root_dir.name))
            if index == 1
            else f"{root_payload.get('name', root_dir.name)} · 第 {index} 集"
        )
        project_payload["stage"] = "scripted"
        project_payload["updated_at"] = now
        project_payload["series"] = {
            "id": series_id,
            "root_directory": root_dir.name,
            "part_index": index,
            "part_count": len(part_results),
            "plan": part_plan,
            "members": members,
            "coverage_score": evaluation.get("coverage_score", 0),
            "reason_zh": evaluation.get("reason_zh", ""),
        }
        artifacts = dict(project_payload.get("artifacts", {}))
        artifacts["events"] = "analysis/events.json"
        artifacts["layered_structure"] = "analysis/layered_structure.json"
        artifacts["story"] = "script/story.json"
        if (directory / "script" / "story_plan.json").exists():
            artifacts["story_plan"] = "script/story_plan.json"
        for key in (
            "matches", "rough_cut", "rough_preview", "fact_review",
            "terminology_review", "content_review", "duration_revision_proposal",
            "narration_audio", "narration_srt", "narration_srt_original",
        ):
            artifacts.pop(key, None)
        if index == 1:
            artifacts["events_full"] = "analysis/events_full.json"
            if (analysis_dir / "layered_structure_full.json").exists():
                artifacts["layered_structure_full"] = "analysis/layered_structure_full.json"
        project_payload["artifacts"] = artifacts
        (directory / "project.json").write_text(
            json.dumps(project_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    evaluation_file = root_dir / "script" / "series_evaluation.json"
    evaluation_file.write_text(
        json.dumps(evaluation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "series_id": series_id,
        "part_count": len(part_results),
        "members": members,
        "current_story": part_results[0]["story"],
    }


def collapse_story_series(root_project_file: Path) -> int:
    """Return an existing series to one project and archive generated siblings."""
    payload = json.loads(root_project_file.read_text(encoding="utf-8"))
    series = payload.get("series", {})
    series = series if isinstance(series, dict) else {}
    if int(series.get("part_index", 0) or 0) != 1:
        return 0
    series_id = str(series.get("id", ""))
    root_dir = root_project_file.parent.resolve()
    projects_dir = root_dir.parent
    timestamp = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
    archive_dir = root_dir / "archive" / f"series-members-{timestamp}"
    archived = 0
    for member in series.get("members", []):
        if not isinstance(member, dict) or int(member.get("part_index", 0) or 0) <= 1:
            continue
        candidate = (projects_dir / str(member.get("directory", ""))).resolve()
        candidate_file = candidate / "project.json"
        if candidate.parent != projects_dir or not candidate_file.exists():
            continue
        try:
            candidate_payload = json.loads(candidate_file.read_text(encoding="utf-8"))
            if str(candidate_payload.get("series", {}).get("id", "")) != series_id:
                continue
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        archive_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(candidate), str(archive_dir / candidate.name))
        archived += 1
    analysis = root_dir / "analysis"
    full_events = analysis / "events_full.json"
    full_layered = analysis / "layered_structure_full.json"
    if full_events.exists():
        shutil.copy2(full_events, analysis / "events.json")
    if full_layered.exists():
        shutil.copy2(full_layered, analysis / "layered_structure.json")
    payload.pop("series", None)
    payload["updated_at"] = __import__("datetime").datetime.now().isoformat(
        timespec="seconds"
    )
    artifacts = payload.setdefault("artifacts", {})
    artifacts["events"] = "analysis/events.json"
    if (analysis / "layered_structure.json").exists():
        artifacts["layered_structure"] = "analysis/layered_structure.json"
    root_project_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return archived


def _available_part_directory(projects_dir: Path, root_name: str, index: int) -> Path:
    base = f"{root_name}-part-{index:02d}"
    candidate = projects_dir / base
    suffix = 1
    while candidate.exists():
        candidate = projects_dir / f"{base}-{suffix}"
        suffix += 1
    return candidate


def _make_keyframes_portable_from_root(
    events_payload: dict[str, Any], root_project_dir: Path
) -> None:
    for event in events_payload.get("events", []):
        if not isinstance(event, dict):
            continue
        value = str(event.get("keyframe", "")).strip()
        if value and not Path(value).is_absolute():
            event["keyframe"] = str((root_project_dir / "analysis" / value).resolve())


def _archive_previous_part_outputs(project_dir: Path, timestamp: str) -> None:
    archive = project_dir / "archive" / f"before-series-{timestamp}"
    candidates = [
        project_dir / "timeline",
        project_dir / "audio",
        project_dir / "script" / "tts",
        project_dir / "script" / "story.json",
        project_dir / "script" / "story_plan.json",
        project_dir / "script" / "content_review.json",
        project_dir / "script" / "fact_review.json",
        project_dir / "script" / "terminology_review.json",
        project_dir / "script" / "duration_revision_proposal.json",
    ]
    existing = [
        item
        for item in candidates
        if item.exists() and (item.is_file() or any(item.iterdir()))
    ]
    if not existing:
        return
    archive.mkdir(parents=True, exist_ok=True)
    for item in existing:
        relative = item.relative_to(project_dir)
        target = archive / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        shutil.move(str(item), str(target))
    (project_dir / "timeline").mkdir(exist_ok=True)
    (project_dir / "audio").mkdir(exist_ok=True)


def _select_evaluation_events(
    events: list[dict[str, Any]],
    layered: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    if len(events) <= limit:
        selected = events
    else:
        critical = {
            int(value)
            for key in (
                "global_turning_point_event_ids",
                "recommended_highlight_event_ids",
            )
            for value in layered.get(key, [])
            if str(value).isdigit()
        }
        by_id = {int(item.get("id", 0) or 0): item for item in events}
        chosen_ids = set(critical)
        remaining = max(1, limit - len(chosen_ids))
        step = max(1.0, len(events) / remaining)
        for index in range(remaining):
            chosen_ids.add(int(events[min(len(events) - 1, int(index * step))].get("id", 0) or 0))
        selected = [item for item in events if int(item.get("id", 0) or 0) in chosen_ids]
        selected = selected[:limit]
    return [
        {
            "id": int(item.get("id", 0) or 0),
            "start": round(float(item.get("start", 0) or 0), 2),
            "end": round(float(item.get("end", 0) or 0), 2),
            "transcript": str(item.get("transcript", "")).strip()[:260],
            "visual": str(item.get("visual_description", "")).strip()[:220],
            "story_value": str(item.get("story_value", "")).strip()[:160],
        }
        for item in selected
    ]


def _evaluation_prompt(
    events: list[dict[str, Any]],
    story: dict[str, Any],
    layered: dict[str, Any],
    target_duration: int,
    max_parts: int,
    trigger_reasons: list[dict[str, Any]],
) -> str:
    layered_context = {
        "whole_video_summary_zh": layered.get("whole_video_summary_zh", ""),
        "central_thread_zh": layered.get("central_thread_zh", ""),
        "global_progression_zh": layered.get("global_progression_zh", ""),
        "chapters": layered.get("chapters", []),
        "global_turning_point_event_ids": layered.get(
            "global_turning_point_event_ids", []
        ),
        "recommended_highlight_event_ids": layered.get(
            "recommended_highlight_event_ids", []
        ),
        "routine_or_repetitive_event_ids": layered.get(
            "routine_or_repetitive_event_ids", []
        ),
    }
    final_story = {
        "title": story.get("title", ""),
        "angle": story.get("angle", ""),
        "word_count": story.get("word_count", 0),
        "estimated_duration_sec": story.get("estimated_duration_sec", 0),
        "outline": story.get("outline", []),
        "narration": [
            {
                "id": item.get("id"),
                "event_ids": item.get("event_ids", []),
                "text_en": item.get("text_en", ""),
            }
            for item in story.get("narration", [])
            if isinstance(item, dict)
        ],
    }
    return f"""
You are the senior retention editor deciding whether one YouTube Short truthfully preserves a source video.

The existing English story already fits below three minutes. Do NOT recommend a series merely because the source is long or contains many events. One concise Short is preferred whenever it preserves the central causal chain, major mechanisms, indispensable contrasts, strongest evidence, key turning points, and complete conclusion.

Recommend multiple parts only when a single story of about {target_duration} seconds must omit or distort essential supported material. Routine repetition, secondary examples, decorative visuals, repeated demonstrations, and details that do not change understanding are not reasons to split.

If splitting is necessary:
- Use the smallest number of parts, from 2 to {max_parts}.
- Each part must support a coherent 2-3 minute standalone story with its own question, development, payoff, and ending.
- Preserve chronological and causal order where required.
- Do not split one mechanism or cause-and-effect explanation across parts.
- Assign every truly essential event to at least one part. Avoid duplicate event IDs unless brief context is necessary.
- Parts are editorial arcs, not equal time slices.

Return exactly one JSON object and no Markdown:
{{
  "single_part_acceptable": true,
  "coverage_score": 0.95,
  "reason_zh": "为何单集足够或必须拆分",
  "preserved_essential_points_zh": ["已保留的重要内容"],
  "missing_essential_points_zh": ["单集无法容纳的重要内容"],
  "recommended_part_count": 1,
  "parts": [
    {{
      "part_index": 1,
      "title_zh": "本集主题",
      "purpose_zh": "本集独立解决什么问题",
      "event_ids": [1, 2],
      "must_preserve_zh": ["不可丢失的机制、转折或结论"],
      "opening_context_zh": "必要的开场上下文",
      "ending_payoff_zh": "本集完整落点"
    }}
  ]
}}

LAYERED SOURCE MAP:
{json.dumps(layered_context, ensure_ascii=False)}

CHRONOLOGICAL SOURCE EVIDENCE:
{json.dumps(events, ensure_ascii=False)}

CURRENT ONE-PART STORY:
{json.dumps(final_story, ensure_ascii=False)}

LOCAL REASONS FOR REQUESTING THIS CHECK (signals only, not proof that splitting is required):
{json.dumps(trigger_reasons, ensure_ascii=False)}
""".strip()


def _normalize_evaluation(
    value: dict[str, Any],
    events: list[dict[str, Any]],
    max_parts: int,
    required_event_ids: set[int] | None = None,
) -> dict[str, Any]:
    valid_ids = {int(item.get("id", 0) or 0) for item in events}
    acceptable = bool(value.get("single_part_acceptable", True))
    raw_parts = value.get("parts", [])
    raw_parts = raw_parts if isinstance(raw_parts, list) else []
    parts: list[dict[str, Any]] = []
    for raw in raw_parts[:max_parts]:
        if not isinstance(raw, dict):
            continue
        event_ids = sorted(
            {
                int(event_id)
                for event_id in raw.get("event_ids", [])
                if str(event_id).isdigit() and int(event_id) in valid_ids
            }
        )
        if not event_ids:
            continue
        parts.append(
            {
                "part_index": len(parts) + 1,
                "title_zh": str(raw.get("title_zh", "")).strip()
                or f"第 {len(parts) + 1} 集",
                "purpose_zh": str(raw.get("purpose_zh", "")).strip(),
                "event_ids": event_ids,
                "must_preserve_zh": [
                    str(item).strip()
                    for item in raw.get("must_preserve_zh", [])
                    if str(item).strip()
                ],
                "opening_context_zh": str(raw.get("opening_context_zh", "")).strip(),
                "ending_payoff_zh": str(raw.get("ending_payoff_zh", "")).strip(),
            }
        )
    requested_count = max(
        1,
        min(max_parts, int(value.get("recommended_part_count", len(parts) or 1) or 1)),
    )
    if not acceptable and len(parts) < 2:
        part_count = max(2, requested_count)
        ordered_ids = [int(item.get("id", 0) or 0) for item in events]
        parts = []
        for index in range(part_count):
            start = round(index * len(ordered_ids) / part_count)
            end = round((index + 1) * len(ordered_ids) / part_count)
            ids = ordered_ids[start:end]
            if ids:
                parts.append(
                    {
                        "part_index": len(parts) + 1,
                        "title_zh": f"第 {len(parts) + 1} 集",
                        "purpose_zh": "保留本阶段的重要内容",
                        "event_ids": ids,
                        "must_preserve_zh": [],
                        "opening_context_zh": "",
                        "ending_payoff_zh": "",
                    }
                )
    if acceptable:
        parts = []
        requested_count = 1
    else:
        requested_count = max(2, min(max_parts, len(parts)))
        parts = parts[:requested_count]
        assigned = {
            event_id for item in parts for event_id in item.get("event_ids", [])
        }
        missing_required = ((required_event_ids or set()) & valid_ids) - assigned
        for event_id in sorted(missing_required):
            nearest = min(
                parts,
                key=lambda item: min(
                    abs(event_id - existing) for existing in item.get("event_ids", [event_id])
                ),
            )
            nearest["event_ids"] = sorted(
                set(nearest.get("event_ids", [])) | {event_id}
            )
        for index, item in enumerate(parts, start=1):
            item["part_index"] = index
    try:
        score = max(0.0, min(1.0, float(value.get("coverage_score", 0) or 0)))
    except (TypeError, ValueError):
        score = 0.0
    return {
        "single_part_acceptable": acceptable,
        "coverage_score": round(score, 3),
        "reason_zh": str(value.get("reason_zh", "")).strip(),
        "preserved_essential_points_zh": [
            str(item).strip()
            for item in value.get("preserved_essential_points_zh", [])
            if str(item).strip()
        ],
        "missing_essential_points_zh": [
            str(item).strip()
            for item in value.get("missing_essential_points_zh", [])
            if str(item).strip()
        ],
        "recommended_part_count": requested_count,
        "parts": parts,
    }
