from __future__ import annotations

import copy
import gzip
import json
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from ..paths import CIVITAI_RESOURCE_DIR, PIXIV_RESOURCE_DIR
from ..runtime import ensure_runtime_layout

log = logging.getLogger("pixiv_uploader")
_SAFE_STEM_RE = re.compile(r"[^0-9A-Za-z\u3040-\u30ff\u3400-\u9fff._-]+")
_DEFAULT_VALIDATION_CASES = {"cases": []}


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                return json.load(stream)
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, gzip.BadGzipFile, UnicodeDecodeError, EOFError) as exc:
        log.warning("JSON 数据损坏，已使用默认值：%s (%s)", path, exc)
        return copy.deepcopy(default)


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def ensure_runtime_files(project_root: Path) -> dict[str, Path]:
    paths = ensure_runtime_layout(project_root)
    rule_fit_root = paths.pixiv_rule_fit
    files = {
        "aliases": paths.pixiv / "tag_aliases.json",
        "popularity": paths.pixiv / "tag_popularity.json",
        "validation": paths.pixiv / "validation_cases.json",
        "age_rules": paths.pixiv / "age_rules.json",
        "jp_aliases": paths.pixiv / "jp_aliases.json",
        "general_jp": paths.pixiv / "general_jp.json",
        "danbooru_jp": PIXIV_RESOURCE_DIR / "danbooru_jp.json.gz",
        "censor_config": paths.pixiv / "censor.json",
        "civitai_safety": paths.civitai / "safety.json",
        "manifests": paths.manifests,
        "rule_fit_root": rule_fit_root,
        "rule_fit_samples": rule_fit_root / "samples",
        "rule_fit_manifests": rule_fit_root / "manifests",
        "rule_fit_reports": rule_fit_root / "reports",
    }
    seeds = {
        "aliases": PIXIV_RESOURCE_DIR / "tag_aliases.json",
        "popularity": PIXIV_RESOURCE_DIR / "tag_popularity.json.gz",
        "age_rules": PIXIV_RESOURCE_DIR / "age_rules.json",
        "jp_aliases": PIXIV_RESOURCE_DIR / "jp_aliases.json",
        "general_jp": PIXIV_RESOURCE_DIR / "general_jp.json",
        "censor_config": PIXIV_RESOURCE_DIR / "censor.json",
        "civitai_safety": CIVITAI_RESOURCE_DIR / "safety.json",
    }
    for key, source in seeds.items():
        destination = files[key]
        if destination.exists():
            continue
        if not source.is_file():
            raise FileNotFoundError(f"missing bundled resource: {source}")
        payload = load_json(source, None)
        if payload is None:
            raise ValueError(f"invalid bundled resource: {source}")
        save_json(destination, payload)
    if not files["validation"].exists():
        save_json(files["validation"], _DEFAULT_VALIDATION_CASES)
    if not files["danbooru_jp"].is_file():
        raise FileNotFoundError(f"missing bundled resource: {files['danbooru_jp']}")
    return files


def safe_stem(name: str) -> str:
    return _SAFE_STEM_RE.sub("_", name).strip("._") or "image"


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def append_validation_case(path: Path, validation_path: Path, manifest: dict[str, Any]) -> None:
    payload = load_json(validation_path, _DEFAULT_VALIDATION_CASES)
    if not isinstance(payload, dict):
        payload = copy.deepcopy(_DEFAULT_VALIDATION_CASES)
    stored_cases = payload.get("cases")
    cases = stored_cases if isinstance(stored_cases, list) else []
    case = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_path": str(path),
        "domain": manifest.get("pixiv", {}).get("domain", ""),
        "raw_candidates": manifest.get("pixiv", {}).get("raw_candidates", []),
        "final_tags": manifest.get("pixiv", {}).get("final_tags", []),
        "title_ja": manifest.get("pixiv", {}).get("title_ja", ""),
        "title_zh": manifest.get("pixiv", {}).get("title_zh", ""),
    }
    identity = {key: value for key, value in case.items() if key != "created_at"}
    cases = [
        existing
        for existing in cases
        if isinstance(existing, dict)
        and {key: value for key, value in existing.items() if key != "created_at"} != identity
    ]
    cases.append(case)
    payload["cases"] = cases[-200:]
    save_json(validation_path, payload)


def create_manifest_path(manifest_dir: Path, source: Path) -> Path:
    return manifest_dir / f"{now_stamp()}_{safe_stem(source.stem)}.json"


def find_target_successes(manifest_dir: Path, source_path: Path) -> dict[str, str]:
    """Return the latest successful post URL for each target and source image."""
    if not manifest_dir.exists():
        return {}
    suffix = f"_{safe_stem(source_path.stem)}.json"
    source_value = str(source_path)
    latest: dict[str, tuple[str, str]] = {}
    for path in manifest_dir.iterdir():
        if not path.is_file() or not path.name.endswith(suffix):
            continue
        manifest = load_json(path, {})
        if not isinstance(manifest, dict) or manifest.get("source_path") != source_value:
            continue
        if manifest.get("dry_run"):
            continue
        for target, status in (manifest.get("status_by_target") or {}).items():
            # Uncertain submissions intentionally block automatic retries. They may
            # have reached Pixiv even when no artwork URL was returned.
            if status == "maybe_posted":
                current = latest.get(target)
                if current is None or path.name > current[0]:
                    latest[target] = (path.name, "")
                continue
            if status != "success":
                continue
            target_block = manifest.get(target) or {}
            url = target_block.get("post_url") if isinstance(target_block, dict) else ""
            if not url:
                continue
            current = latest.get(target)
            if current is None or path.name > current[0]:
                latest[target] = (path.name, url)
    return {target: url for target, (_, url) in latest.items()}


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    save_json(path, manifest)


def create_rule_fit_manifest_path(manifest_dir: Path, illust_id: str) -> Path:
    return manifest_dir / f"{illust_id}.json"


def create_rule_fit_compare_path(manifest_dir: Path, illust_id: str) -> Path:
    return manifest_dir / f"{illust_id}.compare.json"


def create_rule_fit_report_path(report_dir: Path, stem: str = "summary") -> Path:
    return report_dir / f"{now_stamp()}_{safe_stem(stem)}.json"
