from __future__ import annotations

import importlib.util
import io
import json
import os
import urllib.request
import zipfile
from pathlib import Path


LEGACY_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = LEGACY_ROOT.parent
VERSION_FILE = LEGACY_ROOT / "version.json"
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024


def _system_proxies() -> dict[str, str]:
    try:
        detected = urllib.request.getproxies()
    except (OSError, ValueError):
        return {}
    proxies: dict[str, str] = {}
    for scheme in ("http", "https"):
        value = str(detected.get(scheme, "") or "").strip()
        if value:
            if "://" not in value:
                value = "http://" + value
            proxies[scheme] = value
    if "http" in proxies and "https" not in proxies:
        proxies["https"] = proxies["http"]
    return proxies


def _download_archive(repository: str) -> bytes:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(_system_proxies()))
    urls = (
        f"https://github.com/{repository}/archive/refs/heads/main.zip",
        f"https://codeload.github.com/{repository}/zip/refs/heads/main",
    )
    errors: list[str] = []
    for url in urls:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "StoryCut-Legacy-Migration", "Cache-Control": "no-cache"},
        )
        try:
            with opener.open(request, timeout=90) as response:
                data = response.read(MAX_ARCHIVE_BYTES + 1)
            if len(data) > MAX_ARCHIVE_BYTES:
                raise RuntimeError("仓库更新包超过安全大小限制")
            return data
        except Exception as exc:
            errors.append(str(exc))
    proxy_note = "，已尝试 Windows 系统代理" if _system_proxies() else ""
    raise RuntimeError("无法下载 GitHub 主分支" + proxy_note + "：" + "；".join(errors))


def migrate_repository() -> None:
    bridge_version = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
    repository = str(bridge_version["repository"]).strip(" /")
    archive_data = _download_archive(repository)

    with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
        files = [info for info in archive.infolist() if not info.is_dir()]
        if not files or "/" not in files[0].filename:
            raise RuntimeError("GitHub 更新包目录结构无效")
        prefix = files[0].filename.split("/", 1)[0] + "/"
        updater_info = archive.getinfo(prefix + "repository_updater.py")
        target_version = json.loads(
            archive.read(archive.getinfo(prefix + "storycut_v2/version.json")).decode("utf-8")
        )
        updater_path = REPOSITORY_ROOT / "repository_updater.py"
        temporary = updater_path.with_name(updater_path.name + ".migration_tmp")
        temporary.write_bytes(archive.read(updater_info))
        os.replace(temporary, updater_path)

    spec = importlib.util.spec_from_file_location("storycut_migration_updater", updater_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载新版仓库更新器")
    updater = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(updater)
    updater.apply_repository_archive("storycut_v2", target_version, archive_data)
