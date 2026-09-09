from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .media_service import _probe_with_ffprobe, _probe_with_pyav, _resolve_tool


ProgressCallback = Callable[[float, str], None]
VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def stable_asset_id(path: Path) -> str:
    """Return a deterministic ID without reading a potentially large media file."""
    fingerprint = str(path.resolve()).casefold()
    return "asset-" + hashlib.sha1(fingerprint.encode("utf-8", errors="replace")).hexdigest()[:12]


def merge_downloaded_stock_assets(manifest_json: Path, search_json: Path) -> dict[str, Any]:
    manifest = _read_json(manifest_json, {"schema_version": 2, "assets": []})
    assets = [dict(item) for item in manifest.get("assets", []) if isinstance(item, dict)]
    by_path = {
        str(Path(str(item.get("source_path", ""))).resolve()).casefold(): item
        for item in assets
        if str(item.get("source_path", "")).strip()
    }
    search = _read_json(search_json, {})
    for result in search.get("results", []):
        if not isinstance(result, dict) or not str(result.get("local_path", "")).strip():
            continue
        path = Path(str(result["local_path"])).resolve()
        selected_id = str(result.get("selected_candidate_id", ""))
        candidate = next(
            (
                dict(item)
                for item in result.get("candidates", [])
                if isinstance(item, dict) and str(item.get("candidate_id", "")) == selected_id
            ),
            {},
        )
        key = str(path).casefold()
        record = by_path.get(key, {})
        narration_ids = {
            int(value)
            for value in record.get("source_narration_ids", [])
            if str(value).isdigit() and int(value) > 0
        }
        narration_id = int(result.get("narration_id", 0) or 0)
        if narration_id > 0:
            narration_ids.add(narration_id)
        record.update(
            {
                "id": str(record.get("id", "")) or stable_asset_id(path),
                "name": path.name,
                "source_path": str(path),
                "kind": "video" if path.suffix.lower() in VIDEO_SUFFIXES else "image",
                "size_bytes": path.stat().st_size if path.is_file() else 0,
                "file_status": "available" if path.is_file() else "missing",
                "analysis_status": str(record.get("analysis_status", "pending")),
                "source": "stock",
                "provider": str(candidate.get("provider", "")),
                "provider_id": str(candidate.get("provider_id", "")),
                "author": str(candidate.get("author", "")),
                "author_url": str(candidate.get("author_url", "")),
                "license_page": str(candidate.get("page_url", "")),
                "search_query": str(result.get("query", "")),
                "source_narration_id": min(narration_ids) if narration_ids else 0,
                "source_narration_ids": sorted(narration_ids),
            }
        )
        if key not in by_path:
            assets.append(record)
            by_path[key] = record
    return _write_manifest(manifest_json, assets)


