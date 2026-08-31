from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .fact_review_service import (
    _compact_narration,
    _normalize_report as normalize_fact_report,
    _select_evidence_events,
)
from .vision_service import api_configuration, friendly_api_error


_TERM_CATEGORIES = {
    "term_variant",
    "name_consistency",
    "abbreviation",
    "capitalization",
    "unit_format",
    "number_consistency",
}


def review_story_content(
    events_json: Path,
    story_json: Path,
    output_json: Path,
    config: dict[str, Any],
    app_root: Path,
) -> dict[str, Any]:
    """Review factual risk and terminology consistency in one model request."""
    from openai import OpenAI

    api = api_configuration(config, app_root, "story")
    api_key = str(api["api_key"])
    base_url = str(api["base_url"]).strip() or None
    if not api_key:
        raise RuntimeError("未配置 OPENAI_API_KEY，无法进行文案审查")

    events_payload = json.loads(events_json.read_text(encoding="utf-8"))
    story = json.loads(story_json.read_text(encoding="utf-8"))
    narration = [
        _compact_narration(item)
        for item in story.get("narration", [])
        if isinstance(item, dict)
    ]
    if not narration:
        raise ValueError("当前故事没有可审查的英文解说")
    events = _select_evidence_events(events_payload.get("events", []), narration)

    review_config = config.get("content_review", {})
    story_config = config.get("story", {})
    model = (
        str(review_config.get("model", "")).strip()
        or str(config.get("fact_review", {}).get("model", "")).strip()
        or str(story_config.get("editor_model", "")).strip()
        or str(story_config.get("model", "gpt-4o-mini"))
    )
    client = OpenAI(api_key=api_key, base_url=base_url)
    prompt = _build_prompt(events, narration)
    try:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
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
        raise friendly_api_error(exc, base_url, "文案综合审查") from exc

    raw = _parse_json_object(str(response.choices[0].message.content or ""))
    fact_report = normalize_fact_report(
        raw.get("fact_review", {}) if isinstance(raw.get("fact_review"), dict) else {},
        narration,
        model,
    )
    terminology_report = _normalize_terminology_report(
        raw.get("terminology_review", {})
        if isinstance(raw.get("terminology_review"), dict)
        else {},
        narration,
        model,
    )
    fact_line_ids = {
        int(issue["narration_ids"][0])
        for issue in fact_report.get("issues", [])
        if isinstance(issue, dict) and len(issue.get("narration_ids", [])) == 1
    }
    terminology_report["issues"] = [
        issue
        for issue in terminology_report.get("issues", [])
        if int(issue["narration_ids"][0]) not in fact_line_ids
    ]
    terminology_report["issue_count"] = len(terminology_report["issues"])
    terminology_report["status"] = (
        "warning" if terminology_report["issues"] else "pass"
    )
    report = {
        "schema_version": 1,
        "reviewed_at": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "fact_review": fact_report,
        "terminology_review": terminology_report,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def _build_prompt(events: list[dict[str, Any]], narration: list[dict[str, Any]]) -> str:
    return f"""
You are the final content reviewer for an English science and general-knowledge short video.
Perform factual-risk review and terminology-consistency review together, but keep their results separate.

FACT REVIEW
- Audit concrete facts, numbers, units, identities, mechanisms, comparisons, causes, and outcomes against source evidence and stable general knowledge.
- Separate unsupported-by-source claims from generally inaccurate claims. Preserve uncertainty and never invent citations or corrections.
- Do not flag style, emotion, metaphor, or harmless connective language.
- high = materially false or misleading; medium = meaningful uncertainty or unsupported specificity; low = precision improvement.

TERMINOLOGY REVIEW
- Unify repeated technical terms, species or machine names, proper names, abbreviations, capitalization, units, symbols, model numbers, and repeated numeric facts.
- Preserve exact visible labels, units, symbols, and model numbers. Do not add phonetic spelling or TTS pronunciation instructions.
- Do not flag harmless contextual or singular/plural variation.

SAFE APPLICATION
- Every issue must bind to exactly one narration ID and provide a complete, minimally corrected replacement for that line.
- Combine multiple problems affecting the same line into one issue inside each section. Return at most 12 issues per section.
- If one line has both a factual problem and a terminology problem, put the fully combined replacement only in fact_review and omit that line from terminology_review so two suggestions can never overwrite each other.

Return exactly one JSON object and no Markdown:
{{
  "fact_review": {{
    "summary_zh": "事实审查中文结论",
    "issues": [{{
      "severity": "high|medium|low",
      "category": "source_support|general_fact|number_unit|causality|terminology",
      "narration_ids": [1],
      "event_ids": [1],
      "claim_en": "claim",
      "reason_zh": "风险原因",
      "suggestion_en": "完整且最小修改后的该句英文"
    }}]
  }},
  "terminology_review": {{
    "summary_zh": "术语一致性中文结论",
    "canonical_terms": [{{
      "source_term": "原片术语或概念",
      "preferred_en": "统一英文",
      "reason_zh": "选择理由"
    }}],
    "issues": [{{
      "category": "term_variant|name_consistency|abbreviation|capitalization|unit_format|number_consistency",
      "narration_ids": [1],
      "term": "术语、单位或数字",
      "variants": ["variant A", "variant B"],
      "reason_zh": "为什么前后不一致",
      "suggestion_en": "完整且最小修改后的该句英文"
    }}]
  }}
}}

SOURCE EVIDENCE:
{json.dumps(events, ensure_ascii=False)}

COMPLETE ENGLISH NARRATION:
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
            raise ValueError("文案审查模型未返回可解析的 JSON 对象")
        value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("文案审查模型返回结果不是对象")
    return value


def _normalize_terminology_report(
    value: dict[str, Any], narration: list[dict[str, Any]], model: str
) -> dict[str, Any]:
    valid_ids = {int(item.get("id", 0) or 0) for item in narration}
    occupied_ids: set[int] = set()
    issues: list[dict[str, Any]] = []
    for raw in value.get("issues", []):
        if not isinstance(raw, dict):
            continue
        raw_ids = raw.get("narration_ids", [])
        raw_ids = raw_ids if isinstance(raw_ids, list) else []
        narration_ids = sorted(
            {
                int(item)
                for item in raw_ids
                if str(item).isdigit() and int(item) in valid_ids
            }
        )
        if len(narration_ids) != 1 or narration_ids[0] in occupied_ids:
            continue
        suggestion = str(raw.get("suggestion_en", "")).strip()
        if not suggestion:
            continue
        occupied_ids.add(narration_ids[0])
        category = str(raw.get("category", "term_variant")).lower()
        variants = raw.get("variants", [])
        issues.append(
            {
                "id": len(issues) + 1,
                "category": category if category in _TERM_CATEGORIES else "term_variant",
                "narration_ids": narration_ids,
                "term": str(raw.get("term", "")).strip(),
                "variants": [str(item).strip() for item in variants if str(item).strip()]
                if isinstance(variants, list)
                else [],
                "reason_zh": str(raw.get("reason_zh", "")).strip(),
                "suggestion_en": suggestion,
            }
        )
    canonical_terms = []
    for raw in value.get("canonical_terms", []):
        if not isinstance(raw, dict):
            continue
        preferred = str(raw.get("preferred_en", "")).strip()
        if preferred:
            canonical_terms.append(
                {
                    "source_term": str(raw.get("source_term", "")).strip(),
                    "preferred_en": preferred,
                    "reason_zh": str(raw.get("reason_zh", "")).strip(),
                }
            )
    return {
        "schema_version": 1,
        "reviewed_at": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "status": "warning" if issues else "pass",
        "summary_zh": str(value.get("summary_zh", "")).strip()
        or (f"发现 {len(issues)} 处术语一致性问题" if issues else "术语、单位和名称前后一致"),
        "issue_count": len(issues),
        "stale": False,
        "canonical_terms": canonical_terms[:24],
        "issues": issues,
        "disclaimer": "只检查文案中的术语、单位、名称和数字一致性；具体发音由配音工具处理。",
    }
