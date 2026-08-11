from __future__ import annotations

import base64
import io
import json
import logging
import random
import re
import time
from copy import deepcopy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

import httpx
from PIL import Image, ImageOps

from .llm_platforms import (
    DEFAULT_PLATFORM_ID,
    PLATFORM_SPECS,
    all_field_keys,
    empty_sample_fields,
    get_merged_spec,
    list_platform_ids,
    normalize_platform_ids,
    required_field_keys,
)

POLITICAL_RE = re.compile(
    r"(政治|国家政治|政府|政党|意识形态|领土争端|主权争议|外交|革命运动|选举|民主党|共和党|共产党|"
    r"politic|ideology|territor.*disput|sovereign.*disput|diploma|election|"
    r"democrat|republican|communist|紛争|政府|政党)",
    re.IGNORECASE,
)

MAX_FEW_SHOT_SAMPLES = 4
LLM_IMAGE_MAX_EDGE = 1536
LLM_IMAGE_JPEG_QUALITY = 85
LLM_IMAGE_RETRY_PROFILES = ((1536, 85), (1280, 80), (1024, 75))
RETRYABLE_HTTP_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}
MAX_RETRY_HISTORY = 24
_SECRET_HEADER_RE = re.compile(
    r"(?i)\b(authorization|x-api-key|x-goog-api-key)\b\s*[:=]\s*(?:bearer\s+)?[^,;\s]+"
)
_SECRET_QUERY_RE = re.compile(r"(?i)([?&](?:api[_-]?key|key|token|access_token)=)[^&#\s]+")
_URL_USERINFO_RE = re.compile(r"(?i)(https?://)[^/@\s]+@")
log = logging.getLogger(__name__)


class _AttemptFailure(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        failover_allowed: bool = False,
        response_invalid: bool = False,
        adapt_image: bool = False,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.failover_allowed = failover_allowed
        self.response_invalid = response_invalid
        self.adapt_image = adapt_image
        self.status_code = status_code
        self.retry_after = retry_after


def default_llm_reverse_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "provider": "openai_compatible",
        "base_url": "",
        "api_key": "",
        "model": "",
        "timeout_seconds": 45,
        "retry_policy": {
            "request_attempts": 3,
            "repair_attempts": 1,
            "base_delay_seconds": 0.8,
            "max_delay_seconds": 10.0,
            "total_timeout_seconds": 180,
            "adaptive_image": True,
            "fallback_models": [],
        },
        "default_persona_id": "pixiv_soft",
        "default_content_mode": "sfw",
        "personas": [
            {
                "id": "pixiv_soft",
                "label": "Pixiv 软系",
                "platform": "pixiv",
                "default_content_mode": "sfw",
                "voice": "短诗体标题，轻描淡写的简介。语气克制，避免感叹号堆叠。",
                "sfw_prompt": "Write clean Pixiv-friendly copy for this illustration.",
                "nsfw_prompt": "Write direct adult-oriented Pixiv copy for this illustration when the platform allows it.",
                "extra_prompt": "Do not discuss politics, countries, governments, parties, ideology, war, territorial disputes, or real-world national issues.",
                "avoid": ["politics", "national politics", "state or government commentary"],
                "samples": [],
            }
        ],
    }


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return default


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in {float("inf"), float("-inf")}:
        return default
    return max(minimum, min(maximum, number))


def _normalize_retry_policy(value: Any) -> dict[str, Any]:
    defaults = default_llm_reverse_config()["retry_policy"]
    raw = value if isinstance(value, dict) else {}
    base_delay = _bounded_float(raw.get("base_delay_seconds"), defaults["base_delay_seconds"], 0.1, 30.0)
    max_delay = _bounded_float(raw.get("max_delay_seconds"), defaults["max_delay_seconds"], base_delay, 120.0)
    fallback_raw = raw.get("fallback_models") or []
    if isinstance(fallback_raw, str):
        fallback_raw = re.split(r"[,\r\n]+", fallback_raw)
    fallback_models: list[str] = []
    if isinstance(fallback_raw, (list, tuple)):
        for item in fallback_raw:
            model = str(item or "").strip()
            if model and model not in fallback_models:
                fallback_models.append(model)
            if len(fallback_models) >= 4:
                break
    return {
        "request_attempts": _bounded_int(raw.get("request_attempts"), defaults["request_attempts"], 1, 6),
        "repair_attempts": _bounded_int(raw.get("repair_attempts"), defaults["repair_attempts"], 0, 3),
        "base_delay_seconds": base_delay,
        "max_delay_seconds": max_delay,
        "total_timeout_seconds": _bounded_float(
            raw.get("total_timeout_seconds"), defaults["total_timeout_seconds"], 15.0, 900.0
        ),
        "adaptive_image": _coerce_bool(raw.get("adaptive_image"), defaults["adaptive_image"]),
        "fallback_models": fallback_models,
    }


def normalize_llm_reverse_config(config: dict[str, Any] | None) -> dict[str, Any]:
    normalized = default_llm_reverse_config()
    if isinstance(config, dict):
        for key in (
            "enabled", "provider", "base_url", "api_key", "model",
            "timeout_seconds", "default_persona_id", "default_content_mode",
        ):
            if key in config:
                normalized[key] = config[key]

        requested_personas = config.get("personas")
        if isinstance(requested_personas, list):
            valid_personas = [
                _normalize_persona(persona)
                for persona in requested_personas
                if isinstance(persona, dict) and _persona_has_supported_platform(persona)
            ]
            if valid_personas:
                normalized["personas"] = valid_personas

    normalized["enabled"] = _coerce_bool(normalized.get("enabled"), False)
    normalized["timeout_seconds"] = _bounded_float(normalized.get("timeout_seconds"), 45.0, 5.0, 300.0)
    normalized["retry_policy"] = _normalize_retry_policy(config.get("retry_policy") if isinstance(config, dict) else None)
    primary_model = str(normalized.get("model") or "").strip()
    normalized["retry_policy"]["fallback_models"] = [
        model for model in normalized["retry_policy"]["fallback_models"] if model != primary_model
    ][:3]
    normalized["default_content_mode"] = _normalize_content_mode(normalized.get("default_content_mode", "sfw"))
    persona_ids = {persona["id"] for persona in normalized["personas"]}
    if normalized.get("default_persona_id") not in persona_ids:
        normalized["default_persona_id"] = normalized["personas"][0]["id"]
    return normalized