def analyze_manuscript_assets(
    manifest_json: Path,
    analysis_json: Path,
    thumbnails_dir: Path,
    config: dict[str, Any],
    app_root: Path,
    progress: ProgressCallback,
) -> dict[str, Any]:
    manifest = _read_json(manifest_json, {})
    assets = [dict(item) for item in manifest.get("assets", []) if isinstance(item, dict)]
    if not assets:
        raise ValueError("没有可分析的素材，请先导入或下载素材")
    previous = _read_json(analysis_json, {})
    cached = {
        str(item.get("asset_id", "")): item
        for item in previous.get("assets", [])
        if isinstance(item, dict) and str(item.get("analysis_status", "")) == "completed"
    }
    shared = config.get("shared", {})
    ffmpeg = _resolve_tool(str(shared.get("ffmpeg_bin", "ffmpeg")), app_root, "ffmpeg")
    ffprobe = _resolve_tool(str(shared.get("ffprobe_bin", "ffprobe")), app_root, "ffprobe")
    thumbnails_dir.mkdir(parents=True, exist_ok=True)
    analyzed_assets: list[dict[str, Any]] = []
    scenes: list[dict[str, Any]] = []
    next_scene_id = 1

    for index, raw in enumerate(assets, start=1):
        path = Path(str(raw.get("source_path", "")))
        asset_id = str(raw.get("id", "")) or stable_asset_id(path)
        raw["id"] = asset_id
        raw["file_status"] = "available" if path.is_file() else "missing"
        progress((index - 1) / len(assets), f"正在分析素材 {index}/{len(assets)}：{path.name}")
        cached_item = cached.get(asset_id)
        if cached_item and _cache_is_current(cached_item, path):
            analyzed = dict(cached_item)
        elif not path.is_file():
            analyzed = {
                "asset_id": asset_id,
                "source_path": str(path),
                "analysis_status": "missing",
                "file_status": "missing",
                "scenes": [],
            }
        else:
            try:
                analyzed = _analyze_one(raw, path, thumbnails_dir, ffmpeg, ffprobe)
            except Exception as exc:
                analyzed = {
                    "asset_id": asset_id,
                    "source_path": str(path),
                    "file_size": path.stat().st_size,
                    "modified_ns": path.stat().st_mtime_ns,
                    "file_status": "available",
                    "analysis_status": "failed",
                    "error": str(exc).strip() or type(exc).__name__,
                    "scenes": [],
                }
        raw["analysis_status"] = str(analyzed.get("analysis_status", "failed"))
        raw["duration_sec"] = float(analyzed.get("duration_sec", 0) or 0)
        raw["width"] = int(analyzed.get("width", 0) or 0)
        raw["height"] = int(analyzed.get("height", 0) or 0)
        for scene in analyzed.get("scenes", []):
            if not isinstance(scene, dict):
                continue
            normalized = dict(scene)
            normalized["event_id"] = next_scene_id
            normalized["scene_id"] = f"{asset_id}-scene-{int(scene.get('scene_index', next_scene_id)):03d}"
            next_scene_id += 1
            scenes.append(normalized)
        analyzed_assets.append(analyzed)
        _write_analysis(analysis_json, analyzed_assets, scenes, complete=False)
        _write_manifest(manifest_json, assets)

    payload = _write_analysis(analysis_json, analyzed_assets, scenes, complete=True)
    _write_manifest(manifest_json, assets)
    progress(1.0, f"素材分析完成：{len(analyzed_assets)} 个文件，{len(scenes)} 个候选镜头")
    return payload


