from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / ".github" / "release-config.json"
VERSION_PATH = ROOT / "pixiv_uploader" / "version.py"
CHANGELOG_PATH = ROOT / "CHANGELOG.md"
VERSION_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)"
    r"(?:\.(?P<patch>0|[1-9]\d*))?"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?$"
)
CONVENTIONAL_RE = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?:\s*(?P<title>.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedVersion:
    raw: str
    major: int
    minor: int
    patch: int
    prerelease: str | None

    @property
    def sort_key(self) -> tuple[Any, ...]:
        prerelease_key: tuple[Any, ...]
        if self.prerelease is None:
            prerelease_key = (1, ())
        else:
            parts = tuple(
                (0, int(part)) if part.isdigit() else (1, part.casefold())
                for part in self.prerelease.split(".")
            )
            prerelease_key = (0, parts)
        return self.major, self.minor, self.patch, prerelease_key


@dataclass(frozen=True)
class ReleaseCommit:
    commit_type: str
    title: str
    breaking: bool


def _fail(message: str) -> None:
    raise SystemExit(message)


def _run_git(*args: str, check: bool = True, strip: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if check and result.returncode != 0:
        _fail(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip() if strip else result.stdout


def _load_config() -> dict[str, Any]:
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"无法读取发布配置 {CONFIG_PATH}: {exc}")
    required = ("product_name", "artifact_slug", "initial_base", "repository")
    missing = [key for key in required if not str(config.get(key) or "").strip()]
    if missing:
        _fail(f"发布配置缺少字段: {', '.join(missing)}")
    maximum = int(config.get("max_items_per_section", 6))
    if maximum < 1 or maximum > 12:
        _fail("max_items_per_section 必须在 1 到 12 之间")
    config["max_items_per_section"] = maximum
    return config


def _parse_version(value: str) -> ParsedVersion:
    normalized = str(value or "").strip().removeprefix("v")
    match = VERSION_RE.fullmatch(normalized)
    if match is None:
        _fail(f"版本号格式无效: {value!r}；应类似 1.0、1.2.3 或 1.2.3-beta.1")
    return ParsedVersion(
        raw=normalized,
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch") or 0),
        prerelease=match.group("prerelease"),
    )


def _source_version() -> str:
    source = VERSION_PATH.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$', source, re.MULTILINE)
    if match is None:
        _fail(f"无法从 {VERSION_PATH} 读取 __version__")
    return match.group(1).strip()


def _verify_commit(ref: str) -> None:
    _run_git("rev-parse", "--verify", f"{ref}^{{commit}}")


def _is_ancestor(ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=ROOT,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def validate_release(version: str, to_ref: str) -> ParsedVersion:
    parsed = _parse_version(version)
    _verify_commit(to_ref)
    source_version = _source_version()
    if source_version != parsed.raw:
        _fail(f"版本不一致: workflow={parsed.raw}，pixiv_uploader/version.py={source_version}")
    changelog = CHANGELOG_PATH.read_text(encoding="utf-8")
    heading = re.compile(
        rf"^##\s+🚀\s+{re.escape(parsed.raw)}\s+-\s+\d{{4}}-\d{{2}}-\d{{2}}\s*$",
        re.MULTILINE,
    )
    if heading.search(changelog) is None:
        _fail(f"CHANGELOG.md 缺少版本标题: ## 🚀 {parsed.raw} - YYYY-MM-DD")
    return parsed


def _previous_release_ref(
    version: ParsedVersion,
    to_ref: str,
    config: dict[str, Any],
    explicit_from_ref: str,
) -> tuple[str, str | None]:
    if explicit_from_ref:
        _verify_commit(explicit_from_ref)
        if not _is_ancestor(explicit_from_ref, to_ref):
            _fail(f"指定的起点 {explicit_from_ref} 不是 {to_ref} 的祖先")
        return explicit_from_ref, explicit_from_ref if explicit_from_ref.startswith("v") else None

    candidates: list[tuple[tuple[Any, ...], str]] = []
    for tag in _run_git("tag", "--merged", to_ref, "--list", "v*").splitlines():
        tag = tag.strip()
        if not tag or tag == f"v{version.raw}":
            continue
        match = VERSION_RE.fullmatch(tag.removeprefix("v"))
        if match is None:
            continue
        parsed = _parse_version(tag)
        if parsed.sort_key < version.sort_key:
            candidates.append((parsed.sort_key, tag))
    if candidates:
        previous_tag = max(candidates, key=lambda item: item[0])[1]
        return previous_tag, previous_tag

    initial_base = str(config["initial_base"])
    _verify_commit(initial_base)
    if not _is_ancestor(initial_base, to_ref):
        _fail(f"首次发布起点 {initial_base} 不是 {to_ref} 的祖先")
    return initial_base, None


def _read_release_commits(from_ref: str, to_ref: str) -> list[ReleaseCommit]:
    raw = _run_git(
        "log",
        "--first-parent",
        "--no-merges",
        "--format=%H%x1f%s%x1f%b%x1e",
        f"{from_ref}..{to_ref}",
        strip=False,
    )
    commits: list[ReleaseCommit] = []
    for record in raw.split("\x1e"):
        # \x1f is the field delimiter and Python treats it as whitespace. Only
        # trim Git's record newlines so commits with an empty body keep all fields.
        record = record.strip("\r\n")
        if not record:
            continue
        parts = record.split("\x1f", 2)
        if len(parts) != 3:
            continue
        _, subject, body = parts
        match = CONVENTIONAL_RE.fullmatch(subject.strip())
        if match is None:
            continue
        title = match.group("title").strip()
        if not title or "[skip release notes]" in title.casefold():
            continue
        breaking = bool(match.group("breaking")) or bool(
            re.search(r"^BREAKING[ -]CHANGE\s*:", body, re.IGNORECASE | re.MULTILINE)
        )
        commits.append(
            ReleaseCommit(
                commit_type=match.group("type").casefold(),
                title=title.rstrip("。."),
                breaking=breaking,
            )
        )
    return commits


def _categorized_items(commits: list[ReleaseCommit]) -> list[tuple[str, list[str]]]:
    categories: list[tuple[str, str]] = [
        ("breaking", "⚠️ 重要变化"),
        ("feat", "✨ 新功能"),
        ("fix", "🐛 问题修复"),
        ("perf", "⚡ 体验优化"),
    ]
    collected = {key: [] for key, _ in categories}
    seen: set[str] = set()
    for commit in commits:
        category = "breaking" if commit.breaking else commit.commit_type
        if category not in collected:
            continue
        dedupe_key = commit.title.casefold()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        collected[category].append(commit.title)
    return [(heading, collected[key]) for key, heading in categories if collected[key]]


def build_release_notes(
    version: str,
    to_ref: str,
    explicit_from_ref: str = "",
) -> tuple[str, str, str | None]:
    config = _load_config()
    parsed = validate_release(version, to_ref)
    from_ref, previous_tag = _previous_release_ref(parsed, to_ref, config, explicit_from_ref)
    commits = _read_release_commits(from_ref, to_ref)
    categorized = _categorized_items(commits)
    product = str(config["product_name"])
    artifact = f"{config['artifact_slug']}-v{parsed.raw}.zip"
    maximum = int(config["max_items_per_section"])

    lines = [f"# 🚀 {product} v{parsed.raw}", ""]
    if previous_tag is None:
        lines.extend([f"{product} 的首个正式版本。", ""])
    if categorized:
        for heading, items in categorized:
            lines.extend([f"## {heading}", ""])
            for item in items[:maximum]:
                lines.append(f"- {item}")
            overflow = len(items) - maximum
            if overflow > 0:
                lines.append(f"- 另有 {overflow} 项同类改进。")
            lines.append("")
    else:
        lines.extend(["## 🔧 维护更新", "", "- 本版本主要包含稳定性与兼容性改进。", ""])

    lines.extend(
        [
            "## 📦 下载与升级",
            "",
            f"- 下载 `{artifact}` 并解压，按照 README 完成安装或升级。",
            "- 本机的 `config.json`、运行记录和模型不会打进发布包；升级前仍建议自行备份。",
            "- 可使用同名 `.sha256` 文件校验下载完整性。",
        ]
    )
    if previous_tag:
        repository = str(config["repository"])
        lines.extend(
            [
                "",
                "## 🔗 完整变更",
                "",
                f"- [查看 v{previous_tag.removeprefix('v')}...v{parsed.raw} 的提交差异]"
                f"(https://github.com/{repository}/compare/{previous_tag}...v{parsed.raw})",
            ]
        )
    lines.append("")
    return "\n".join(lines), from_ref, previous_tag


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pixiv Uploader release tooling")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate version and changelog")
    validate.add_argument("--version", required=True)
    validate.add_argument("--to-ref", default="HEAD")

    notes = subparsers.add_parser("notes", help="generate concise user-facing release notes")
    notes.add_argument("--version", required=True)
    notes.add_argument("--to-ref", default="HEAD")
    notes.add_argument("--from-ref", default="")
    notes.add_argument("--output", required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if args.command == "validate":
        parsed = validate_release(args.version, args.to_ref)
        print(f"release metadata valid: v{parsed.raw}")
        return 0

    notes, from_ref, previous_tag = build_release_notes(
        version=args.version,
        to_ref=args.to_ref,
        explicit_from_ref=args.from_ref,
    )
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(notes, encoding="utf-8", newline="\n")
    print(f"release notes written: {output_path}")
    print(f"release range: {previous_tag or from_ref}..{args.to_ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