def _persona_has_supported_platform(persona: dict[str, Any]) -> bool:
    raw = persona.get("platform", DEFAULT_PLATFORM_ID)
    platform_ids = raw if isinstance(raw, list) else [raw]
    return any(str(platform or "").strip().lower() in PLATFORM_SPECS for platform in platform_ids)


def _normalize_persona(persona: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    out["id"] = str(persona.get("id") or "").strip() or _gen_persona_id()
    out["label"] = str(persona.get("label") or out["id"]).strip()
    out["platform"] = normalize_platform_ids(persona.get("platform"))
    out["default_content_mode"] = _normalize_content_mode(persona.get("default_content_mode", "sfw"))
    out["voice"] = str(persona.get("voice") or "").strip()
    out["sfw_prompt"] = str(persona.get("sfw_prompt") or "").strip()
    out["nsfw_prompt"] = str(persona.get("nsfw_prompt") or "").strip()
    out["extra_prompt"] = str(persona.get("extra_prompt") or "").strip()
    avoid_raw = persona.get("avoid") or []
    out["avoid"] = [str(item).strip() for item in avoid_raw if str(item).strip()] if isinstance(avoid_raw, list) else []
    samples_raw = persona.get("samples") or []
    out["samples"] = [_clean_sample(s, out["platform"]) for s in samples_raw if isinstance(s, dict)] if isinstance(samples_raw, list) else []
    return out


def _clean_sample(sample: dict[str, Any], platform_ids: list[str]) -> dict[str, Any]:
    fields_raw = sample.get("fields") or {}
    fields: dict[str, Any] = {}
    allowed_keys = {key for platform_id in platform_ids for key in all_field_keys(platform_id)}
    if isinstance(fields_raw, dict):
        for key, value in fields_raw.items():
            if key not in allowed_keys:
                continue
            if isinstance(value, list):
                fields[str(key)] = [str(v).strip() for v in value if str(v).strip()]
            else:
                fields[str(key)] = str(value or "").strip()
    return {
        "mode": _normalize_content_mode(sample.get("mode", "sfw")),
        "note": str(sample.get("note") or "").strip(),
        "fields": fields,
    }


_NSFW_TIER = {"all_ages": 0, "sfw": 0, "r18": 1, "r18g": 2}


def content_mode_can_handle_age(content_mode: str, image_age: str) -> bool:
    """Check if the requested content_mode covers the image's age level.

    nsfw → handles all ages. sfw → only handles sfw/all_ages (tier 0).
    This user-facing gate determines which images receive LLM inference.
    """
    if _normalize_content_mode(content_mode) == "nsfw":
        return True
    return _NSFW_TIER.get(image_age, 0) == 0


def mask_llm_config(config: dict[str, Any] | None) -> dict[str, Any]:
    masked = deepcopy(normalize_llm_reverse_config(config))
    api_key = str(masked.get("api_key", ""))
    masked["has_api_key"] = bool(api_key)
    masked["api_key_masked"] = _mask_secret(api_key)
    masked["api_key"] = ""
    return masked


def validate_llm_reverse_config(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    cfg = normalize_llm_reverse_config(config)
    personas = cfg.get("personas", [])
    persona_ids = _unique_ids(personas, "persona", errors)
    valid_platforms = set(list_platform_ids())
    for persona in personas:
        platforms = persona.get("platform") if isinstance(persona.get("platform"), list) else [persona.get("platform")]
        for plat in platforms:
            if plat not in valid_platforms:
                errors.append(f"persona {persona.get('id', '')} unknown platform: {plat}")
        for sample in persona.get("samples", []):
            if sample.get("mode") not in {"sfw", "nsfw"}:
                errors.append(f"persona {persona.get('id', '')} sample mode invalid")
    if cfg.get("default_persona_id") and cfg["default_persona_id"] not in persona_ids:
        errors.append("default_persona_id not found")
    if cfg.get("enabled"):
        provider = str(cfg.get("provider") or "openai_compatible").strip().lower()
        # anthropic/gemini allow empty base_url (each has its own official endpoint fallback)
        required_keys = ("api_key", "model") if provider in ("anthropic", "google_gemini") else ("base_url", "api_key", "model")
        for key in required_keys:
            if not str(cfg.get(key, "")).strip():
                errors.append(f"{key} is required when enabled")
    return errors


def resolve_persona(
    config: dict[str, Any] | None,
    persona_id: str = "",
    content_mode: str = "",
) -> tuple[dict[str, Any], str]:
    """Resolve which persona and content mode to use.

    Falls back to default_persona_id then to the first persona. Mode falls
    back to persona.default_content_mode then config.default_content_mode.
    """
    cfg = normalize_llm_reverse_config(config)
    personas = {str(item.get("id", "")): item for item in cfg.get("personas", []) if item.get("id")}
    persona = (
        personas.get(persona_id)
        or personas.get(str(cfg.get("default_persona_id", "")))
        or next(iter(personas.values()), {})
    )
    mode = _normalize_content_mode(
        content_mode
        or persona.get("default_content_mode", "")
        or cfg.get("default_content_mode", "sfw")
    )
    return persona, mode


def infer_image_copy(
    image_path: Path | None = None,
    image_url: str | None = None,
    config: dict[str, Any] | None = None,
    persona_id: str = "",
    content_mode: str = "",
    extra_context: str = "",
    cancel_event=None,
    event_callback: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    cfg = normalize_llm_reverse_config(config)
    persona, mode = resolve_persona(cfg, persona_id, content_mode)
    platforms = persona.get("platform", DEFAULT_PLATFORM_ID)
    if not isinstance(platforms, list):
        platforms = [platforms]
    spec = get_merged_spec(platforms)
    result = _base_result(cfg, persona, mode, spec)
    if not cfg.get("enabled"):
        result["status"] = "disabled"
        result["error"] = "llm reverse disabled"
        result["error_code"] = "disabled"
        return result
    provider = str(cfg.get("provider") or "openai_compatible").strip().lower()
    required = ("api_key", "model") if provider in {"anthropic", "google_gemini"} else ("base_url", "api_key", "model")
    missing = [key for key in required if not str(cfg.get(key, "")).strip()]
    if missing:
        result["status"] = "failed"
        result["error"] = f"missing config: {', '.join(missing)}"
        result["error_code"] = "missing_config"
        return result
    if not image_path and not image_url:
        result["status"] = "failed"
        result["error"] = "image_path or image_url required"
        result["error_code"] = "image_required"
        return result

    policy = cfg["retry_policy"]
    models = _model_candidates(cfg)
    max_attempts = len(models) * (policy["repair_attempts"] + 1) * policy["request_attempts"]
    started_at = time.monotonic()
    deadline = started_at + policy["total_timeout_seconds"]
    retry_state = {
        "attempt_count": 0,
        "max_attempts": max_attempts,
        "retry_count": 0,
        "repair_count": 0,
        "models_tried": [],
        "fallback_used": False,
        "recovered": False,
        "exhausted": False,
        "elapsed_seconds": 0.0,
        "last_error_code": "",
        "history": [],
        "history_truncated": 0,
    }
    result["retry"] = retry_state
    source_path = Path(image_path) if image_path else None
    preview_cache: dict[int, str] = {}
    media_profile_index = 0
    last_failure: _AttemptFailure | None = None

    _raise_if_canceled(cancel_event)
    if source_path is not None:
        try:
            edge, quality = LLM_IMAGE_RETRY_PROFILES[0]
            preview_cache[0] = _image_to_data_url(source_path, max_edge=edge, jpeg_quality=quality)
        except Exception as exc:
            message = _scrub_error(str(exc), str(cfg.get("api_key", "")))
            result.update(status="failed", error=message, error_code="image_processing_error")
            retry_state.update(exhausted=True, last_error_code="image_processing_error")
            return result

    try:
        with httpx.Client(follow_redirects=True) as client:
            for model_index, model in enumerate(models):
                _raise_if_canceled(cancel_event)
                if time.monotonic() >= deadline:
                    last_failure = _AttemptFailure("retry_budget_exhausted", "LLM retry time budget exhausted")
                    break
                retry_state["models_tried"].append(model)
                if model_index:
                    retry_state["fallback_used"] = True
                    log.warning("LLM 反推: 主模型不可用，切换后备模型 %s", _safe_log_value(model))
                    _emit_retry_event(
                        event_callback,
                        "fallback_started",
                        retry_state,
                        model=model,
                        model_index=model_index + 1,
                        model_count=len(models),
                    )

                for repair_index in range(policy["repair_attempts"] + 1):
                    if repair_index:
                        retry_state["repair_count"] += 1
                        log.warning(
                            "LLM 反推: 响应格式无效，执行结构修复 %s/%s",
                            repair_index,
                            policy["repair_attempts"],
                        )
                        _emit_retry_event(
                            event_callback,
                            "repair_started",
                            retry_state,
                            model=model,
                            repair_attempt=repair_index,
                            repair_attempts=policy["repair_attempts"],
                            error_code=last_failure.code if last_failure else "invalid_response",
                        )

                    response_invalid = False
                    for request_index in range(1, policy["request_attempts"] + 1):
                        _raise_if_canceled(cancel_event)
                        if time.monotonic() >= deadline:
                            last_failure = _AttemptFailure("retry_budget_exhausted", "LLM retry time budget exhausted")
                            break

                        if image_url:
                            image_ref = image_url
                            media_max_edge = 0
                        else:
                            if media_profile_index not in preview_cache:
                                edge, quality = LLM_IMAGE_RETRY_PROFILES[media_profile_index]
                                preview_cache[media_profile_index] = _image_to_data_url(
                                    source_path, max_edge=edge, jpeg_quality=quality
                                )
                            image_ref = preview_cache[media_profile_index]
                            media_max_edge = LLM_IMAGE_RETRY_PROFILES[media_profile_index][0]

                        try:
                            request_timeout = _remaining_request_timeout(cfg["timeout_seconds"], deadline)
                        except _AttemptFailure as exc:
                            last_failure = exc
                            retry_state["last_error_code"] = exc.code
                            break
                        retry_state["attempt_count"] += 1
                        attempt_number = retry_state["attempt_count"]
                        attempt_started = time.monotonic()
                        repair_code = last_failure.code if repair_index and last_failure else ""
                        log.info(
                            "LLM 反推: 请求 %s/%s（模型=%s，请求轮次=%s/%s%s）",
                            attempt_number,
                            max_attempts,
                            _safe_log_value(model),
                            request_index,
                            policy["request_attempts"],
                            f"，结构修复={repair_index}/{policy['repair_attempts']}" if repair_index else "",
                        )
                        _emit_retry_event(
                            event_callback,
                            "attempt_started",
                            retry_state,
                            model=model,
                            request_attempt=request_index,
                            request_attempts=policy["request_attempts"],
                            repair_attempt=repair_index,
                            repair_attempts=policy["repair_attempts"],
                            media_max_edge=media_max_edge,
                            progress=min(0.9, (attempt_number - 1) / max(1, max_attempts)),
                        )

                        failure: _AttemptFailure | None = None
                        normalized_output: dict[str, Any] | None = None
                        try:
                            normalized_output = _perform_inference_request(
                                client,
                                provider,
                                {**cfg, "model": model},
                                persona,
                                mode,
                                spec,
                                image_ref,
                                extra_context,
                                repair_code,
                                request_timeout,
                            )
                        except InterruptedError:
                            raise
                        except _AttemptFailure as exc:
                            failure = exc
                        except Exception as exc:
                            failure = _AttemptFailure(
                                "unexpected_error",
                                _scrub_error(str(exc), str(cfg.get("api_key", ""))),
                                failover_allowed=False,
                            )

                        elapsed_ms = round((time.monotonic() - attempt_started) * 1000)
                        if failure is None and normalized_output is not None:
                            _append_retry_history(
                                retry_state,
                                {
                                    "attempt": attempt_number,
                                    "model": model,
                                    "request_attempt": request_index,
                                    "repair_attempt": repair_index,
                                    "media_max_edge": media_max_edge,
                                    "outcome": "ok",
                                    "elapsed_ms": elapsed_ms,
                                },
                            )
                            combined = "\n".join(_stringify_for_check(v) for v in normalized_output.values())
                            if _has_political_content(combined):
                                log.warning("LLM output contains political keywords (persona should prevent this) — passing through")
                            retry_state.update(
                                retry_count=max(0, attempt_number - 1),
                                recovered=attempt_number > 1,
                                exhausted=False,
                                elapsed_seconds=round(time.monotonic() - started_at, 3),
                                last_error_code="",
                            )
                            result.update(fields=normalized_output, status="ok", model=model, error="", error_code="")
                            log.info(
                                "LLM 反推: 文案生成成功（请求 %s 次%s，耗时 %.2fs）",
                                attempt_number,
                                "，已使用后备模型" if model_index else "",
                                retry_state["elapsed_seconds"],
                            )
                            _emit_retry_event(event_callback, "succeeded", retry_state, model=model, progress=1.0)
                            return result

                        assert failure is not None
                        last_failure = failure
                        retry_state["last_error_code"] = failure.code
                        image_profile_changed = False
                        if failure.adapt_image and policy["adaptive_image"] and source_path is not None:
                            next_profile_index = min(
                                media_profile_index + 1,
                                len(LLM_IMAGE_RETRY_PROFILES) - 1,
                            )
                            image_profile_changed = next_profile_index != media_profile_index
                            media_profile_index = next_profile_index
                        if failure.code == "payload_too_large" and not image_profile_changed:
                            failure.retryable = False
                        history_entry = {
                            "attempt": attempt_number,
                            "model": model,
                            "request_attempt": request_index,
                            "repair_attempt": repair_index,
                            "media_max_edge": media_max_edge,
                            "outcome": "failed",
                            "error_code": failure.code,
                            "elapsed_ms": elapsed_ms,
                        }
                        if failure.status_code is not None:
                            history_entry["status_code"] = failure.status_code
                        _append_retry_history(retry_state, history_entry)
                        _emit_retry_event(
                            event_callback,
                            "attempt_failed",
                            retry_state,
                            model=model,
                            error_code=failure.code,
                            status_code=failure.status_code,
                        )
                        log.warning(
                            "LLM 反推: 请求 %s 失败 [%s] %s",
                            attempt_number,
                            failure.code,
                            _safe_log_value(failure.message, 180),
                        )

                        if failure.response_invalid:
                            response_invalid = True
                            break
                        if failure.retryable and request_index < policy["request_attempts"]:
                            delay = _retry_delay_seconds(policy, request_index, failure.retry_after)
                            history_entry["retry_delay_seconds"] = round(delay, 3)
                            log.warning("LLM 反推: %.2fs 后重试请求", delay)
                            _emit_retry_event(
                                event_callback,
                                "retry_scheduled",
                                retry_state,
                                model=model,
                                error_code=failure.code,
                                delay_seconds=round(delay, 3),
                            )
                            try:
                                _wait_for_retry(delay, cancel_event, deadline)
                            except _AttemptFailure as exc:
                                last_failure = exc
                                retry_state["last_error_code"] = exc.code
                                break
                            continue
                        break

                    if last_failure and last_failure.code == "retry_budget_exhausted":
                        break
                    if response_invalid and repair_index < policy["repair_attempts"]:
                        continue
                    break

                has_fallback = model_index + 1 < len(models)
                if not (
                    has_fallback
                    and last_failure is not None
                    and last_failure.failover_allowed
                    and time.monotonic() < deadline
                ):
                    break
    except InterruptedError:
        raise
    except Exception as exc:
        last_failure = _AttemptFailure(
            "client_error",
            _scrub_error(str(exc), str(cfg.get("api_key", ""))),
            failover_allowed=False,
        )

    if last_failure is None:
        last_failure = _AttemptFailure("unknown_error", "LLM inference failed without a response")
    retry_state.update(
        retry_count=max(0, retry_state["attempt_count"] - 1),
        recovered=False,
        exhausted=True,
        elapsed_seconds=round(time.monotonic() - started_at, 3),
        last_error_code=last_failure.code,
    )
    result.update(
        status="failed",
        error=_scrub_error(last_failure.message, str(cfg.get("api_key", ""))),
        error_code=last_failure.code,
    )
    log.error(
        "LLM 反推: 多级重试耗尽 [%s]（请求 %s 次，耗时 %.2fs）",
        last_failure.code,
        retry_state["attempt_count"],
        retry_state["elapsed_seconds"],
    )
    _emit_retry_event(event_callback, "failed", retry_state, error_code=last_failure.code)
    return result


def _model_candidates(cfg: dict[str, Any]) -> list[str]:
    primary = str(cfg.get("model") or "").strip()
    models = [primary]
    for fallback in cfg["retry_policy"].get("fallback_models") or []:
        model = str(fallback or "").strip()
        if model and model not in models:
            models.append(model)
    return models


def _emit_retry_event(
    callback: Callable[[str, dict[str, Any]], None] | None,
    event: str,
    retry_state: dict[str, Any],
    **details: Any,
) -> None:
    if callback is None:
        return
    maximum = max(1, int(retry_state.get("max_attempts") or 1))
    completed = int(retry_state.get("attempt_count") or 0)
    payload = {
        "attempt": completed,
        "max_attempts": maximum,
        "progress": min(0.9, completed / maximum),
        **details,
    }
    try:
        callback(event, payload)
    except Exception:
        log.exception("LLM retry event callback failed")


def _build_provider_request(
    provider: str,
    cfg: dict[str, Any],
    persona: dict[str, Any],
    mode: str,
    spec: dict[str, Any],
    image_ref: str,
    extra_context: str,
    repair_code: str,
) -> tuple[dict[str, Any], str, dict[str, str]]:
    if provider == "google_gemini":
        return _build_gemini_request(cfg, persona, mode, spec, image_ref, extra_context, repair_code)
    if provider == "anthropic":
        return _build_anthropic_request(cfg, persona, mode, spec, image_ref, extra_context, repair_code)
    payload = _build_request_payload(cfg, persona, mode, spec, image_ref, extra_context, repair_code)
    endpoint = _chat_completions_url(str(cfg.get("base_url", "")))
    headers = {"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"}
    return payload, endpoint, headers


def _perform_inference_request(
    client: httpx.Client,
    provider: str,
    cfg: dict[str, Any],
    persona: dict[str, Any],
    mode: str,
    spec: dict[str, Any],
    image_ref: str,
    extra_context: str,
    repair_code: str,
    request_timeout: float,
) -> dict[str, Any]:
    try:
        payload, endpoint, headers = _build_provider_request(
            provider, cfg, persona, mode, spec, image_ref, extra_context, repair_code
        )
    except Exception as exc:
        raise _AttemptFailure(
            "request_build_error",
            _scrub_error(str(exc), str(cfg.get("api_key", ""))),
            failover_allowed=True,
        ) from exc
    try:
        response = client.post(endpoint, headers=headers, json=payload, timeout=request_timeout)
    except httpx.RequestError as exc:
        raise _classify_request_exception(exc, str(cfg.get("api_key", ""))) from exc
    if response.status_code >= 400:
        raise _http_failure(response, str(cfg.get("api_key", "")))
    try:
        data = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise _AttemptFailure(
            "invalid_json_response",
            "upstream returned a non-JSON response",
            response_invalid=True,
            failover_allowed=True,
        ) from exc
    if not isinstance(data, dict):
        raise _AttemptFailure(
            "invalid_response",
            "upstream response is not a JSON object",
            response_invalid=True,
            failover_allowed=True,
        )
    try:
        if provider == "google_gemini":
            content = _extract_gemini_content(data)
        elif provider == "anthropic":
            content = _extract_anthropic_content(data)
        else:
            content = _extract_message_content(data)
        parsed = _parse_json_object(content)
        return _normalize_output(parsed, spec)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise _semantic_failure(exc) from exc


def _classify_request_exception(exc: httpx.RequestError, api_key: str) -> _AttemptFailure:
    message = _scrub_error(str(exc), api_key)
    if isinstance(exc, httpx.TimeoutException):
        if isinstance(exc, httpx.ConnectTimeout):
            code = "connect_timeout"
        elif isinstance(exc, httpx.ReadTimeout):
            code = "read_timeout"
        elif isinstance(exc, httpx.WriteTimeout):
            code = "write_timeout"
        else:
            code = "request_timeout"
        return _AttemptFailure(
            code,
            message or code,
            retryable=True,
            failover_allowed=True,
            adapt_image=not isinstance(exc, httpx.ConnectTimeout),
        )
    lower = message.lower()
    certificate_error = "certificate" in lower or "cert verify" in lower
    return _AttemptFailure(
        "tls_error" if certificate_error else "network_error",
        message or type(exc).__name__,
        retryable=not certificate_error,
        failover_allowed=not certificate_error,
    )


def _http_failure(response: httpx.Response, api_key: str) -> _AttemptFailure:
    status = int(response.status_code)
    message = _response_error_message(response, api_key)
    if status == 401:
        code = "authentication_failed"
    elif status == 403:
        code = "permission_denied"
    elif status == 404:
        code = "model_or_endpoint_not_found"
    elif status == 413:
        code = "payload_too_large"
    elif status == 429:
        code = "rate_limited"
    elif status == 408:
        code = "request_timeout"
    elif status >= 500:
        code = "upstream_unavailable"
    elif status in {400, 422}:
        code = "invalid_request"
    else:
        code = "http_error"
    return _AttemptFailure(
        code,
        message,
        retryable=status in RETRYABLE_HTTP_STATUSES or status == 413,
        failover_allowed=status not in {401, 403},
        adapt_image=status == 413,
        status_code=status,
        retry_after=_retry_after_seconds(response),
    )


def _response_error_message(response: httpx.Response, api_key: str) -> str:
    provider_message = ""
    try:
        data = response.json()
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict):
                provider_message = str(error.get("message") or error.get("code") or error.get("type") or "")
            elif error:
                provider_message = str(error)
            if not provider_message:
                provider_message = str(data.get("message") or "")
    except (ValueError, json.JSONDecodeError):
        pass
    suffix = f": {provider_message}" if provider_message else ""
    return _scrub_error(f"HTTP {response.status_code}{suffix}", api_key)


def _semantic_failure(exc: Exception) -> _AttemptFailure:
    message = str(exc) or type(exc).__name__
    lower = message.lower()
    if isinstance(exc, json.JSONDecodeError):
        code = "invalid_model_json"
    elif "empty required fields" in lower:
        code = "empty_required_fields"
    elif "empty choices" in lower or "no text" in lower or "empty candidates" in lower:
        code = "empty_model_response"
    else:
        code = "invalid_model_response"
    return _AttemptFailure(
        code,
        _scrub_error(message, ""),
        response_invalid=True,
        failover_allowed=True,
    )


def _retry_after_seconds(response: httpx.Response) -> float | None:
    retry_after_ms = response.headers.get("retry-after-ms")
    if retry_after_ms:
        try:
            return max(0.0, float(retry_after_ms) / 1000.0)
        except ValueError:
            pass
    value = response.headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _retry_delay_seconds(policy: dict[str, Any], request_index: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return max(0.0, retry_after)
    ceiling = min(
        float(policy["max_delay_seconds"]),
        float(policy["base_delay_seconds"]) * (2 ** max(0, request_index - 1)),
    )
    return random.uniform(ceiling / 2, ceiling)


def _remaining_request_timeout(configured_timeout: float, deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _AttemptFailure("retry_budget_exhausted", "LLM retry time budget exhausted")
    return max(0.1, min(float(configured_timeout), remaining))


def _wait_for_retry(delay: float, cancel_event, deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0 or delay >= remaining:
        raise _AttemptFailure("retry_budget_exhausted", "LLM retry time budget exhausted before next attempt")
    if delay <= 0:
        _raise_if_canceled(cancel_event)
        return
    if cancel_event is not None and callable(getattr(cancel_event, "wait", None)):
        if cancel_event.wait(delay):
            raise InterruptedError("task canceled")
        return
    end = time.monotonic() + delay
    while True:
        _raise_if_canceled(cancel_event)
        sleep_for = min(0.1, end - time.monotonic())
        if sleep_for <= 0:
            return
        time.sleep(sleep_for)


def _append_retry_history(retry_state: dict[str, Any], entry: dict[str, Any]) -> None:
    history = retry_state["history"]
    if len(history) >= MAX_RETRY_HISTORY:
        history.pop(0)
        retry_state["history_truncated"] += 1
    history.append(entry)


def _safe_log_value(value: Any, limit: int = 90) -> str:
    return re.sub(r"[\r\n\t]+", " ", str(value or "")).strip()[:limit]


def build_llm_retry_activity(event: str, details: dict[str, Any]) -> dict[str, Any]:
    """Build a bounded, JSON-safe task activity payload for UI/SSE consumers."""
    activity: dict[str, Any] = {"kind": "llm_retry", "event": str(event or "")}
    integer_fields = (
        "attempt",
        "max_attempts",
        "request_attempt",
        "request_attempts",
        "repair_attempt",
        "repair_attempts",
        "model_index",
        "model_count",
        "media_max_edge",
        "status_code",
    )
    for key in integer_fields:
        value = details.get(key)
        if value is not None:
            try:
                activity[key] = int(value)
            except (TypeError, ValueError):
                pass
    for key in ("progress", "delay_seconds"):
        value = details.get(key)
        if value is not None:
            try:
                activity[key] = round(float(value), 3)
            except (TypeError, ValueError):
                pass
    if details.get("model"):
        activity["model"] = _safe_log_value(details["model"])
    if details.get("error_code"):
        activity["error_code"] = _safe_log_value(details["error_code"])
    return activity


def apply_llm_result_to_pixiv_payload(payload: dict[str, Any], result: dict[str, Any]) -> None:
    if result.get("status") != "ok":
        return
    fields = result.get("fields") or {}
    for key in ("title_ja", "title_zh", "caption_ja", "caption_zh"):
        value = str(fields.get(key, "") or "").strip()
        if value:
            payload[key] = value


def _base_result(cfg: dict[str, Any], persona: dict[str, Any], mode: str, spec: dict[str, Any]) -> dict[str, Any]:
    plat = persona.get("platform", DEFAULT_PLATFORM_ID)
    if not isinstance(plat, list):
        plat = [plat]
    return {
        "enabled": bool(cfg.get("enabled")),
        "status": "disabled",
        "provider": str(cfg.get("provider", "openai_compatible")),
        "model": str(cfg.get("model", "")),
        "persona_id": str(persona.get("id", "")),
        "platform": plat,
        "platform_label": str(spec.get("label", "")),
        "content_mode": mode,
        "fields": {},
        "error": "",
        "error_code": "",
        "retry": {
            "attempt_count": 0,
            "max_attempts": 0,
            "retry_count": 0,
            "repair_count": 0,
            "models_tried": [],
            "fallback_used": False,
            "recovered": False,
            "exhausted": False,
            "elapsed_seconds": 0.0,
            "last_error_code": "",
            "history": [],
            "history_truncated": 0,
        },
    }


def _build_request_payload(
    cfg: dict[str, Any],
    persona: dict[str, Any],
    mode: str,
    spec: dict[str, Any],
    image_ref: str,
    extra_context: str = "",
    repair_code: str = "",
) -> dict[str, Any]:
    fields = spec.get("fields") or []
    extra_fields = spec.get("extra_fields") or []
    required_keys = [str(f.get("key")) for f in fields if f.get("key")]
    extra_keys = [str(f.get("key")) for f in extra_fields if f.get("key")]
    field_lines = [_describe_field(f) for f in fields]
    extra_lines = [_describe_field(f) for f in extra_fields]

    mode_prompt = str(persona.get("nsfw_prompt" if mode == "nsfw" else "sfw_prompt", ""))
    voice = str(persona.get("voice", "")).strip()
    extra_prompt = str(persona.get("extra_prompt", "")).strip()
    avoid = persona.get("avoid") or []
    avoid_line = ", ".join(str(item) for item in avoid) if avoid else ""

    parts: list[str] = []
    parts.append(spec.get("prompt_intro", "Analyze the image."))
    parts.append(
        "Return only one JSON object. Required keys: "
        + ", ".join(required_keys)
        + (f". Optional keys: {', '.join(extra_keys)}" if extra_keys else "")
        + "."
    )
    parts.append("Field rules:")
    parts.extend(f"  - {line}" for line in field_lines + extra_lines)
    parts.append(f"content_mode: {mode}")
    if voice:
        parts.append(f"voice / style: {voice}")
    if mode_prompt:
        parts.append(f"mode instruction: {mode_prompt}")
    if extra_prompt:
        parts.append(f"extra persona instruction: {extra_prompt}")
    if extra_context:
        parts.append(f"image subject context (from file metadata): {extra_context}")
    if avoid_line:
        parts.append(f"avoid topics: {avoid_line}")
    if spec.get("policy_notes"):
        parts.append(f"platform policy: {spec['policy_notes']}")
    parts.append(
        "STRICT: Do not mention political parties, political ideology, territorial disputes, "
        "sovereignty, diplomatic relations, elections, or real-world political conflicts. "
        "This applies even to fictional/game settings — describe characters as individuals, "
        "not as representatives of nations or factions with political implications. "
        "Focus only on visual aesthetics, mood, character personality, and art style."
    )
    parts.append("Do not identify real people. Do not invent copyrighted character names unless visually obvious.")
    if repair_code:
        parts.append(
            "REPAIR: The previous response was rejected because "
            f"{repair_code}. Return exactly one valid JSON object with the required keys and no markdown, "
            "commentary, preface, or trailing text. Every required text field must be non-empty."
        )

    samples_block = _render_samples_block(persona, mode, spec)
    if samples_block:
        parts.append("")
        parts.append("Example outputs to imitate (style, tone, length). Match this voice closely:")
        parts.append(samples_block)

    prompt = "\n".join(parts).strip()

    return {
        "model": str(cfg.get("model", "")),
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_ref}},
                ],
            }
        ],
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }


def _describe_field(field: dict[str, Any]) -> str:
    key = field.get("key", "")
    kind = field.get("kind", "text")
    if kind == "tags":
        max_count = field.get("max_count", 10)
        per_max = field.get("max", 50)
        return f"{key}: list of strings, up to {max_count} items, each within {per_max} chars"
    limit = field.get("max", 200)
    shape = "single line" if kind == "text" else "1-3 short lines"
    return f"{key}: string, {shape}, within {limit} chars"


def _render_samples_block(persona: dict[str, Any], mode: str, spec: dict[str, Any]) -> str:
    samples = [s for s in (persona.get("samples") or []) if isinstance(s, dict)]
    matched = [s for s in samples if s.get("mode") == mode]
    if not matched:
        return ""
    _plat = persona.get("platform", DEFAULT_PLATFORM_ID)
    if isinstance(_plat, list):
        valid_keys = set(k for pid in _plat for k in all_field_keys(pid))
    else:
        valid_keys = set(all_field_keys(_plat))
    rendered: list[str] = []
    for idx, sample in enumerate(matched[:MAX_FEW_SHOT_SAMPLES], start=1):
        fields = sample.get("fields") or {}
        clean = {k: v for k, v in fields.items() if k in valid_keys and v not in (None, "", [])}
        if not clean:
            continue
        note = str(sample.get("note", "")).strip()
        header = f"Example {idx}" + (f" ({note})" if note else "")
        body = json.dumps(clean, ensure_ascii=False, indent=2)
        rendered.append(f"{header}:\n{body}")
    return "\n\n".join(rendered)


