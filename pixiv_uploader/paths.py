from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
RESOURCE_ROOT = PACKAGE_DIR / "resources"
PIXIV_RESOURCE_DIR = RESOURCE_ROOT / "pixiv"
CIVITAI_RESOURCE_DIR = RESOURCE_ROOT / "civitai"
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
FRONTEND_SOURCE_DIR = FRONTEND_ROOT / "src"
FRONTEND_PUBLIC_DIR = FRONTEND_ROOT / "public"
FRONTEND_DIST_DIR = FRONTEND_ROOT / "dist"


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    manifests: Path
    progress: Path
    logs: Path
    temp: Path
    pixiv: Path
    pixiv_rule_fit: Path
    civitai: Path
    watermark: Path


def runtime_paths(project_root: Path = PROJECT_ROOT) -> RuntimePaths:
    root = project_root.resolve() / "runtime"
    pixiv = root / "pixiv"
    return RuntimePaths(
        root=root,
        manifests=root / "manifests",
        progress=root / "progress",
        logs=root / "logs",
        temp=root / "tmp",
        pixiv=pixiv,
        pixiv_rule_fit=pixiv / "rule_fit",
        civitai=root / "civitai",
        watermark=root / "watermark",
    )
