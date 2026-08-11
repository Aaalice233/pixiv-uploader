from __future__ import annotations

from copy import deepcopy
from typing import Any

# Platform-specific output schema and prompt scaffolding for LLM image-reverse.
#
# Adding a new platform = adding one entry here. Backend prompt template,
# output normalization, and frontend persona editor all read from this map.
# No platform-specific if/else should appear in business code; everything
# routes through PLATFORM_SPECS[persona["platform"]].

PLATFORM_SPECS: dict[str, dict[str, Any]] = {
    "pixiv": {
        "label": "Pixiv",
        "fields": [
            {
                "key": "title_ja", "label": "标题（日）", "label_key": "llm.field.titleJa",
                "kind": "text", "max": 60, "required": True, "consumer": "payload",
            },
            {
                "key": "title_zh", "label": "标题（中）", "label_key": "llm.field.titleZh",
                "kind": "text", "max": 60, "required": True, "consumer": "payload",
            },
            {
                "key": "caption_ja", "label": "简介（日）", "label_key": "llm.field.captionJa",
                "kind": "multiline", "max": 500, "required": True, "consumer": "payload",
            },
            {
                "key": "caption_zh", "label": "简介（中）", "label_key": "llm.field.captionZh",
                "kind": "multiline", "max": 500, "required": True, "consumer": "payload",
            },
        ],
        "extra_fields": [
            {
                "key": "keywords",
                "label": "视觉标签",
                "label_key": "llm.field.keywords",
                "kind": "tags",
                "min_count": 6,
                "max_count": 16,
                "max": 50,
                "required": True,
                "consumer": "tag_candidates",
                "forbidden_values": [
                    "original", "original art", "original illustration", "オリジナル",
                    "オリジナルイラスト", "ai art", "ai generated", "AIイラスト", "AI生成",
                ],
                "forbidden_prefixes": ["#", "http://", "https://", "www."],
                "instruction": (
                    "return 6-16 concise, established Pixiv/Danbooru-style visual tags; Japanese is preferred; "
                    "cover subjects, count, hair/eyes, clothing, pose, animals, setting, lighting, mood and style; "
                    "do not include hashtags, prose, オリジナル, オリジナルイラスト, AIイラスト or AI生成"
                ),
            },
        ],
        "prompt_intro": "Analyze the image and write Pixiv post copy.",
        "policy_notes": "Never identify real people. Avoid copyrighted character names unless visually obvious.",
    },
}

DEFAULT_PLATFORM_ID = "pixiv"
SUPPORTED_OUTPUT_CONSUMERS = frozenset({"payload", "tag_candidates"})


def validate_platform_specs() -> list[str]:
    errors: list[str] = []
    for platform_id, spec in PLATFORM_SPECS.items():
        seen: set[str] = set()
        for field in (spec.get("fields") or []) + (spec.get("extra_fields") or []):
            key = str(field.get("key") or "").strip()
            if not key:
                errors.append(f"{platform_id}: field key is required")
                continue
            if key in seen:
                errors.append(f"{platform_id}: duplicate field {key}")
            seen.add(key)
            if field.get("consumer") not in SUPPORTED_OUTPUT_CONSUMERS:
                errors.append(f"{platform_id}.{key}: unsupported output consumer")
            if not str(field.get("label_key") or "").strip():
                errors.append(f"{platform_id}.{key}: label_key is required")
            if field.get("kind") not in {"text", "multiline", "tags"}:
                errors.append(f"{platform_id}.{key}: unsupported field kind")
            if field.get("kind") == "tags" and field.get("required"):
                minimum = int(field.get("min_count", 0) or 0)
                maximum = int(field.get("max_count", 0) or 0)
                if minimum < 1 or maximum < minimum:
                    errors.append(f"{platform_id}.{key}: invalid required tag count")
    return errors


_PLATFORM_SPEC_ERRORS = validate_platform_specs()
if _PLATFORM_SPEC_ERRORS:
    raise RuntimeError("invalid LLM platform schema: " + "; ".join(_PLATFORM_SPEC_ERRORS))