def _chat_completions_url(base_url: str) -> str:
    from urllib.parse import urlparse
    base = base_url.strip()
    if base.lower().endswith("/chat/completions"):
        return base
    if not base.endswith("/"):
        base += "/"
    # If the URL already has a non-root path (e.g. /openai/ or /v1/),
    # the caller has provided a versioned/prefixed base — just append
    # chat/completions. Only fall back to v1/chat/completions when the
    # base path is bare root ("/").
    parsed_path = urlparse(base).path
    if parsed_path in ("/", ""):
        return urljoin(base, "v1/chat/completions")
    return urljoin(base, "chat/completions")


def _parse_data_url(data_url: str) -> tuple[str, str]:
    """Return (mime_type, base64_data) from a data URL."""
    if not data_url.startswith("data:"):
        raise ValueError("not a data URL")
    header, data = data_url.split(",", 1)
    mime = header.split(";")[0][5:]  # strip "data:"
    return mime or "image/jpeg", data


def _build_gemini_request(
    cfg: dict[str, Any],
    persona: dict[str, Any],
    mode: str,
    spec: dict[str, Any],
    image_ref: str,
    extra_context: str = "",
    repair_code: str = "",
) -> tuple[dict[str, Any], str, dict[str, str]]:
    """Return (payload, endpoint, headers) for Gemini native API."""
    oai_payload = _build_request_payload(cfg, persona, mode, spec, image_ref, extra_context, repair_code)
    content_parts = oai_payload["messages"][0]["content"]
    prompt_text = next((p["text"] for p in content_parts if p.get("type") == "text"), "")

    parts: list[dict[str, Any]] = [{"text": prompt_text}]
    if image_ref.startswith("data:"):
        mime_type, b64_data = _parse_data_url(image_ref)
        parts.append({"inline_data": {"mime_type": mime_type, "data": b64_data}})
    else:
        parts.append({"file_data": {"file_uri": image_ref, "mime_type": "image/jpeg"}})

    model = str(cfg.get("model", "gemini-2.5-flash"))
    base = str(cfg.get("base_url", "")).rstrip("/") or "https://generativelanguage.googleapis.com"
    endpoint = f"{base}/v1beta/models/{model}:generateContent"
    headers = {"x-goog-api-key": str(cfg["api_key"]), "Content-Type": "application/json"}
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.7, "responseMimeType": "application/json"},
    }
    return payload, endpoint, headers


