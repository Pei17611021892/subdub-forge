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


def migrate_repository() -> None:
    bridge_version = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
    repository = str(bridge_version["repository"]).strip(" /")
    request = urllib.request.Request(
        f"https://github.com/{repository}/archive/refs/heads/main.zip",
        headers={"User-Agent": "StoryCut-Legacy-Migration", "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        archive_data = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(archive_data) > MAX_ARCHIVE_BYTES:
        raise RuntimeError("仓库更新包超过安全大小限制")

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