def list_platform_ids() -> list[str]:
    return list(PLATFORM_SPECS.keys())


def get_platform_spec(platform_id: str) -> dict[str, Any]:
    return deepcopy(PLATFORM_SPECS.get(platform_id) or PLATFORM_SPECS[DEFAULT_PLATFORM_ID])


def normalize_platform_id(value: Any) -> str:
    pid = str(value or "").strip().lower()
    return pid if pid in PLATFORM_SPECS else DEFAULT_PLATFORM_ID


def normalize_platform_ids(value: Any) -> list[str]:
    """Normalize platform field to a deduplicated list of valid platform IDs.

    Accepts a single string (legacy) or a list. Invalid IDs fall back to
    DEFAULT_PLATFORM_ID. Always returns at least one element.
    """
    raw = value if isinstance(value, list) else [value]
    seen: set[str] = set()
    result: list[str] = []
    for v in raw:
        pid = str(v or "").strip().lower()
        pid = pid if pid in PLATFORM_SPECS else DEFAULT_PLATFORM_ID
        if pid not in seen:
            seen.add(pid)
            result.append(pid)
    return result or [DEFAULT_PLATFORM_ID]


def get_merged_spec(platform_ids: list[str]) -> dict[str, Any]:
    """Merge platform specs for multiple platforms into one combined spec.

    Fields and extra_fields are deduplicated by key in order of appearance.
    For a single platform, equivalent to get_platform_spec.
    """
    if not platform_ids:
        return get_platform_spec(DEFAULT_PLATFORM_ID)
    if len(platform_ids) == 1:
        return get_platform_spec(platform_ids[0])
    merged_fields: list[dict] = []
    merged_extra: list[dict] = []
    seen_keys: set[str] = set()
    intros: list[str] = []
    policy_notes: list[str] = []
    labels: list[str] = []
    for pid in platform_ids:
        spec = PLATFORM_SPECS.get(pid) or PLATFORM_SPECS[DEFAULT_PLATFORM_ID]
        labels.append(str(spec.get("label", pid)))
        intro = spec.get("prompt_intro", "")
        if intro:
            intros.append(intro)
        note = spec.get("policy_notes", "")
        if note:
            policy_notes.append(note)
        for f in (spec.get("fields") or []):
            key = str(f.get("key") or "")
            if key and key not in seen_keys:
                seen_keys.add(key)
                merged_fields.append(deepcopy(f))
        for f in (spec.get("extra_fields") or []):
            key = str(f.get("key") or "")
            if key and key not in seen_keys:
                seen_keys.add(key)
                merged_extra.append(deepcopy(f))
    return {
        "label": " / ".join(labels),
        "fields": merged_fields,
        "extra_fields": merged_extra,
        "prompt_intro": " ".join(intros),
        "policy_notes": " ".join(p for p in policy_notes if p),
    }


def all_field_keys(platform_id: str) -> list[str]:
    spec = PLATFORM_SPECS.get(platform_id) or {}
    keys: list[str] = []
    for field in (spec.get("fields") or []) + (spec.get("extra_fields") or []):
        key = str(field.get("key") or "").strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def required_field_keys(platform_id: str) -> list[str]:
    spec = PLATFORM_SPECS.get(platform_id) or {}
    required: list[str] = []
    for field in spec.get("fields") or []:
        key = str(field.get("key") or "").strip()
        if key and field.get("required", True):
            required.append(key)
    for field in spec.get("extra_fields") or []:
        key = str(field.get("key") or "").strip()
        if key and field.get("required", False):
            required.append(key)
    return required


def field_specs_for_consumer(platform_id: str, consumer: str) -> list[dict[str, Any]]:
    spec = PLATFORM_SPECS.get(platform_id) or PLATFORM_SPECS[DEFAULT_PLATFORM_ID]
    return [
        deepcopy(field)
        for field in (spec.get("fields") or []) + (spec.get("extra_fields") or [])
        if field.get("consumer") == consumer
    ]
