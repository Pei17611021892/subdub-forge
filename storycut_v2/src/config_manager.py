from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError(f"配置文件顶层必须是对象：{path}")
    return value


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def load_config(root: Path) -> dict[str, Any]:
    return _merge(
        _read_yaml(root / "config.default.yaml"),
        _read_yaml(root / "config.user.yaml"),
    )


def update_user_config(root: Path, values: dict[str, Any]) -> None:
    """Merge user-facing settings without overwriting unrelated local options."""
    path = root / "config.user.yaml"
    updated = _merge(_read_yaml(path), values)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".update_tmp")
    temporary.write_text(
        yaml.safe_dump(updated, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    temporary.replace(path)
