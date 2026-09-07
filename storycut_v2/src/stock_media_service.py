from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ProgressCallback = Callable[[float, str], None]


def search_stock_media(
    storyboard_json: Path,
    output_json: Path,
    pexels_api_key: str,
    pixabay_api_key: str,
    progress: ProgressCallback,
    candidates_per_beat: int = 3,
) -> dict[str, Any]:
    storyboard = json.loads(storyboard_json.read_text(encoding="utf-8"))
    beats = [dict(item) for item in storyboard.get("beats", []) if isinstance(item, dict)]
    if not beats:
        raise ValueError("没有可用于搜索素材的分镜")
    if not pexels_api_key.strip() and not pixabay_api_key.strip():
        raise RuntimeError("请先配置 Pexels 或 Pixabay API Key")

    limit = max(1, min(6, int(candidates_per_beat)))
    query_cache: dict[str, list[dict[str, Any]]] = {}
    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    for index, beat in enumerate(beats, start=1):
        queries = [str(value).strip() for value in beat.get("search_queries_en", []) if str(value).strip()]
        query = queries[0] if queries else str(beat.get("text_en", "")).strip()[:100]
        cache_key = query.casefold()
        if cache_key not in query_cache:
            candidates: list[dict[str, Any]] = []
            if pexels_api_key.strip():
                try:
                    candidates.extend(_search_pexels(query, pexels_api_key, limit + 2))
                except Exception as exc:
                    warnings.append(f"Pexels：{exc}")
            if pixabay_api_key.strip():
                try:
                    candidates.extend(_search_pixabay(query, pixabay_api_key, limit + 2))
                except Exception as exc:
                    warnings.append(f"Pixabay：{exc}")
            candidates.sort(key=lambda item: float(item.get("score", 0)), reverse=True)
            query_cache[cache_key] = candidates[:limit]
        selected = [dict(item) for item in query_cache[cache_key]]
        results.append(
            {
                "narration_id": int(beat.get("narration_id", beat.get("id", index)) or index),
                "query": query,
                "ideal_shot_zh": str(beat.get("ideal_shot_zh", "")),
                "status": "found" if selected else "missing",
                "selected_candidate_id": str(selected[0].get("candidate_id", "")) if selected else "",
                "candidates": selected,
            }
        )
        progress(index / len(beats), f"正在搜索免费素材 {index}/{len(beats)}：{query}")

    found = sum(1 for item in results if item["candidates"])
    unique_warnings = list(dict.fromkeys(warnings))
    if found == 0 and unique_warnings:
        raise RuntimeError("免费素材接口未返回可用结果：" + "；".join(unique_warnings[:2]))
    payload = {
        "schema_version": 1,
        "providers": [
            name
            for name, key in (("Pexels", pexels_api_key), ("Pixabay", pixabay_api_key))
            if key.strip()
        ],
        "beat_count": len(results),
        "found_count": found,
        "coverage_percent": round(found * 100 / len(results)),
        "warnings": unique_warnings[:8],
        "results": results,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def download_selected_stock_media(
    search_json: Path,
    destination_dir: Path,
    progress: ProgressCallback,
) -> dict[str, Any]:
    payload = json.loads(search_json.read_text(encoding="utf-8"))
    results = [dict(item) for item in payload.get("results", []) if isinstance(item, dict)]
    selected: dict[str, dict[str, Any]] = {}
    for result in results:
        selected_id = str(result.get("selected_candidate_id", ""))
        candidate = next(
            (
                dict(item)
                for item in result.get("candidates", [])
                if isinstance(item, dict) and str(item.get("candidate_id", "")) == selected_id
            ),
            None,
        )
        if candidate:
            selected[selected_id] = candidate
    if not selected:
        raise ValueError("当前没有可下载的已选素材")

    destination_dir.mkdir(parents=True, exist_ok=True)
    downloaded: dict[str, str] = {}
    items = list(selected.items())
    for index, (candidate_id, candidate) in enumerate(items, start=1):
        url = str(candidate.get("download_url", "")).strip()
        if not url:
            continue
        suffix = ".mp4" if ".mp4" in url.lower() or candidate.get("duration_sec") else ".jpg"
        target = destination_dir / f"{candidate_id}{suffix}"
        if not target.exists() or target.stat().st_size == 0:
            temporary = target.with_suffix(target.suffix + ".part")
            request = Request(url, headers={"User-Agent": "StoryCut/3.0"})
            try:
                with urlopen(request, timeout=90) as response, temporary.open("wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        downloaded[candidate_id] = str(target.resolve())
        progress(index / len(items), f"正在下载已选免费素材 {index}/{len(items)}")

    for result in results:
        candidate_id = str(result.get("selected_candidate_id", ""))
        if candidate_id in downloaded:
            result["local_path"] = downloaded[candidate_id]
            result["download_status"] = "downloaded"
            for candidate in result.get("candidates", []):
                if isinstance(candidate, dict) and str(candidate.get("candidate_id", "")) == candidate_id:
                    candidate["local_path"] = downloaded[candidate_id]
    payload["results"] = results
    payload["downloaded_count"] = len(downloaded)
    search_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _search_pexels(query: str, api_key: str, count: int) -> list[dict[str, Any]]:
    url = "https://api.pexels.com/v1/videos/search?" + urlencode(
        {"query": query, "per_page": max(3, min(15, count)), "size": "medium"}
    )
    payload = _request_json(url, {"Authorization": api_key})
    results: list[dict[str, Any]] = []
    for rank, raw in enumerate(payload.get("videos", [])):
        if not isinstance(raw, dict):
            continue
        files = [item for item in raw.get("video_files", []) if isinstance(item, dict) and item.get("link")]
        files.sort(key=lambda item: (_quality_score(item), int(item.get("width", 0) or 0)), reverse=True)
        if not files:
            continue
        media = files[0]
        creator = raw.get("user", {}) if isinstance(raw.get("user"), dict) else {}
        results.append(
            {
                "candidate_id": f"pexels-{raw.get('id')}",
                "provider": "Pexels",
                "provider_id": str(raw.get("id", "")),
                "page_url": str(raw.get("url", "")),
                "preview_url": str(raw.get("image", "")),
                "download_url": str(media.get("link", "")),
                "width": int(media.get("width", 0) or 0),
                "height": int(media.get("height", 0) or 0),
                "duration_sec": float(raw.get("duration", 0) or 0),
                "author": str(creator.get("name", "")),
                "author_url": str(creator.get("url", "")),
                "score": 100 - rank * 3 + _media_bonus(media, raw),
            }
        )
    return results


def _search_pixabay(query: str, api_key: str, count: int) -> list[dict[str, Any]]:
    url = "https://pixabay.com/api/videos/?" + urlencode(
        {"key": api_key, "q": query[:100], "per_page": max(3, min(20, count)), "safesearch": "true"}
    )
    payload = _request_json(url, {})
    results: list[dict[str, Any]] = []
    for rank, raw in enumerate(payload.get("hits", [])):
        if not isinstance(raw, dict):
            continue
        variants = raw.get("videos", {}) if isinstance(raw.get("videos"), dict) else {}
        media = next((variants.get(key) for key in ("medium", "small", "large", "tiny") if isinstance(variants.get(key), dict) and variants[key].get("url")), None)
        if not media:
            continue
        results.append(
            {
                "candidate_id": f"pixabay-{raw.get('id')}",
                "provider": "Pixabay",
                "provider_id": str(raw.get("id", "")),
                "page_url": str(raw.get("pageURL", "")),
                "preview_url": str(media.get("thumbnail", "")),
                "download_url": str(media.get("url", "")),
                "width": int(media.get("width", 0) or 0),
                "height": int(media.get("height", 0) or 0),
                "duration_sec": float(raw.get("duration", 0) or 0),
                "author": str(raw.get("user", "")),
                "author_url": (
                    f"https://pixabay.com/users/{raw.get('user')}-{raw.get('user_id')}/"
                    if raw.get("user") and raw.get("user_id") else ""
                ),
                "score": 96 - rank * 3 + _media_bonus(media, raw),
            }
        )
    return results


def _request_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "StoryCut/3.0", **headers})
    with urlopen(request, timeout=25) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("素材接口没有返回 JSON 对象")
    return value


def _quality_score(media: dict[str, Any]) -> int:
    quality = str(media.get("quality", "")).lower()
    return {"uhd": 3, "hd": 2, "sd": 1}.get(quality, 0)


def _media_bonus(media: dict[str, Any], raw: dict[str, Any]) -> float:
    width = int(media.get("width", 0) or 0)
    height = int(media.get("height", 0) or 0)
    duration = float(raw.get("duration", 0) or 0)
    return (6 if width >= 1280 else 0) + (3 if height >= 720 else 0) + (4 if duration >= 5 else 0)