def _extract_gemini_content(data: dict[str, Any]) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        raise ValueError("empty candidates in Gemini response")
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    texts = [p.get("text", "") for p in parts if p.get("text")]
    if not texts:
        raise ValueError("no text in Gemini candidate parts")
    return "\n".join(texts)


def _build_anthropic_request(
    cfg: dict[str, Any],
    persona: dict[str, Any],
    mode: str,
    spec: dict[str, Any],
    image_ref: str,
    extra_context: str = "",
    repair_code: str = "",
) -> tuple[dict[str, Any], str, dict[str, str]]:
    """Return (payload, endpoint, headers) for Anthropic Messages API."""
    oai_payload = _build_request_payload(cfg, persona, mode, spec, image_ref, extra_context, repair_code)
    content_parts = oai_payload["messages"][0]["content"]
    prompt_text = next((p["text"] for p in content_parts if p.get("type") == "text"), "")

    anthropic_content: list[dict[str, Any]] = []
    if image_ref.startswith("data:"):
        mime_type, b64_data = _parse_data_url(image_ref)
        anthropic_content.append(
            {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": b64_data}}
        )
    else:
        anthropic_content.append(
            {"type": "image", "source": {"type": "url", "url": image_ref}}
        )
    anthropic_content.append({"type": "text", "text": prompt_text})

    base = str(cfg.get("base_url") or "").rstrip("/")
    endpoint = f"{base}/v1/messages" if base else "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": str(cfg["api_key"]),
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": str(cfg.get("model", "")),
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": anthropic_content}],
    }
    return payload, endpoint, headers


