from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config.default.yaml"
USER_CONFIG = ROOT / "config.user.yaml"
LEGACY_CONFIG = ROOT / "config.yaml"
LEGACY_REPOSITORY_USER_CONFIG = ROOT.parent / "config.user.yaml"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle) or {}
    if not isinstance(value, dict):
        raise ValueError(f"配置文件顶层必须是对象：{path}")
    return value


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _deep_diff(current: dict[str, Any], default: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in current.items():
        if key not in default:
            result[key] = deepcopy(value)
        elif isinstance(value, dict) and isinstance(default[key], dict):
            nested = _deep_diff(value, default[key])
            if nested:
                result[key] = nested
        elif value != default[key]:
            result[key] = deepcopy(value)
    return result


def load_config() -> dict[str, Any]:
    if not DEFAULT_CONFIG.exists():
        raise FileNotFoundError(f"找不到默认配置：{DEFAULT_CONFIG}")
    defaults = _read_yaml(DEFAULT_CONFIG)
    # Compatibility for an older checkout. New releases use config.user.yaml.
    if USER_CONFIG.exists():
        user_path = USER_CONFIG
    elif LEGACY_CONFIG.exists():
        user_path = LEGACY_CONFIG
    else:
        # One-release compatibility for checkouts created before the application
        # moved into translator_studio/.
        user_path = LEGACY_REPOSITORY_USER_CONFIG
    return deep_merge(defaults, _read_yaml(user_path))


def save_config(config: dict[str, Any]) -> None:
    defaults = _read_yaml(DEFAULT_CONFIG)
    overrides = _deep_diff(config, defaults)
    temp = USER_CONFIG.with_suffix(".yaml.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(overrides, handle, allow_unicode=True, sort_keys=False, width=120)
    temp.replace(USER_CONFIG)
