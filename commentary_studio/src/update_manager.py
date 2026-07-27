from __future__ import annotations

import io
import json
import os
import re
import shutil
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "version.json"
MANIFEST_FILE = ROOT / "update_manifest.json"
ARCHIVE_APP_PREFIX = "commentary_studio/"
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
PROTECTED_FILES = {"config.user.yaml"}
PROTECTED_DIRS = {"projects", "cache", "__pycache__"}


class UpdateError(RuntimeError):
    pass


def read_version(path: Path = VERSION_FILE) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise UpdateError(f"无法读取 StoryCut 版本文件：{exc}") from exc
    if not isinstance(value, dict) or not value.get("version") or not value.get("repository"):
        raise UpdateError("StoryCut 版本文件缺少 version 或 repository")
    return value


def version_key(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"v?(\d+(?:\.\d+){1,3})(?:[-+].*)?", value.strip())
    if not match:
        raise UpdateError(f"无法识别版本号：{value}")
    parts = tuple(int(part) for part in match.group(1).split("."))
    return parts + (0,) * (4 - len(parts))


def _request_bytes(url: str, timeout: int = 20) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "StoryCut-Studio-Updater",
            "Accept": "application/octet-stream",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_ARCHIVE_BYTES:
            raise UpdateError("更新包异常过大，已停止下载")
        data = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(data) > MAX_ARCHIVE_BYTES:
        raise UpdateError("更新包超过安全大小限制")
    return data


def check_for_update() -> tuple[dict[str, Any], dict[str, Any], bool]:
    local = read_version()
    repository = str(local["repository"]).strip(" /")
    url = (
        f"https://raw.githubusercontent.com/{repository}/main/"
        f"{ARCHIVE_APP_PREFIX}version.json?time={int(time.time())}"
    )
    try:
        remote = json.loads(_request_bytes(url).decode("utf-8"))
    except Exception as exc:
        raise UpdateError(f"无法连接 GitHub 检查 StoryCut 更新：{exc}") from exc
    if not isinstance(remote, dict) or str(remote.get("repository", "")).strip(" /") != repository:
        raise UpdateError("GitHub StoryCut 版本信息无效")
    newer = version_key(str(remote.get("version", ""))) > version_key(str(local["version"]))
    return local, remote, newer


def _safe_relative(value: str) -> Path:
    posix = PurePosixPath(value)
    if posix.is_absolute() or not posix.parts or any(part in {"", ".", ".."} for part in posix.parts):
        raise UpdateError(f"更新清单包含不安全路径：{value}")
    relative = Path(*posix.parts)
    if relative.name in PROTECTED_FILES or relative.parts[0] in PROTECTED_DIRS:
        raise UpdateError(f"更新清单试图修改用户内容：{value}")
    return relative


def _load_manifest_bytes(data: bytes) -> dict[str, Any]:
    try:
        manifest = json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise UpdateError(f"更新清单格式错误：{exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise UpdateError("更新清单缺少 files 列表")
    return manifest


def _zip_entries(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    files = [info for info in archive.infolist() if not info.is_dir()]
    if not files or "/" not in files[0].filename:
        raise UpdateError("GitHub 更新包目录结构无效")
    prefix = files[0].filename.split("/", 1)[0] + "/"
    return {info.filename[len(prefix):]: info for info in files if info.filename.startswith(prefix)}


def download_and_apply(remote: dict[str, Any], progress: Callable[[str], None] | None = None) -> Path:
    report = progress or (lambda _message: None)
    local = read_version()
    repository = str(local["repository"]).strip(" /")
    new_version = str(remote.get("version", "")).lstrip("v")
    if version_key(new_version) <= version_key(str(local["version"])):
        raise UpdateError("远程版本不高于本地版本")

    tag = f"storycut-v{new_version}"
    archive_url = f"https://github.com/{repository}/archive/refs/tags/{tag}.zip"
    report(f"正在下载 {tag}……")
    try:
        archive_data = _request_bytes(archive_url, timeout=60)
    except Exception as exc:
        raise UpdateError(f"下载 {tag} 失败：{exc}") from exc

    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_data))
    except zipfile.BadZipFile as exc:
        raise UpdateError("下载内容不是有效 ZIP") from exc

    with archive:
        entries = _zip_entries(archive)
        manifest_key = ARCHIVE_APP_PREFIX + "update_manifest.json"
        manifest_info = entries.get(manifest_key)
        if not manifest_info:
            raise UpdateError("新版缺少 StoryCut 更新清单")
        new_manifest = _load_manifest_bytes(archive.read(manifest_info))
        if str(new_manifest.get("version", "")).lstrip("v") != new_version:
            raise UpdateError("更新清单版本与远程版本不一致")

        new_paths = {_safe_relative(str(value)) for value in new_manifest["files"]}
        if Path("update_manifest.json") not in new_paths or Path("version.json") not in new_paths:
            raise UpdateError("更新清单必须管理版本文件和清单自身")
        missing = [
            path.as_posix()
            for path in new_paths
            if ARCHIVE_APP_PREFIX + path.as_posix() not in entries
        ]
        if missing:
            raise UpdateError("更新包缺少文件：" + ", ".join(missing[:8]))

        old_manifest = (
            _load_manifest_bytes(MANIFEST_FILE.read_bytes())
            if MANIFEST_FILE.exists()
            else {"files": []}
        )
        old_paths = {_safe_relative(str(value)) for value in old_manifest.get("files", [])}
        affected = old_paths | new_paths
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup = ROOT / "cache" / "update_backups" / f"v{local['version']}_{stamp}"
        backup.mkdir(parents=True, exist_ok=True)
        existed_before: set[Path] = set()

        report(f"正在备份 StoryCut v{local['version']}……")
        for relative in affected:
            target = ROOT / relative
            if target.is_file():
                existed_before.add(relative)
                destination = backup / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, destination)

        try:
            report("正在安装 StoryCut 新版本……")
            for relative in sorted(new_paths, key=lambda path: path.as_posix()):
                info = entries[ARCHIVE_APP_PREFIX + relative.as_posix()]
                if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    raise UpdateError(f"更新包包含不允许的符号链接：{relative}")
                target = ROOT / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + ".update_tmp")
                temporary.write_bytes(archive.read(info))
                os.replace(temporary, target)
            for relative in sorted(old_paths - new_paths, key=lambda path: path.as_posix(), reverse=True):
                target = ROOT / relative
                if target.is_file():
                    target.unlink()
        except Exception as exc:
            report("安装失败，正在恢复旧版本……")
            for relative in affected:
                temporary = (ROOT / relative).with_name((ROOT / relative).name + ".update_tmp")
                if temporary.is_file():
                    temporary.unlink()
            for relative in new_paths - existed_before:
                target = ROOT / relative
                if target.is_file():
                    target.unlink()
            for relative in existed_before:
                saved = backup / relative
                target = ROOT / relative
                if saved.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(saved, target)
            raise UpdateError(f"安装更新失败，已恢复旧版本：{exc}") from exc

    report(f"StoryCut v{new_version} 安装完成")
    return backup
