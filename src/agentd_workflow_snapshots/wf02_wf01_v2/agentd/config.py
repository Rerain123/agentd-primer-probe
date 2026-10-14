"""Versioned configuration loading."""

from __future__ import annotations

import json
import os
import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any

from .errors import InputValidationError


def _discover_project_root() -> Path:
    configured = os.environ.get("AGENTD_PROJECT_ROOT")
    candidates = [
        Path(configured).expanduser() if configured else None,
        Path.cwd(),
        Path(__file__).resolve().parents[2],
    ]
    for candidate in candidates:
        if candidate is not None and (candidate / "config" / "defaults.json").is_file():
            return candidate.resolve()
    return Path(__file__).resolve().parents[2]


PROJECT_ROOT = _discover_project_root()
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "defaults.json"


def _deep_merge(base: dict, override: dict) -> dict:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _read_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise InputValidationError(f"无法读取配置文件: {path}", details={"error": str(exc)}) from exc


def load_config(path: str | Path | None = None, *, overrides: dict | None = None) -> dict[str, Any]:
    config = _read_json(DEFAULT_CONFIG_PATH)
    selected = path or os.environ.get("AGENTD_CONFIG")
    if selected:
        config = _deep_merge(config, _read_json(Path(selected)))
    if overrides:
        config = _deep_merge(config, overrides)
    return config


def config_fingerprint(config: dict[str, Any]) -> str:
    """Return a stable digest for scientific-run provenance."""
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