def generate_manuscript_matches(
    storyboard_json: Path,
    analysis_json: Path,
    matches_json: Path,
) -> dict[str, Any]:
    storyboard = _read_json(storyboard_json, {})
    beats = [dict(item) for item in storyboard.get("beats", []) if isinstance(item, dict)]
    analysis = _read_json(analysis_json, {})
    scenes = [dict(item) for item in analysis.get("scenes", []) if isinstance(item, dict)]
    if not beats:
        raise ValueError("没有可匹配的分镜")
    if not scenes:
        raise ValueError("素材分析中没有可用候选镜头")

    existing = _read_json(matches_json, {})
    locked_by_narration = {
        int(item.get("narration_id", 0) or 0): dict(item)
        for item in existing.get("items", [])
        if isinstance(item, dict) and bool(item.get("locked", False))
    }
    candidate_map: dict[int, list[dict[str, Any]]] = {}
    for index, beat in enumerate(beats, start=1):
        narration_id = int(beat.get("narration_id", beat.get("id", index)) or index)
        ranked = [_score_scene(beat, scene) for scene in scenes]
        ranked.sort(key=lambda item: (-float(item["score"]), -float(item["duration_sec"])))
        candidate_map[narration_id] = ranked

    selected: dict[int, dict[str, Any]] = {}
    used_scene_ids: set[str] = set()
    for narration_id, old in locked_by_narration.items():
        scene_id = str(old.get("selected_scene_id", ""))
        candidate = next(
            (item for item in candidate_map.get(narration_id, []) if str(item.get("scene_id", "")) == scene_id),
            None,
        )
        if candidate:
            selected[narration_id] = candidate
            used_scene_ids.add(scene_id)

    pending: list[tuple[float, int]] = []
    for narration_id, candidates in candidate_map.items():
        if narration_id in selected:
            continue
        margin = float(candidates[0]["score"]) - (float(candidates[1]["score"]) if len(candidates) > 1 else 0.0)
        pending.append((margin, narration_id))
    for _margin, narration_id in sorted(pending, reverse=True):
        candidates = candidate_map[narration_id]
        choice = next((item for item in candidates if str(item["scene_id"]) not in used_scene_ids), candidates[0])
        selected[narration_id] = choice
        used_scene_ids.add(str(choice["scene_id"]))

    items: list[dict[str, Any]] = []
    for index, beat in enumerate(beats, start=1):
        narration_id = int(beat.get("narration_id", beat.get("id", index)) or index)
        choice = selected[narration_id]
        duration = max(0.2, float(beat.get("estimated_duration_sec", 0) or 0))
        candidates = candidate_map[narration_id][: min(5, len(scenes))]
        if all(str(item.get("scene_id", "")) != str(choice["scene_id"]) for item in candidates):
            candidates = [choice, *candidates[:4]]
        confidence = _confidence(float(choice["score"]))
        clip = _clip_for_scene(choice, duration)
        locked = narration_id in locked_by_narration
        items.append(
            {
                "narration_id": narration_id,
                "text_en": str(beat.get("text_en", "")),
                "visual_query": " ".join(str(v) for v in beat.get("search_queries_en", []) if str(v).strip()),
                "narration_duration_sec": duration,
                "selected_event_id": int(choice["event_id"]),
                "selected_scene_id": str(choice["scene_id"]),
                "selected_start": float(clip["start"]),
                "selected_end": float(clip["end"]),
                "selected_clips": [clip],
                "coverage_sec": round(float(clip["end"]) - float(clip["start"]), 3),
                "selection_mode": "manual" if locked else "automatic",
                "locked": locked,
                "confidence": confidence,
                "confidence_label": {"green": "高", "yellow": "中", "red": "低"}[confidence],
                "match_reason": str(choice["reason"]),
                "missing_prompt": str(beat.get("ideal_shot_zh", "")) if confidence == "red" else "",
                "candidates": candidates,
            }
        )
    payload = {
        "schema_version": 3,
        "content_mode": "manuscript",
        "strategy": "global semantic assignment + unique scene preference + locked selection preservation",
        "items": items,
        "confidence_summary": {
            color: sum(item["confidence"] == color for item in items)
            for color in ("green", "yellow", "red")
        },
    }
    matches_json.parent.mkdir(parents=True, exist_ok=True)
    matches_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def set_manuscript_match_lock(matches_json: Path, narration_id: int, locked: bool) -> dict[str, Any]:
    payload = _read_json(matches_json, {})
    for item in payload.get("items", []):
        if isinstance(item, dict) and int(item.get("narration_id", 0) or 0) == narration_id:
            item["locked"] = bool(locked)
            if locked:
                item["selection_mode"] = "manual"
            break
    else:
        raise ValueError("找不到对应的分镜")
    matches_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _analyze_one(
    raw: dict[str, Any], path: Path, thumbnails_dir: Path, ffmpeg: str | None, ffprobe: str | None
) -> dict[str, Any]:
    kind = str(raw.get("kind", "video"))
    if kind == "video":
        metadata = _probe_with_ffprobe(path, ffprobe) if ffprobe else _probe_with_pyav(path)
        duration = max(0.1, float(metadata.get("duration_sec", 0) or 0))
        cuts = _detect_scene_cuts(path, ffmpeg, duration) if ffmpeg else []
        boundaries = [0.0, *cuts, duration]
    else:
        metadata = _probe_image(path, ffprobe)
        duration = 0.0
        boundaries = [0.0, 0.0]
    base_tags = _base_tags(raw, path)
    scenes: list[dict[str, Any]] = []
    count = max(1, len(boundaries) - 1)
    for index in range(count):
        start = float(boundaries[index]) if kind == "video" else 0.0
        end = float(boundaries[index + 1]) if kind == "video" else 0.0
        thumb = thumbnails_dir / f"{raw['id']}-{index + 1:03d}.jpg"
        if not thumb.exists():
            _make_thumbnail(path, thumb, (start + end) / 2 if end > start else 0.0, kind, ffmpeg)
        try:
            keyframe = str(thumb.relative_to(thumbnails_dir.parent.parent)).replace("\\", "/")
        except ValueError:
            keyframe = str(thumb)
        scenes.append(
            {
                "asset_id": str(raw["id"]),
                "scene_index": index + 1,
                "source_path": str(path.resolve()),
                "media_kind": kind,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration_sec": round(max(0.0, end - start), 3),
                "width": int(metadata.get("width", 0) or 0),
                "height": int(metadata.get("height", 0) or 0),
                "keyframe": keyframe,
                "tags": base_tags,
                "tag_profile": {
                    "subjects": [],
                    "actions": [],
                    "environment": [],
                    "shot_scale": "unknown",
                    "direction": "unknown",
                    "mood": [],
                    "unsuitable": [],
                    "inferred_keywords": base_tags,
                    "confidence": "metadata_only",
                },
                "description": _description(raw, path),
                "uncertain": True,
                "source": str(raw.get("source", "local")),
                "provider": str(raw.get("provider", "")),
                "author": str(raw.get("author", "")),
                "license_page": str(raw.get("license_page", "")),
                "source_narration_id": int(raw.get("source_narration_id", 0) or 0),
                "source_narration_ids": [
                    int(value)
                    for value in raw.get("source_narration_ids", [])
                    if str(value).isdigit() and int(value) > 0
                ],
            }
        )
    return {
        "asset_id": str(raw["id"]),
        "source_path": str(path.resolve()),
        "file_size": path.stat().st_size,
        "modified_ns": path.stat().st_mtime_ns,
        "file_status": "available",
        "analysis_status": "completed",
        "kind": kind,
        "duration_sec": duration,
        "width": int(metadata.get("width", 0) or 0),
        "height": int(metadata.get("height", 0) or 0),
        "scenes": scenes,
    }


