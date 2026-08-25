from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .vision_service import api_configuration, friendly_api_error


_SEVERITIES = {"high", "medium", "low"}
_CATEGORIES = {
    "source_support",
    "general_fact",
    "number_unit",
    "causality",
    "terminology",
}


def review_story_facts(
    events_json: Path,
    story_json: Path,
    output_json: Path,
    config: dict[str, Any],
    app_root: Path,
) -> dict[str, Any]:
    """Run one optional, non-blocking factual-risk review for the current narration."""
    from openai import OpenAI

    api = api_configuration(config, app_root, "story")
    api_key = str(api["api_key"])
    base_url = str(api["base_url"]).strip() or None
    if not api_key:
        raise RuntimeError("未配置 OPENAI_API_KEY，无法进行事实审查")

    events_payload = json.loads(events_json.read_text(encoding="utf-8"))
    story = json.loads(story_json.read_text(encoding="utf-8"))
    narration = [_compact_narration(item) for item in story.get("narration", []) if isinstance(item, dict)]
    if not narration:
        raise ValueError("当前故事没有可审查的英文解说")
    events = _select_evidence_events(events_payload.get("events", []), narration)

    review_config = config.get("fact_review", {})
    story_config = config.get("story", {})
    model = (
        str(review_config.get("model", "")).strip()
        or str(story_config.get("editor_model", "")).strip()
        or str(story_config.get("model", "gpt-4o-mini"))
    )
    prompt = _build_review_prompt(events, narration)
    client = OpenAI(api_key=api_key, base_url=base_url)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
    except Exception as exc:
        if getattr(exc, "status_code", None) == 400 and "temperature" in str(exc).lower():
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                )
            except Exception as retry_exc:
                raise friendly_api_error(retry_exc, base_url, "科普事实审查") from retry_exc
        else:
            raise friendly_api_error(exc, base_url, "科普事实审查") from exc

    raw = _parse_json_object(str(response.choices[0].message.content or ""))
    report = _normalize_report(raw, narration, model)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(event.get("id", 0) or 0),
        "start": event.get("start", 0),
        "end": event.get("end", 0),
        "transcript": str(event.get("transcript", "")),
        "visual": str(event.get("visual_description", "")),
        "screen_text": event.get("screen_text", []),
        "technical_visual": event.get("technical_visual", {}),
        "uncertainty": str(event.get("visual_uncertainty", "")),
    }


def _compact_narration(item: dict[str, Any]) -> dict[str, Any]:
    event_ids = item.get("event_ids", [])
    return {
        "id": int(item.get("id", 0) or 0),
        "event_ids": event_ids if isinstance(event_ids, list) else [],
        "text_en": str(item.get("text_en", "")),
    }


