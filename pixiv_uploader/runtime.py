from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path

from .paths import PROJECT_ROOT, RuntimePaths, runtime_paths

log = logging.getLogger("pixiv_uploader")
_MIGRATION_LOCK = threading.Lock()


def _merge_directory(source: Path, destination: Path) -> list[str]:
    moved: list[str] = []
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir() and target.is_dir():
            moved.extend(_merge_directory(item, target))
            continue
        if target.exists():
            log.warning("保留冲突的旧运行文件，未覆盖：%s", item)
            continue
        shutil.move(str(item), str(target))
        moved.append(str(item))
    try:
        source.rmdir()
    except OSError:
        pass
    return moved


def _move_legacy_path(source: Path, destination: Path) -> list[str]:
    if not source.exists():
        return []
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if source.is_dir() and destination.is_dir():
            return _merge_directory(source, destination)
        log.warning("保留冲突的旧运行文件，未覆盖：%s", source)
        return []
    shutil.move(str(source), str(destination))
    return [str(source)]


def migrate_legacy_runtime(project_root: Path = PROJECT_ROOT) -> list[str]:
    """Move legacy root-level runtime state without overwriting newer data."""
    root = project_root.resolve()
    paths = runtime_paths(root)
    migrations = (
        (root / "manifests", paths.manifests),
        (root / "progress", paths.progress),
        (root / "logs", paths.logs),
        (root / ".tmp", paths.temp),
        (root / "pixiv" / "logs", paths.logs),
        (root / "pixiv" / "rule_fit", paths.pixiv_rule_fit),
        (root / "watermark.json", paths.watermark / "config.json"),
        (root / "watermark_fonts", paths.watermark / "fonts"),
        (root / "watermark_images", paths.watermark / "images"),
        (root / "civitai_safety.json", paths.civitai / "safety.json"),
        (root / "pixiv_censor.json", paths.pixiv / "censor.json"),
        (root / "pixiv_age_rules.json", paths.pixiv / "age_rules.json"),
        (root / "pixiv_general_jp.json", paths.pixiv / "general_jp.json"),
        (root / "pixiv_jp_aliases.json", paths.pixiv / "jp_aliases.json"),
        (root / "pixiv_tag_aliases.json", paths.pixiv / "tag_aliases.json"),
        (root / "pixiv_tag_popularity.json", paths.pixiv / "tag_popularity.json"),
        (root / "pixiv_validation_cases.json", paths.pixiv / "validation_cases.json"),
        (root / "pixiv" / "censor.json", paths.pixiv / "censor.json"),
        (root / "pixiv" / "age_rules.json", paths.pixiv / "age_rules.json"),
        (root / "pixiv" / "general_jp.json", paths.pixiv / "general_jp.json"),
        (root / "pixiv" / "jp_aliases.json", paths.pixiv / "jp_aliases.json"),
        (root / "pixiv" / "tag_aliases.json", paths.pixiv / "tag_aliases.json"),
        (root / "pixiv" / "tag_popularity.json", paths.pixiv / "tag_popularity.json"),
        (root / "pixiv" / "validation_cases.json", paths.pixiv / "validation_cases.json"),
    )
    moved: list[str] = []
    with _MIGRATION_LOCK:
        paths.root.mkdir(parents=True, exist_ok=True)
        for source, destination in migrations:
            moved.extend(_move_legacy_path(source, destination))
        legacy_pixiv_dir = root / "pixiv"
        if legacy_pixiv_dir.is_dir() and not any(legacy_pixiv_dir.iterdir()):
            legacy_pixiv_dir.rmdir()
    if moved:
        log.info("已迁移 %d 个旧运行路径到 runtime/", len(moved))
    return moved


def ensure_runtime_layout(project_root: Path = PROJECT_ROOT) -> RuntimePaths:
    paths = runtime_paths(project_root)
    migrate_legacy_runtime(project_root)
    for directory in (
        paths.root,
        paths.manifests,
        paths.progress,
        paths.logs,
        paths.temp,
        paths.pixiv,
        paths.pixiv_rule_fit,
        paths.pixiv_rule_fit / "samples",
        paths.pixiv_rule_fit / "manifests",
        paths.pixiv_rule_fit / "reports",
        paths.civitai,
        paths.watermark,
        paths.watermark / "fonts",
        paths.watermark / "images",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return paths
