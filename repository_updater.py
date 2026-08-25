from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable


REPOSITORY_ROOT = Path(__file__).resolve().parent
ROOT_MANIFEST = REPOSITORY_ROOT / "update_manifest.json"
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024

PROTECTED_NAMES = {
    ".env",
    "config.user.yaml",
    "config.yaml",
    "url+apikey.txt",
    "huggingface-token.txt",
}
PROTECTED_PARTS = {
    ".git",
    ".agents",
    ".idea",
    ".vscode",
    "venv",
    "models",
    "projects",
    "output",
    "export",
    "cache",
    "logs",
    "__pycache__",
    ".update_backups",
}
ALLOWED_ROOT_FILES = {
    ".env.example",
    ".gitignore",
    "CODEX_HANDOFF.md",
    "LICENSE",
    "MODEL_DOWNLOAD.md",
    "README.md",
    "repository_updater.py",
    "requirements.txt",
    "update_manifest.json",
    "version.json",
    "点我启动StoryCut（AI解说剪辑）.vbs",
}
ALLOWED_APP_DIRS = {"storycut_v2"}


class UpdateError(RuntimeError):
    pass


def read_version(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise UpdateError(f"无法读取版本文件 {path}：{exc}") from exc
    if not isinstance(value, dict) or not value.get("version") or not value.get("repository"):
        raise UpdateError(f"版本文件缺少 version 或 repository：{path}")
    return value


def version_key(value: str) -> tuple[int, ...]:
    match = re.fullmatch(r"v?(\d+(?:\.\d+){1,3})(?:[-+].*)?", value.strip())
    if not match:
        raise UpdateError(f"无法识别版本号：{value}")
    parts = tuple(int(part) for part in match.group(1).split("."))
    return parts + (0,) * (4 - len(parts))


def _system_proxies() -> dict[str, str]:
    """Return HTTP proxies from environment variables or Windows system settings."""
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


def _proxy_environment() -> dict[str, str]:
    proxies = _system_proxies()
    values: dict[str, str] = {}
    if proxies.get("http"):
        values["HTTP_PROXY"] = proxies["http"]
        values["http_proxy"] = proxies["http"]
    if proxies.get("https"):
        values["HTTPS_PROXY"] = proxies["https"]
        values["https_proxy"] = proxies["https"]
    return values


def _request_bytes(url: str, timeout: int = 30, accept: str = "application/octet-stream") -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "StoryCut-Repository-Updater",
            "Accept": accept,
            "Cache-Control": "no-cache",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(_system_proxies()))
    with opener.open(request, timeout=timeout) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_ARCHIVE_BYTES:
            raise UpdateError("更新包异常过大，已停止下载")
        data = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(data) > MAX_ARCHIVE_BYTES:
        raise UpdateError("更新包超过安全大小限制")
    return data


def check_for_update(app_relative_dir: str) -> tuple[dict[str, Any], dict[str, Any], bool]:
    app_dir = _validate_app_dir(app_relative_dir)
    local = read_version(REPOSITORY_ROOT / app_dir / "version.json")
    repository = str(local["repository"]).strip(" /")
    raw_url = (
        f"https://raw.githubusercontent.com/{repository}/main/"
        f"{app_dir}/version.json?time={int(time.time())}"
    )
    api_url = (
        f"https://api.github.com/repos/{repository}/contents/"
        f"{app_dir}/version.json?ref=main&time={int(time.time())}"
    )
    errors: list[str] = []
    remote: dict[str, Any] | None = None
    try:
        value = json.loads(_request_bytes(raw_url).decode("utf-8"))
        remote = value if isinstance(value, dict) else None
    except Exception as exc:
        errors.append(f"Raw：{exc}")
    if remote is None:
        try:
            wrapper = json.loads(
                _request_bytes(api_url, accept="application/vnd.github+json").decode("utf-8")
            )
            encoded = str(wrapper.get("content", "")).replace("\n", "")
            value = json.loads(base64.b64decode(encoded, validate=True).decode("utf-8"))
            remote = value if isinstance(value, dict) else None
        except Exception as exc:
            errors.append(f"API：{exc}")
    if remote is None:
        proxy_note = "（已尝试 Windows 系统代理）" if _system_proxies() else ""
        raise UpdateError(
            "无法连接 GitHub 检查更新" + proxy_note + "：" + "；".join(errors)
        )
    if not isinstance(remote, dict) or str(remote.get("repository", "")).strip(" /") != repository:
        raise UpdateError("GitHub 版本信息无效或仓库标识不匹配")
    newer = version_key(str(remote.get("version", ""))) > version_key(str(local["version"]))
    return local, remote, newer


def _validate_app_dir(value: str) -> str:
    cleaned = value.strip().strip("/")
    if cleaned not in ALLOWED_APP_DIRS:
        raise UpdateError(f"不允许的应用目录：{value}")
    return cleaned


