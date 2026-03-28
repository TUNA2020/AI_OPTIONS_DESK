from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import yaml


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        loaded = yaml.safe_load(file)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise TypeError(f"Settings file must contain a YAML mapping/object: {path}")
    return loaded


def _merge_settings(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in override.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(value, dict):
            merged[key] = _merge_settings(base_value, value)
        else:
            merged[key] = value
    return merged


def load_settings(path: str | Path = "config/settings.yaml") -> dict[str, Any]:
    settings_path = Path(path)
    default_path = settings_path.with_name("config.sample.yaml")
    default_settings = _read_yaml_mapping(default_path)
    user_settings = _read_yaml_mapping(settings_path)
    return _merge_settings(default_settings, user_settings)


def _normalize_for_yaml(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if key_text.startswith("__"):
                continue
            out[key_text] = _normalize_for_yaml(item)
        return out
    if isinstance(value, list):
        return [_normalize_for_yaml(item) for item in value]
    return value


def save_settings(settings: dict[str, Any], path: str | Path = "config/settings.yaml") -> None:
    if not isinstance(settings, dict):
        raise TypeError("settings must be a dict")
    settings_path = Path(path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _normalize_for_yaml(settings)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=settings_path.parent,
            prefix=f"{settings_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            yaml.safe_dump(payload, file, sort_keys=False, allow_unicode=False)
            file.flush()
            os.fsync(file.fileno())
            temp_path = Path(file.name)
        temp_path.replace(settings_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)
