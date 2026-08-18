from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Callable


APP_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = APP_ROOT.parent
VERSION_FILE = APP_ROOT / "version.json"
_SHARED_UPDATER = REPOSITORY_ROOT / "repository_updater.py"


def _load_shared_updater():
    spec = importlib.util.spec_from_file_location("storycut_repository_updater", _SHARED_UPDATER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载仓库更新器：{_SHARED_UPDATER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_updater = _load_shared_updater()
UpdateError = _updater.UpdateError
version_key = _updater.version_key


def read_version(path: Path = VERSION_FILE) -> dict[str, Any]:
    return _updater.read_version(path)


def check_for_update() -> tuple[dict[str, Any], dict[str, Any], bool]:
    return _updater.check_for_update("storycut_v2")


def download_and_apply(
    remote: dict[str, Any], progress: Callable[[str], None] | None = None
) -> Path:
    return _updater.download_and_apply("storycut_v2", remote, progress)