def _safe_managed_path(value: str) -> Path:
    posix = PurePosixPath(value)
    if posix.is_absolute() or not posix.parts or any(part in {"", ".", ".."} for part in posix.parts):
        raise UpdateError(f"更新清单包含不安全路径：{value}")
    relative = Path(*posix.parts)
    lowered_parts = {part.lower() for part in relative.parts}
    if relative.name.lower() in {name.lower() for name in PROTECTED_NAMES}:
        raise UpdateError(f"更新清单试图修改用户文件：{value}")
    if lowered_parts & {part.lower() for part in PROTECTED_PARTS}:
        raise UpdateError(f"更新清单试图修改用户目录：{value}")
    first = relative.parts[0]
    if len(relative.parts) == 1:
        if relative.name not in ALLOWED_ROOT_FILES:
            raise UpdateError(f"更新清单包含未授权的根目录文件：{value}")
    elif first not in ALLOWED_APP_DIRS:
        raise UpdateError(f"更新清单包含未授权的目录：{value}")
    return relative


def _load_manifest_bytes(data: bytes) -> dict[str, Any]:
    try:
        manifest = json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise UpdateError(f"仓库更新清单格式错误：{exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise UpdateError("仓库更新清单缺少 files 列表")
    return manifest


def _zip_entries(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    files = [info for info in archive.infolist() if not info.is_dir()]
    if not files or "/" not in files[0].filename:
        raise UpdateError("GitHub 更新包目录结构无效")
    prefix = files[0].filename.split("/", 1)[0] + "/"
    return {info.filename[len(prefix):]: info for info in files if info.filename.startswith(prefix)}


def _read_old_paths() -> set[Path]:
    if not ROOT_MANIFEST.exists():
        return set()
    try:
        old = _load_manifest_bytes(ROOT_MANIFEST.read_bytes())
    except UpdateError:
        return set()
    paths: set[Path] = set()
    for value in old.get("files", []):
        try:
            paths.add(_safe_managed_path(str(value)))
        except UpdateError:
            continue
    return paths


def download_and_apply(
    app_relative_dir: str,
    remote: dict[str, Any],
    progress: Callable[[str], None] | None = None,
) -> Path:
    app_dir = _validate_app_dir(app_relative_dir)
    report = progress or (lambda _message: None)
    local = read_version(REPOSITORY_ROOT / app_dir / "version.json")
    repository = str(local["repository"]).strip(" /")
    requested_version = str(remote.get("version", "")).lstrip("v")
    if version_key(requested_version) <= version_key(str(local["version"])):
        raise UpdateError("远程版本不高于本地版本，无需更新")

    git_result = _try_git_fast_forward(
        app_dir,
        repository,
        requested_version,
        report,
    )
    if git_result is not None:
        return git_result

    if _system_proxies():
        report("已检测到系统代理，GitHub 请求将自动通过代理连接……")
    archive_urls = (
        f"https://github.com/{repository}/archive/refs/heads/main.zip",
        f"https://codeload.github.com/{repository}/zip/refs/heads/main",
    )
    report("正在下载 GitHub 最新仓库文件……")
    archive_data = b""
    errors: list[str] = []
    for archive_url in archive_urls:
        try:
            archive_data = _request_bytes(archive_url, timeout=90)
            break
        except Exception as exc:
            errors.append(str(exc))
    if not archive_data:
        proxy_note = "（已尝试 Windows 系统代理）" if _system_proxies() else ""
        raise UpdateError(
            f"下载 GitHub 主分支失败{proxy_note}：" + "；".join(errors)
        )
    return apply_repository_archive(app_dir, remote, archive_data, report)


def _run_git(git_executable: str, *arguments: str, timeout: int = 45) -> subprocess.CompletedProcess[str]:
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment.update(_proxy_environment())
    return subprocess.run(
        [git_executable, "-C", str(REPOSITORY_ROOT), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        creationflags=creation_flags,
        env=environment,
    )


def _try_git_fast_forward(
    app_relative_dir: str,
    repository: str,
    requested_version: str,
    report: Callable[[str], None],
) -> Path | None:
    git_directory = REPOSITORY_ROOT / ".git"
    git_executable = shutil.which("git")
    if not git_directory.exists() or not git_executable:
        report("未检测到 Git 克隆环境，改用内置 ZIP 更新……")
        return None

    try:
        remote_result = _run_git(git_executable, "remote", "get-url", "origin")
        remote_url = remote_result.stdout.strip()
        normalized_remote = remote_url.lower().replace("\\", "/").replace(":", "/")
        if remote_result.returncode != 0 or repository.lower() not in normalized_remote:
            report("Git origin 不是当前 StoryCut 仓库，改用内置 ZIP 更新……")
            return None

        branch_result = _run_git(git_executable, "branch", "--show-current")
        if branch_result.returncode != 0 or branch_result.stdout.strip() != "main":
            report("当前不在 Git main 分支，改用内置 ZIP 更新……")
            return None

        status_result = _run_git(
            git_executable,
            "status",
            "--porcelain",
            "--untracked-files=no",
        )
        if status_result.returncode != 0 or status_result.stdout.strip():
            report("Git 跟踪的程序文件存在本地修改，改用可备份回滚的 ZIP 更新……")
            return None

        report("已检测到 Git 克隆，正在后台执行 git pull --ff-only origin main……")
        pull_result = _run_git(
            git_executable,
            "pull",
            "--ff-only",
            "origin",
            "main",
            timeout=180,
        )
        if pull_result.returncode != 0:
            detail = (pull_result.stderr or pull_result.stdout).strip().splitlines()
            reason = detail[-1] if detail else "未知 Git 错误"
            report(f"Git 快进更新失败（{reason}），改用内置 ZIP 更新……")
            return None

        installed = read_version(REPOSITORY_ROOT / app_relative_dir / "version.json")
        if version_key(str(installed.get("version", ""))) < version_key(requested_version):
            report("Git 更新后的版本仍低于 GitHub 检测结果，改用内置 ZIP 更新……")
            return None
        report("Git 快进更新完成，未跟踪文件和忽略的用户数据均已保留")
        return git_directory
    except (OSError, subprocess.SubprocessError, UpdateError) as exc:
        report(f"Git 更新不可用（{exc}），改用内置 ZIP 更新……")
        return None


def apply_repository_archive(
    app_relative_dir: str,
    remote: dict[str, Any],
    archive_data: bytes,
    progress: Callable[[str], None] | None = None,
) -> Path:
    app_dir = _validate_app_dir(app_relative_dir)
    report = progress or (lambda _message: None)
    requested_version = str(remote.get("version", "")).lstrip("v")
    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_data))
    except zipfile.BadZipFile as exc:
        raise UpdateError("下载内容不是有效 ZIP") from exc

    with archive:
        entries = _zip_entries(archive)
        manifest_info = entries.get("update_manifest.json")
        if not manifest_info:
            raise UpdateError("新版缺少仓库根 update_manifest.json")
        manifest = _load_manifest_bytes(archive.read(manifest_info))
        new_paths = {_safe_managed_path(str(value)) for value in manifest["files"]}
        required = {
            Path("update_manifest.json"),
            Path("repository_updater.py"),
            Path(app_dir) / "version.json",
        }
        if not required.issubset(new_paths):
            raise UpdateError("仓库更新清单缺少必需文件")
        missing = [path.as_posix() for path in new_paths if path.as_posix() not in entries]
        if missing:
            raise UpdateError("更新包缺少文件：" + ", ".join(missing[:8]))
        try:
            archived_version = json.loads(
                archive.read(entries[f"{app_dir}/version.json"]).decode("utf-8")
            )
        except Exception as exc:
            raise UpdateError(f"无法验证更新包版本：{exc}") from exc
        if str(archived_version.get("version", "")).lstrip("v") != requested_version:
            raise UpdateError("GitHub 主分支在检查后已发生变化，请重新检查更新")

        old_paths = _read_old_paths()
        explicitly_removed: set[Path] = set()
        for value in manifest.get("remove", []):
            explicitly_removed.add(_safe_managed_path(str(value)))
        affected = old_paths | new_paths | explicitly_removed
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup = REPOSITORY_ROOT / ".update_backups" / f"{app_dir}_{stamp}"
        backup.mkdir(parents=True, exist_ok=True)
        existed_before: set[Path] = set()

        report("正在备份将要变更的程序文件……")
        for relative in affected:
            target = REPOSITORY_ROOT / relative
            if target.is_file():
                existed_before.add(relative)
                destination = backup / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, destination)

        try:
            report("正在同步 StoryCut 和根目录启动器……")
            for relative in sorted(new_paths, key=lambda path: path.as_posix()):
                info = entries[relative.as_posix()]
                if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    raise UpdateError(f"更新包包含不允许的符号链接：{relative}")
                target = REPOSITORY_ROOT / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + ".update_tmp")
                temporary.write_bytes(archive.read(info))
                os.replace(temporary, target)
            obsolete = (old_paths | explicitly_removed) - new_paths
            for relative in sorted(obsolete, key=lambda path: path.as_posix(), reverse=True):
                target = REPOSITORY_ROOT / relative
                if target.is_file():
                    target.unlink()
        except Exception as exc:
            report("安装失败，正在恢复旧程序文件……")
            for relative in affected:
                temporary = (REPOSITORY_ROOT / relative).with_name(
                    (REPOSITORY_ROOT / relative).name + ".update_tmp"
                )
                if temporary.is_file():
                    temporary.unlink()
            for relative in new_paths - existed_before:
                target = REPOSITORY_ROOT / relative
                if target.is_file():
                    target.unlink()
            for relative in existed_before:
                saved = backup / relative
                target = REPOSITORY_ROOT / relative
                if saved.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(saved, target)
            raise UpdateError(f"安装更新失败，已尝试回滚：{exc}") from exc

    report(f"仓库程序文件已同步到 v{requested_version}")
    return backup