def _select_evidence_events(
    raw_events: Any, narration: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    events = [item for item in raw_events if isinstance(item, dict)] if isinstance(raw_events, list) else []
    referenced = {
        int(event_id)
        for line in narration
        for event_id in (
            line.get("event_ids", []) if isinstance(line.get("event_ids", []), list) else []
        )
        if str(event_id).isdigit()
    }
    if not referenced:
        return [_compact_event(item) for item in events[:80]]
    evidence_ids = referenced | {event_id - 1 for event_id in referenced} | {
        event_id + 1 for event_id in referenced
    }
    selected = [item for item in events if int(item.get("id", 0) or 0) in evidence_ids]
    return [_compact_event(item) for item in selected]


def _build_review_prompt(events: list[dict[str, Any]], narration: list[dict[str, Any]]) -> str:
    return f"""
You are a cautious factual-risk editor for a science and general-knowledge short video.
Audit the English narration against the supplied source evidence and your stable general knowledge.

SCOPE
- This is an advisory review, not an internet-sourced verification and not a rewrite request.
- Check only concrete claims: facts, numbers, units, terminology, identities, mechanisms, comparisons, causes, and outcomes.
- Separate unsupported-by-source claims from claims that appear generally inaccurate.
- A claim can be plausible yet unsupported by this source; label that source_support.
- Do not flag style, emotion, metaphor, harmless connective language, or reasonable uncertainty wording.
- Do not treat the source transcript as automatically true: if it conflicts with stable knowledge, flag general_fact.
- Preserve uncertainty. Never manufacture citations, evidence, corrections, or exact values.
- Suggestions must minimally repair or soften the affected English line; never replace the whole story.
- Each issue must refer to exactly one narration line so its suggestion can be applied safely.
- Each issue must refer to exactly one narration line so its suggestion can be applied safely.
- high = likely materially false or seriously misleading; medium = meaningful uncertainty or unsupported specificity; low = terminology/precision improvement.
- Return at most 12 issues. Prioritize material high/medium risks and omit repetitive low-value warnings.

Return exactly one JSON object and no Markdown:
{{
  "summary_zh": "简洁中文结论",
  "issues": [
    {{
      "severity": "high|medium|low",
      "category": "source_support|general_fact|number_unit|causality|terminology",
      "narration_ids": [1],
      "event_ids": [1],
      "claim_en": "the exact claim being reviewed",
      "reason_zh": "为什么存在风险，并说明是原片证据问题还是常识问题",
      "suggestion_en": "minimal safer replacement; empty if no responsible correction is possible"
    }}
  ]
}}

SOURCE EVIDENCE:
{json.dumps(events, ensure_ascii=False)}

ENGLISH NARRATION:
{json.dumps(narration, ensure_ascii=False)}
""".strip()


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise ValueError("事实审查模型未返回可解析的 JSON 对象")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("事实审查模型返回结果不是对象")
    return value


def _normalize_report(
    value: dict[str, Any], narration: list[dict[str, Any]], model: str
) -> dict[str, Any]:
    valid_narration_ids = {int(item.get("id", 0)) for item in narration}
    issues: list[dict[str, Any]] = []
    for index, raw in enumerate(value.get("issues", []), start=1):
        if not isinstance(raw, dict):
            continue
        severity = str(raw.get("severity", "low")).lower()
        category = str(raw.get("category", "source_support")).lower()
        raw_narration_ids = raw.get("narration_ids", [])
        raw_narration_ids = raw_narration_ids if isinstance(raw_narration_ids, list) else []
        raw_event_ids = raw.get("event_ids", [])
        raw_event_ids = raw_event_ids if isinstance(raw_event_ids, list) else []
        narration_ids = sorted(
            {
                int(item)
                for item in raw_narration_ids
                if str(item).isdigit() and int(item) in valid_narration_ids
            }
        )
        issues.append(
            {
                "id": index,
                "severity": severity if severity in _SEVERITIES else "low",
                "category": category if category in _CATEGORIES else "source_support",
                "narration_ids": narration_ids,
                "event_ids": sorted(
                    {
                        int(item)
                        for item in raw_event_ids
                        if str(item).isdigit()
                    }
                ),
                "claim_en": str(raw.get("claim_en", "")).strip(),
                "reason_zh": str(raw.get("reason_zh", "")).strip(),
                "suggestion_en": str(raw.get("suggestion_en", "")).strip(),
            }
        )
    rank = {"high": 0, "medium": 1, "low": 2}
    issues.sort(key=lambda item: (rank[item["severity"]], item["id"]))
    counts = {severity: sum(item["severity"] == severity for item in issues) for severity in _SEVERITIES}
    status = "risk" if counts["high"] else "warning" if issues else "pass"
    return {
        "schema_version": 1,
        "reviewed_at": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "status": status,
        "summary_zh": str(value.get("summary_zh", "")).strip()
        or ("未发现明显事实风险" if not issues else f"发现 {len(issues)} 项需要人工确认的内容"),
        "issue_count": len(issues),
        "high_count": counts["high"],
        "medium_count": counts["medium"],
        "low_count": counts["low"],
        "stale": False,
        "disclaimer": "AI 辅助审查，未联网检索权威来源；高风险内容仍建议人工核对。",
        "issues": issues,
    }