def _extract_anthropic_content(data: dict[str, Any]) -> str:
    content = data.get("content") or []
    texts = [block.get("text", "") for block in content if block.get("type") == "text"]
    if not texts:
        raise ValueError("no text content in Anthropic response")
    return "\n".join(texts)


def _image_to_data_url(
    path: Path,
    *,
    max_edge: int = LLM_IMAGE_MAX_EDGE,
    jpeg_quality: int = LLM_IMAGE_JPEG_QUALITY,
) -> str:
    with Image.open(path) as source:
        source.seek(0)
        image = ImageOps.exif_transpose(source)
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            rgba = image.convert("RGBA")
            rgb = Image.new("RGB", rgba.size, "white")
            rgb.paste(rgba, mask=rgba.getchannel("A"))
        else:
            rgb = image.convert("RGB")
        buffer = io.BytesIO()
        rgb.save(
            buffer,
            format="JPEG",
            quality=jpeg_quality,
            optimize=True,
            progressive=True,
        )
    data = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{data}"


def _extract_message_content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("empty choices")
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(raw[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model output is not a JSON object")
    return parsed


def _normalize_output(data: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    has_required = False
    for field in spec.get("fields") or []:
        key = str(field.get("key", ""))
        if not key:
            continue
        value = _coerce_field_value(data.get(key), field)
        out[key] = value
        if value not in (None, "", []):
            has_required = True
    for field in spec.get("extra_fields") or []:
        key = str(field.get("key", ""))
        if not key or key not in data:
            continue
        out[key] = _coerce_field_value(data.get(key), field)
    if not has_required:
        raise ValueError(f"empty required fields for platform {spec.get('label', '')}")
    return out


def _coerce_field_value(value: Any, field: dict[str, Any]) -> Any:
    kind = field.get("kind", "text")
    if kind == "tags":
        max_count = int(field.get("max_count", 10))
        per_max = int(field.get("max", 50))
        items = value if isinstance(value, list) else []
        return [_clean_text(item, per_max) for item in items[:max_count] if _clean_text(item, per_max)]
    limit = int(field.get("max", 200))
    return _clean_text(value, limit)


def _clean_text(value: Any, limit: int) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"[ \t]+", " ", text)
    return text[:limit].strip()


def _stringify_for_check(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value or "")


def _has_political_content(text: str) -> bool:
    return bool(POLITICAL_RE.search(text or ""))


def _normalize_content_mode(value: Any) -> str:
    mode = str(value or "sfw").strip().lower()
    return mode if mode in {"sfw", "nsfw"} else "sfw"


def _unique_ids(items: list[Any], label: str, errors: list[str]) -> set[str]:
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            errors.append(f"invalid {label} entry")
            continue
        ident = str(item.get("id", "")).strip()
        if not ident:
            errors.append(f"{label} id required")
        elif ident in seen:
            errors.append(f"duplicate {label} id: {ident}")
        else:
            seen.add(ident)
    return seen


def _mask_secret(secret: str) -> str:
    if len(secret) > 4:
        return "*" * (len(secret) - 4) + secret[-4:]
    return "*" * len(secret)


def _scrub_error(message: str, api_key: str) -> str:
    text = message or ""
    if api_key:
        text = text.replace(api_key, "***")
    text = _SECRET_HEADER_RE.sub(lambda match: f"{match.group(1)}=***", text)
    text = _SECRET_QUERY_RE.sub(lambda match: f"{match.group(1)}***", text)
    text = _URL_USERINFO_RE.sub(r"\1***@", text)
    return text[:500]


def _raise_if_canceled(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("task canceled")


def _gen_persona_id() -> str:
    import secrets
    import time

    return f"persona_{int(time.time() * 1000)}_{secrets.token_hex(3)}"
