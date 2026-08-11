from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from ..paths import PROJECT_ROOT
from .storage import load_json, save_json


def haintag_settings_path() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "HainTag" / "settings.json"


def load_haintag_settings(path: Path | None = None) -> dict[str, Any]:
    payload = load_json(path or haintag_settings_path(), {})
    if not isinstance(payload, dict):
        return {}
    settings = payload.get("settings", payload)
    return dict(settings) if isinstance(settings, dict) else {}


def save_haintag_settings(updates: dict[str, Any], path: Path | None = None) -> None:
    settings_path = path or haintag_settings_path()
    payload = load_json(settings_path, {})
    if not isinstance(payload, dict):
        payload = {}
    if "settings" in payload:
        if not isinstance(payload["settings"], dict):
            payload["settings"] = {}
        payload["settings"].update(updates)
    else:
        payload.update(updates)
    save_json(settings_path, payload)


def scan_cl_model_dir(path: str | Path | None) -> tuple[Path | None, Path | None]:
    directory = _expand_path(path)
    if directory is None or not directory.is_dir():
        return None, None
    model_path: Path | None = None
    mapping_path: Path | None = None
    for item in sorted(directory.iterdir(), key=lambda candidate: candidate.name.lower()):
        if not item.is_file():
            continue
        name = item.name.lower()
        if name.endswith(".onnx") and model_path is None:
            model_path = item
        elif mapping_path is None and (
            name.endswith(".json") and any(token in name for token in ("tag", "mapping", "label"))
            or name.endswith(".csv") and any(token in name for token in ("tag", "label"))
        ):
            mapping_path = item
    return model_path, mapping_path


def pixai_model_ready(path: str | Path | None) -> bool:
    directory = _expand_path(path)
    return bool(
        directory
        and directory.is_dir()
        and (directory / "model.onnx").is_file()
        and (directory / "selected_tags.csv").is_file()
    )


def resolve_cl_model_dir(
    settings: dict[str, Any] | None = None,
    project_root: Path = PROJECT_ROOT,
) -> Path | None:
    values = settings if settings is not None else load_haintag_settings()
    return _resolve_model_dir(
        values.get("tagger_model_dir"),
        "cl_tagger",
        lambda candidate: all(scan_cl_model_dir(candidate)),
        project_root,
    )


def resolve_pixai_model_dir(
    settings: dict[str, Any] | None = None,
    project_root: Path = PROJECT_ROOT,
) -> Path | None:
    values = settings if settings is not None else load_haintag_settings()
    return _resolve_model_dir(
        values.get("pixai_tagger_model_dir"),
        "pixai_tagger",
        pixai_model_ready,
        project_root,
    )


def resolve_tagger_python(settings: dict[str, Any] | None = None) -> str | None:
    values = settings if settings is not None else load_haintag_settings()
    path = _expand_path(values.get("tagger_python_path"))
    return str(path) if path and path.is_file() else None


def _resolve_model_dir(
    configured: Any,
    default_name: str,
    is_ready: Callable[[Path], bool],
    project_root: Path,
) -> Path | None:
    candidates: list[Path] = []
    configured_path = _expand_path(configured, project_root)
    if configured_path is not None:
        candidates.append(configured_path)
    candidates.append(project_root / "models" / default_name)
    candidates.append(haintag_settings_path().parent / "models" / default_name)

    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate.resolve(strict=False)))
        if key in seen:
            continue
        seen.add(key)
        if is_ready(candidate):
            return candidate.resolve()
    return None


def _expand_path(value: Any, base: Path = PROJECT_ROOT) -> Path | None:
    text = os.path.expandvars(str(value or "").strip())
    if not text:
        return None
    path = Path(text).expanduser()
    return path if path.is_absolute() else base / path