def _detect_scene_cuts(path: Path, ffmpeg: str | None, duration: float) -> list[float]:
    if not ffmpeg or duration < 1.5:
        return []
    command = [
        ffmpeg, "-hide_banner", "-i", str(path), "-filter:v", "select='gt(scene,0.32)',showinfo",
        "-an", "-f", "null", "-",
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    cuts = [float(value) for value in re.findall(r"pts_time:([0-9]+(?:\.[0-9]+)?)", result.stderr or "")]
    normalized: list[float] = []
    for value in cuts:
        if value < 0.75 or value > duration - 0.75:
            continue
        if not normalized or value - normalized[-1] >= 0.75:
            normalized.append(round(value, 3))
    return normalized


def _make_thumbnail(path: Path, output: Path, timestamp: float, kind: str, ffmpeg: str | None) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if not ffmpeg:
        if kind == "image":
            from PySide6.QtCore import Qt
            from PySide6.QtGui import QImageReader

            image = QImageReader(str(path)).read()
            if image.isNull():
                raise ValueError(f"无法读取图片素材：{path.name}")
            if image.width() > 640:
                image = image.scaledToWidth(640, Qt.TransformationMode.SmoothTransformation)
            if not image.save(str(output), "JPG", 88):
                raise ValueError(f"无法生成图片缩略图：{path.name}")
            return
        raise RuntimeError("未找到 FFmpeg，无法生成素材缩略图")
    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    if kind == "video":
        command.extend(["-ss", f"{max(0.0, timestamp):.3f}", "-i", str(path)])
    else:
        command.extend(["-i", str(path)])
    command.extend(["-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "3", str(output)])
    subprocess.run(command, capture_output=True, check=True)


def _probe_image(path: Path, ffprobe: str | None) -> dict[str, Any]:
    if ffprobe:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        )
        streams = json.loads(result.stdout).get("streams", [])
        stream = streams[0] if streams else {}
        return {"width": int(stream.get("width", 0) or 0), "height": int(stream.get("height", 0) or 0)}
    from PySide6.QtGui import QImageReader
    size = QImageReader(str(path)).size()
    return {"width": size.width(), "height": size.height()}


def _base_tags(raw: dict[str, Any], path: Path) -> list[str]:
    text = " ".join((path.stem, str(raw.get("search_query", "")), str(raw.get("provider", ""))))
    return sorted(_tokens(text))


def _description(raw: dict[str, Any], path: Path) -> str:
    query = str(raw.get("search_query", "")).strip()
    return f"搜索元数据：{query}" if query else f"文件名线索：{path.stem}（未进行视觉确认）"


def _score_scene(beat: dict[str, Any], scene: dict[str, Any]) -> dict[str, Any]:
    required = _tokens(" ".join(str(v) for v in beat.get("must_show_zh", []) if str(v).strip()))
    fallback = _tokens(" ".join(str(v) for v in beat.get("acceptable_fallbacks_zh", []) if str(v).strip()))
    avoid = _tokens(" ".join(str(v) for v in beat.get("avoid_zh", []) if str(v).strip()))
    query = _tokens(" ".join(str(v) for v in beat.get("search_queries_en", []) if str(v).strip()))
    ideal = _tokens(str(beat.get("ideal_shot_zh", "")))
    scene_tokens = _tokens(" ".join(str(v) for v in scene.get("tags", [])) + " " + str(scene.get("description", "")))
    overlap = lambda wanted: len(wanted & scene_tokens) / max(1, len(wanted))
    query_score = overlap(query)
    required_score = overlap(required)
    fallback_score = overlap(fallback | ideal)
    avoid_score = overlap(avoid)
    narration_id = int(beat.get("narration_id", beat.get("id", 0)) or 0)
    linked_ids = {
        int(value)
        for value in scene.get("source_narration_ids", [])
        if str(value).isdigit()
    }
    if int(scene.get("source_narration_id", 0) or 0) > 0:
        linked_ids.add(int(scene.get("source_narration_id", 0) or 0))
    source_bonus = 0.22 if narration_id in linked_ids else 0.0
    duration = float(scene.get("duration_sec", 0) or 0)
    target = max(0.2, float(beat.get("estimated_duration_sec", 0) or 0))
    duration_score = 1.0 if str(scene.get("media_kind", "")) == "image" else min(1.0, duration / target)
    score = 0.08 + source_bonus + 0.34 * query_score + 0.18 * required_score + 0.10 * fallback_score + 0.08 * duration_score - 0.35 * avoid_score
    score = min(0.99, max(0.01, score))
    reasons = []
    if source_bonus:
        reasons.append("来自该分镜的素材搜索")
    if query_score >= 0.35:
        reasons.append("搜索词与素材标签吻合")
    if required_score > 0:
        reasons.append("覆盖部分必须内容")
    if avoid_score > 0:
        reasons.append("包含避用线索，已降分")
    if not reasons:
        reasons.append("仅有文件名或基础元数据支持")
    candidate = dict(scene)
    if str(candidate.get("media_kind", "")) == "image":
        candidate["end"] = 180.0
        candidate["duration_sec"] = 180.0
    candidate.update({"score": round(score, 3), "reason": " · ".join(reasons)})
    return candidate


def _clip_for_scene(scene: dict[str, Any], target: float) -> dict[str, Any]:
    start = float(scene.get("start", 0) or 0)
    kind = str(scene.get("media_kind", "video"))
    end = start + target if kind == "image" else min(float(scene.get("end", start)), start + target)
    return {
        "event_id": int(scene.get("event_id", 0) or 0),
        "scene_id": str(scene.get("scene_id", "")),
        "asset_id": str(scene.get("asset_id", "")),
        "source_path": str(scene.get("source_path", "")),
        "media_kind": kind,
        "width": int(scene.get("width", 0) or 0),
        "height": int(scene.get("height", 0) or 0),
        "keyframe": str(scene.get("keyframe", "")),
        "start": round(start, 3),
        "end": round(end, 3),
    }


def _confidence(score: float) -> str:
    return "green" if score >= 0.62 else "yellow" if score >= 0.34 else "red"


def _tokens(text: str) -> set[str]:
    normalized = str(text).casefold()
    words = set(re.findall(r"[a-z0-9]+", normalized))
    chunks = re.findall(r"[\u3400-\u9fff]+", normalized)
    for chunk in chunks:
        words.update(chunk[index : index + 2] for index in range(max(1, len(chunk) - 1)))
    return {word for word in words if len(word) > 1}


def _cache_is_current(cached: dict[str, Any], path: Path) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    return int(cached.get("file_size", -1)) == stat.st_size and int(cached.get("modified_ns", -1)) == stat.st_mtime_ns


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else dict(default)
    except (OSError, ValueError, TypeError):
        return dict(default)


def _write_manifest(path: Path, assets: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {"schema_version": 2, "asset_count": len(assets), "assets": assets}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _write_analysis(path: Path, assets: list[dict[str, Any]], scenes: list[dict[str, Any]], complete: bool) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "analysis_status": "completed" if complete else "running",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "asset_count": len(assets),
        "scene_count": len(scenes),
        "assets": assets,
        "scenes": scenes,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
