from __future__ import annotations

import argparse
import json
import logging
import random
import re
import shutil
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, PngImagePlugin

from .paths import PROJECT_ROOT, runtime_paths
from .runtime import ensure_runtime_layout
from .watermark import (
    ImageWatermarkSpec,
    TextWatermarkSpec,
    WatermarkError,
    WatermarkService,
)
from .humanize import HumanSession
from .pixiv.censor import CENSOR_CLASS_BY_NAME, CensorEngine, DEFAULT_CENSOR_CLASSES, DeepghsDetector, parse_class_set
from .pixiv.llm_platforms import field_specs_for_consumer
from .pixiv.llm_reverse import (
    apply_llm_result_to_pixiv_payload,
    build_llm_retry_activity,
    content_mode_can_handle_age,
    default_llm_reverse_config,
    infer_image_copy,
    normalize_llm_reverse_config,
    resolve_persona,
)

# Per-platform processing requirements. Civitai keeps generation metadata;
# Pixiv receives a sanitized, policy-checked copy with generated Japanese copy.
PLATFORM_RULES: dict[str, dict] = {
    "civitai": {"needs_sanitize": False, "needs_censor": False, "needs_copy": False},
    "pixiv":   {"needs_sanitize": True,  "needs_censor": True,  "needs_copy": True},
}


def _targets_need_copy(targets) -> bool:
    return any(PLATFORM_RULES.get(t, {}).get("needs_copy") for t in targets)


def _emit_progress(callback, stage: str, **details) -> None:
    if callback is not None:
        callback(stage, **details)


def _emit_llm_retry_progress(callback, event: str, details: dict) -> None:
    activity = build_llm_retry_activity(event, details)
    _emit_progress(
        callback,
        "generating_copy",
        stage_progress=float(details.get("progress") or 0.0),
        activity=activity,
    )


def _load_watermark_for_targets(
    targets: list[str],
) -> tuple[WatermarkService | None, TextWatermarkSpec | ImageWatermarkSpec | None]:
    if not any(PLATFORM_RULES.get(target, {}).get("needs_sanitize") for target in targets):
        return None, None
    service = WatermarkService(ensure_runtime_layout(SCRIPT_DIR).watermark)
    spec = service.load_config()
    if spec.enabled:
        if spec.renderer == "image":
            log.info(f"图片水印: 已启用 (renderer={spec.renderer}, image={spec.file_name})")
        else:
            font_name = spec.font.file_name or "system"
            log.info(f"文字水印: 已启用 (renderer={spec.renderer}, font={font_name})")
    return service, spec


def _build_llm_extra_context(
    pixiv_payload: dict | None,
    source_meta: dict | None = None,
) -> str:
    if not pixiv_payload and not source_meta:
        return ""
    parts: list[str] = []
    if pixiv_payload:
        domain = str(pixiv_payload.get("domain", "") or "").strip()
        if domain:
            parts.append(f"domain={domain}")
        entity_tags = pixiv_payload.get("entity_tags") or []
        if entity_tags:
            parts.append("entity tags: " + ", ".join(str(t) for t in entity_tags[:10]))
        hits = pixiv_payload.get("metadata_entity_hits") or []
        if hits:
            hit_names = [str(h.get("name") or h) for h in hits[:5] if h]
            if hit_names:
                parts.append("metadata entities: " + ", ".join(hit_names))
    if source_meta:
        metadata = source_meta.get("metadata")
        if metadata:
            raw_prompt = re.sub(r",\s*,", ",", _LORA_RE.sub("", getattr(metadata, "positive_prompt", "") or "")).strip().strip(",")
            if raw_prompt:
                parts.append(
                    "generation prompt (identify characters from this): " + raw_prompt
                )
    return "; ".join(parts)


from .pixiv.standalone import StandaloneMetadataReader, StandaloneTaggerBridge
from .pixiv.pixai_tagger import PixAITaggerBridge
from .pixiv.tagger_settings import (
    load_haintag_settings,
    resolve_cl_model_dir,
    resolve_pixai_model_dir,
)
from .pixiv.session import PIXIV_SESSION, PixivFlowError, PixivProfileInUseError
from .pixiv.storage import (
    append_validation_case,
    create_manifest_path,
    create_rule_fit_report_path,
    ensure_runtime_files,
    find_target_successes,
    load_json,
    save_json,
    write_manifest,
)
from .pixiv.support import (
    HainTagBridge,
    HainTagTaggerBridge,
    build_pixiv_payload,
    collect_artwork_urls_from_source,
    collect_rule_fit_sample_manifests,
    compare_rule_fit_samples,
    close_pixiv_browser,
    create_pixiv_post,
    PixivPostResult,
    ensure_on_pixiv_upload_page,
    extract_artwork_id,
    fetch_pixiv_illust_data,
    force_pixiv_age_restriction,
    infer_age_restriction,
    normalize_key,
    open_pixiv_browser,
    PIXIV_RULE_FIT_PROFILE_DIR,
    pixiv_browse_transition,
    summarize_rule_fit_report,
    sanitize_image_for_pixiv,
    warm_up_pixiv_session,
)

SCRIPT_DIR = PROJECT_ROOT
_RUNTIME_PATHS = runtime_paths(SCRIPT_DIR)
UPLOAD_DIR = SCRIPT_DIR / "upload"
DONE_DIR = SCRIPT_DIR / "done"
LOG_DIR = _RUNTIME_PATHS.logs
TMP_DIR = _RUNTIME_PATHS.temp
CHROME_PROFILE_DIR = Path.home() / ".civitai_splitter_chrome"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
CIVITAI_BASE = "https://civitai.red"
DONE_DAYS = 7
_LORA_RE = re.compile(r"<lora:([^:>]+):([^>]+)>")
TARGETS = {"civitai", "pixiv"}


def _extract_llm_visual_keywords(result: dict) -> list[str]:
    fields = result.get("fields") if result.get("status") == "ok" else None
    if not isinstance(fields, dict):
        return []
    keyword_fields = field_specs_for_consumer("pixiv", "tag_candidates")
    keyword_limit = max(
        (int(field.get("max_count", 0) or 0) for field in keyword_fields),
        default=20,
    )
    reserved_keys = {
        normalize_key(value)
        for field in keyword_fields
        for value in field.get("forbidden_values", [])
    }
    source: list = []
    for field in keyword_fields:
        value = fields.get(str(field.get("key") or ""))
        if isinstance(value, list):
            source.extend(value)
        elif isinstance(value, str):
            source.append(value)

    keywords: list[str] = []
    seen: set[str] = set()
    for raw in source:
        for part in re.split(r"[,，、;；\r\n]+", str(raw or "")):
            keyword = re.sub(r"\s+", " ", part).strip().lstrip("#").strip()
            key = normalize_key(keyword)
            if (
                not key
                or key in seen
                or key in reserved_keys
                or len(keyword) > 50
                or re.match(r"(?i)^(?:https?://|www\.)", keyword)
            ):
                continue
            seen.add(key)
            keywords.append(keyword)
            if len(keywords) >= keyword_limit:
                return keywords
    return keywords


def _merge_llm_keywords_into_groups(
    groups: dict[str, list[tuple[str, float]]],
    keywords: list[str],
) -> dict[str, list[tuple[str, float]]]:
    merged = {category: list(entries or []) for category, entries in groups.items()}
    general = merged.setdefault("general", [])
    seen = {
        normalize_key(entry[0] if isinstance(entry, (tuple, list)) and entry else str(entry))
        for entry in general
    }
    for keyword in keywords:
        key = normalize_key(keyword)
        if key and key not in seen:
            general.append((keyword, 1.0))
            seen.add(key)
    return merged


def sync_playwright():
    from patchright.sync_api import sync_playwright as factory

    return factory()


def _raise_if_canceled(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("task canceled")


def _sleep_with_cancel(seconds: float, cancel_event, poll: float = 0.2) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        _raise_if_canceled(cancel_event)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(poll, remaining))


def check_civitai_safety(
    image_path: Path,
    source_meta: dict,
    age_rules: dict,
    safety_cfg: dict,
) -> tuple[bool, str]:
    rating = infer_age_restriction(image_path, age_rules)
    unsafe = {r.lower() for r in safety_cfg.get("unsafe_ratings", ["r18", "r18g"])}
    if rating.lower() not in unsafe:
        return False, ""

    tokens: set[str] = set()
    phrases: set[str] = set()
    stem = image_path.stem.lower().replace("_", " ")
    phrases.add(stem)
    for part in re.split(r"[\s_\-\[\](){}|]+", image_path.stem.lower()):
        tok = part.strip()
        if tok:
            tokens.add(tok)
    metadata = source_meta.get("metadata")
    if metadata:
        prompt = _LORA_RE.sub("", getattr(metadata, "positive_prompt", "") or "")
        for part in re.split(r"[,|\n]+", prompt.lower()):
            tok = re.sub(r":\s*[\d.]+$", "", part.strip().replace("_", " ")).strip()
            if tok:
                tokens.add(tok)
                phrases.add(tok)

    haystack = "\n".join(phrases)
    minor_tags = {t.lower().replace("_", " ") for t in safety_cfg.get("minor_tags", [])}
    school_tags = {t.lower().replace("_", " ") for t in safety_cfg.get("school_tags", [])}
    hit_minor = {tag for tag in minor_tags if tag in tokens or tag in haystack}
    hit_school = {tag for tag in school_tags if tag in tokens or tag in haystack}
    if hit_minor:
        return True, f"rating={rating}, loli/minor tags: {sorted(hit_minor)}"
    if hit_school:
        return True, f"rating={rating}, school tags: {sorted(hit_school)}"
    return False, ""


def load_app_config() -> dict:
    cfg_file = SCRIPT_DIR / "config.json"
    if cfg_file.exists():
        try:
            payload = json.loads(cfg_file.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
    return {}


def load_llm_reverse_config() -> dict:
    return normalize_llm_reverse_config(load_app_config().get("llm_reverse") or default_llm_reverse_config())


def _resolve_haintag_root() -> Path:
    override = load_app_config().get("haintag_root", "")
    if override:
        return Path(override)
    return SCRIPT_DIR.parent / "haintag"


def _make_bridges():
    """
    返回 (metadata_bridge, tagger_bridge)。
    metadata reader: haintag 存在时用 HainTagBridge，否则 StandaloneMetadataReader。
    tagger bridge: PixAI → CL(Standalone) → None 优先链，均不可用时返回 None。
    """
    # metadata reader 保持现有逻辑
    root = _resolve_haintag_root()
    if root.exists():
        metadata_reader = HainTagBridge(root)
    else:
        metadata_reader = StandaloneMetadataReader()

    # tagger bridge: PixAI → CL → None (with runtime fallback)
    settings = load_haintag_settings()
    tagger = None
    pixai_dir = resolve_pixai_model_dir(settings)
    if pixai_dir:
        try:
            t = PixAITaggerBridge(pixai_dir)
            sample = pixai_dir / "sample.webp"
            if sample.exists():
                probe = t.predict_tags(sample)
                if probe.get("available"):
                    tagger = t
                else:
                    log.info(f"PixAI tagger 加载失败 ({probe.get('status')}), 尝试 WD14 回退")
            else:
                tagger = t
        except Exception as exc:
            log.info(f"PixAI tagger 异常 ({exc}), 尝试 WD14 回退")
    cl_dir = resolve_cl_model_dir(settings)
    if tagger is None and cl_dir:
        tagger = StandaloneTaggerBridge(cl_dir)

    return metadata_reader, tagger


MODEL_HASH_PATCHES = {
    "anima-preview3-base": "14fffe8ad5",
}


def _inject_model_hash(settings_line: str) -> str:
    if not settings_line or "Model hash:" in settings_line:
        return settings_line
    for model_name, hash_value in MODEL_HASH_PATCHES.items():
        target = f"Model: {model_name}"
        if target in settings_line:
            return settings_line.replace(
                target, f"Model hash: {hash_value}, {target}", 1
            )
    return settings_line


def setup_logging():
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logger = logging.getLogger("pixiv_uploader")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    return logger


log = logging.getLogger("pixiv_uploader")


def strip_prompts_keep_lora(image_path: Path, dest_dir: Path) -> Path:
    pil_img = Image.open(image_path)
    old_params = pil_img.info.get("parameters", "")
    if not old_params:
        dest = dest_dir / f"{image_path.stem}.png"
        pnginfo = PngImagePlugin.PngInfo()
        pil_img.save(dest, "PNG", pnginfo=pnginfo)
        return dest

    steps_idx = old_params.rfind("\nSteps:")
    if steps_idx == -1:
        steps_idx = old_params.rfind("Steps:")
    if steps_idx != -1:
        prompt_block = old_params[:steps_idx]
        settings_line = old_params[steps_idx:].strip()
    else:
        prompt_block = old_params
        settings_line = ""

    settings_line = _inject_model_hash(settings_line)

    lora_tags = _LORA_RE.findall(prompt_block)
    parts = []
    if lora_tags:
        parts.append(", ".join(f"<lora:{name}:{round(random.random(), 2)}>" for name, _ in lora_tags))
    if settings_line:
        parts.append(settings_line)

    new_params = "\n".join(parts)
    pnginfo = PngImagePlugin.PngInfo()
    if new_params:
        pnginfo.add_text("parameters", new_params)

    dest = dest_dir / f"{image_path.stem}.png"
    pil_img.save(dest, "PNG", pnginfo=pnginfo)
    return dest


LOG_KEEP_PER_GROUP = 100
LOG_KEEP_DAYS = 10


def prune_logs():
    """Keep, per group, all files newer than N days OR among newest M files."""
    if not LOG_DIR.exists():
        return
    cutoff = (datetime.now() - timedelta(days=LOG_KEEP_DAYS)).timestamp()
    groups = [
        "[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_*.log",  # main run logs
        "pixiv_failure_*.png",
        "pixiv_failure_*.html",
        "pixiv_autocomplete_probe_*.html",
    ]
    removed = 0
    for pattern in groups:
        files = sorted(LOG_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
        # Keep newest LOG_KEEP_PER_GROUP unconditionally; for the rest, keep
        # only those still within the LOG_KEEP_DAYS window.
        for old in files[LOG_KEEP_PER_GROUP:]:
            try:
                if old.stat().st_mtime >= cutoff:
                    continue
                old.unlink()
                removed += 1
            except Exception:
                pass
    if removed:
        log.info(f"清理 runtime/logs/：删除 {removed} 个（每类保留最新 {LOG_KEEP_PER_GROUP} 或 {LOG_KEEP_DAYS} 天内）")


def cleanup_done_dir():
    if not DONE_DIR.exists():
        return

    cutoff = datetime.now() - timedelta(days=DONE_DAYS)
    removed = 0

    for file in DONE_DIR.iterdir():
        if not file.is_file():
            continue
        match = re.match(r"^(\d{8})_", file.name)
        if not match:
            continue
        try:
            file_date = datetime.strptime(match.group(1), "%Y%m%d")
        except ValueError:
            continue
        if file_date < cutoff:
            file.unlink()
            removed += 1

    if removed:
        log.info(f"清理 done/ 目录：删除了 {removed} 个超过 {DONE_DAYS} 天的文件")


def move_to_done(src: Path) -> Path:
    source = Path(src)
    if not source.is_file():
        raise FileNotFoundError(f"待归档原图不存在：{source}")
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    prefix = datetime.now().strftime("%Y%m%d")
    dest = DONE_DIR / f"{prefix}_{source.name}"
    counter = 1
    while dest.exists():
        dest = DONE_DIR / f"{prefix}_{source.stem}_{counter}{source.suffix}"
        counter += 1
    shutil.move(str(source), str(dest))
    if source.exists() or not dest.is_file():
        raise OSError(f"原图归档校验失败：{source} -> {dest}")
    return dest


def make_temp_dir(prefix: str) -> Path:
    TMP_DIR.mkdir(exist_ok=True)
    stamp = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{random.randint(1000, 9999)}"
    path = TMP_DIR / f"{prefix}{stamp}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def open_civitai_browser(pw):
    context = pw.chromium.launch_persistent_context(
        str(CHROME_PROFILE_DIR),
        channel="chrome",
        headless=False,
        args=["--start-minimized"],
        ignore_default_args=["--enable-automation", "--no-sandbox"],
    )
    page = context.pages[0] if context.pages else context.new_page()
    try:
        page.goto(f"{CIVITAI_BASE}/", wait_until="commit", timeout=15000)
    except Exception:
        pass
    return context, page


def safe_goto(page, url, wait=5):
    try:
        page.goto(url, wait_until="commit", timeout=15000)
    except Exception:
        pass
    time.sleep(wait)


def ensure_on_create_page(page):
    safe_goto(page, f"{CIVITAI_BASE}/posts/create", wait=5)
    if "/login" in page.url or "signin" in page.url:
        try:
            page.evaluate("window.moveTo(100, 100); window.resizeTo(1280, 800);")
        except Exception:
            pass
        log.warning("未登录。请在浏览器里登录 Civitai，然后按 Enter 继续...")
        input()
        safe_goto(page, f"{CIVITAI_BASE}/posts/create", wait=5)
        if "/login" in page.url or "signin" in page.url:
            log.warning("仍未登录。请确认登录后再按 Enter...")
            input()
            safe_goto(page, f"{CIVITAI_BASE}/posts/create", wait=5)
        try:
            page.evaluate("window.moveTo(-32000, -32000);")
        except Exception:
            pass


def create_civitai_post(page, image_path: Path, delay: float, cancel_event=None, human: HumanSession | None = None) -> str | None:
    _raise_if_canceled(cancel_event)
    session = human if human is not None else HumanSession(page, cancel_event=cancel_event)
    ensure_on_create_page(page)

    # Wait for file input to appear — safe_goto uses wait_until="commit" which
    # only waits for response headers, so the DOM may still be loading.
    file_input = None
    for _ in range(12):
        _raise_if_canceled(cancel_event)
        loc = page.locator('input[type="file"]')
        if loc.count() > 0:
            file_input = loc.first
            break
        _sleep_with_cancel(1, cancel_event)
    if file_input is None:
        log.error("    未找到文件上传输入框（页面可能未加载完成），跳过")
        return None

    try:
        file_input.set_input_files(str(image_path))
    except Exception as exc:
        log.error(f"    上传失败: {exc}")
        log.debug(traceback.format_exc())
        return None

    publish_btn = page.locator('button:has-text("Publish")')
    enabled = False
    for _ in range(60):
        _sleep_with_cancel(2, cancel_event)
        session.mouse.idle_drift(page, cancel_event=cancel_event, probability=0.2)
        if publish_btn.count() > 0 and publish_btn.first.is_enabled():
            enabled = True
            break

    if not enabled:
        log.error("    Publish 按钮未启用（等待 120 秒），跳过")
        return None

    session.action_pause()
    try:
        session.mouse.click_locator(page, publish_btn.first, cancel_event=cancel_event)
    except InterruptedError:
        raise
    except Exception as click_exc:
        # sticky 通知栏可能遮挡按钮导致轨迹点击落空，force 跳过遮挡检查兑底
        log.debug(f"    拟人点击 Publish 失败，回退 force 点击: {type(click_exc).__name__}: {click_exc}")
        publish_btn.first.click(force=True)
    log.info("    已点击 Publish，等待跳转...")

    for _ in range(30):
        _sleep_with_cancel(2, cancel_event)
        session.mouse.idle_drift(page, cancel_event=cancel_event, probability=0.2)
        current_url = page.url
        if "/posts/create" not in current_url and "/posts/" in current_url:
            post_url = re.sub(r"/edit$", "", current_url)
            wait = delay + random.uniform(1, 3)
            _sleep_with_cancel(wait, cancel_event)
            return post_url
    log.error("    发布超时（60 秒内未跳转），跳过")
    return None


def parse_targets(raw: str) -> list[str]:
    targets = []
    for part in raw.split(","):
        item = part.strip().lower()
        if not item:
            continue
        if item not in TARGETS:
            raise ValueError(f"不支持的 targets 项：{item}")
        if item not in targets:
            targets.append(item)
    return targets or ["civitai"]


def parse_bool_flag(raw: str) -> bool:
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"无法解析布尔值: {raw}")


def render_rule_fit_report_markdown(report: dict) -> str:
    lines = [
        "# Pixiv Rule Fit Report",
        "",
        f"- Generated at: {report.get('generated_at', '')}",
        f"- Sample count: {report.get('sample_count', 0)}",
    ]
    if report.get("stage_counts"):
        lines.append(f"- Stage counts: {report.get('stage_counts', {})}")
    if report.get("tagger_status_counts"):
        lines.append(f"- Tagger statuses: {report.get('tagger_status_counts', {})}")
    lines.extend(["", "## Top Missing"])
    for item in report.get("top_missing", []):
        lines.append(f"- {item['tag']}: {item['count']}")
    lines.extend(["", "## Top Extra"])
    for item in report.get("top_extra", []):
        lines.append(f"- {item['tag']}: {item['count']}")
    lines.extend(["", "## Top Synonym Mismatch"])
    for item in report.get("top_synonym_mismatch", []):
        lines.append(f"- {item['pair']}: {item['count']}")
    lines.extend(["", "## Domain Patterns"])
    for key, stats in report.get("domain_patterns", {}).items():
        lines.append(
            f"- {key}: count={stats.get('count', 0)}, avg_missing={stats.get('avg_missing', 0.0)}, "
            f"avg_extra={stats.get('avg_extra', 0.0)}, avg_synonym={stats.get('avg_synonym', 0.0)}"
        )
    lines.extend(["", "## Age Patterns"])
    for key, stats in report.get("age_patterns", {}).items():
        lines.append(
            f"- {key}: count={stats.get('count', 0)}, avg_missing={stats.get('avg_missing', 0.0)}, "
            f"avg_extra={stats.get('avg_extra', 0.0)}, avg_synonym={stats.get('avg_synonym', 0.0)}"
        )
    lines.append("")
    return "\n".join(lines)


def create_upload_manifest(
    image_path: Path,
    targets: list[str],
    files: dict[str, Path],
    hain_bridge: HainTagBridge,
    alias_data: dict,
    popularity_data: dict,
    age_rules: dict,
    civitai_dir: Path,
    pixiv_dir: Path,
    pixiv_privacy: str,
    pixiv_allow_tag_edits: bool,
    tagger_bridge: HainTagTaggerBridge | None = None,
    jp_alias_cache: dict | None = None,
    general_jp_data: dict | None = None,
    pixiv_page=None,
    censor_engine: CensorEngine | None = None,
    censor_secondary: "DeepghsDetector | None" = None,
    censor_classes=None,
    civitai_safety_cfg: dict | None = None,
    llm_reverse_config: dict | None = None,
    llm_persona_id: str = "",
    llm_content_mode: str = "",
    ai_tags_by_platform: dict | None = None,
    watermark_service: WatermarkService | None = None,
    watermark_spec: TextWatermarkSpec | ImageWatermarkSpec | None = None,
    cancel_event=None,
    progress_callback=None,
) -> tuple[dict, bool]:
    _raise_if_canceled(cancel_event)
    _emit_progress(progress_callback, "reading_metadata", stage_progress=0.0)
    source_meta = hain_bridge.read_metadata(image_path)
    _emit_progress(progress_callback, "reading_metadata", stage_progress=1.0)

    civitai_blocked = False
    civitai_block_reason = ""
    if "civitai" in targets:
        _emit_progress(progress_callback, "safety_check", stage_progress=0.0)
        if civitai_safety_cfg:
            _raise_if_canceled(cancel_event)
            civitai_blocked, civitai_block_reason = check_civitai_safety(
                image_path, source_meta, age_rules, civitai_safety_cfg
            )
            if civitai_blocked:
                log.info(f"    Civitai 安全过滤：{civitai_block_reason}")
        _emit_progress(progress_callback, "safety_check", stage_progress=1.0)

    _emit_progress(progress_callback, "preparing_artifacts", stage_progress=0.0)
    civitai_copy = strip_prompts_keep_lora(image_path, civitai_dir) if "civitai" in targets else None
    _raise_if_canceled(cancel_event)
    # Sanitize / tag pipeline runs if ANY target needs it (PLATFORM_RULES table).
    needs_sanitize = any(PLATFORM_RULES.get(t, {}).get("needs_sanitize") for t in targets)
    needs_pixiv_payload = any(PLATFORM_RULES.get(t, {}).get("needs_copy") for t in targets)
    pixiv_clean = sanitize_image_for_pixiv(image_path, pixiv_dir) if needs_sanitize else None
    _raise_if_canceled(cancel_event)
    _emit_progress(progress_callback, "preparing_artifacts", stage_progress=1.0)

    # Run auto-censor on the sanitized pixiv copy if engine present.
    censor_result = None
    if needs_pixiv_payload:
        _emit_progress(progress_callback, "censoring", stage_progress=0.0)
    if pixiv_clean is not None and censor_engine is not None:
        log.info("    Pixiv 准备: 正在执行内容安全检查")
        _raise_if_canceled(cancel_event)
        censor_result = censor_engine.detect_and_censor(
            Path(pixiv_clean.output_path),
            output_path=Path(pixiv_clean.output_path),
            enabled_classes=censor_classes,
            secondary_detector=censor_secondary,
        )
        _raise_if_canceled(cancel_event)
        if censor_result.applied:
            log.info(f"    censor: 打码完成 — {censor_result.detail}")
        elif censor_result.status == "ok":
            log.info(f"    censor: 无需打码 — {censor_result.detail}")
        # other statuses already logged by engine
    if needs_pixiv_payload:
        _emit_progress(progress_callback, "censoring", stage_progress=1.0)

    pixiv_metadata_check = (
        hain_bridge.read_metadata(pixiv_clean.output_path) if pixiv_clean is not None
        else {"status": "skipped", "detected_types": [], "details": []}
    )
    _raise_if_canceled(cancel_event)
    if needs_pixiv_payload:
        _emit_progress(progress_callback, "tagging", stage_progress=0.0)
    if needs_pixiv_payload and tagger_bridge is not None:
        log.info("    Pixiv 准备: 正在识别图片标签")
        tagger_result = tagger_bridge.predict_tags(image_path)
        if tagger_result.get("status") not in ("ok", "disabled") and not tagger_result.get("available"):
            log.info(
                f"    本地 tagger: {tagger_result.get('status')} — 先用 prompt/文件名候选；启用 LLM 时将补充视觉标签"
            )
    else:
        tagger_result = {"available": False, "status": "disabled", "flat_tags": [], "groups": {}, "details": []}
    if needs_pixiv_payload:
        _emit_progress(progress_callback, "tagging", stage_progress=1.0)
    extra_candidates: list[str] = []
    extra_groups: dict[str, list[tuple[str, float]]] = {}
    if tagger_result.get("available"):
        extra_candidates = list(tagger_result.get("flat_tags", []))
        for category, entries in (tagger_result.get("groups") or {}).items():
            extra_groups[category] = [(tag, float(score)) for tag, score in entries]
    if needs_pixiv_payload:
        log.info("    Pixiv 准备: 正在整理标签与分级")
        _emit_progress(progress_callback, "organizing_tags", stage_progress=0.0)
    pixiv_payload = (
        build_pixiv_payload(
            image_path=image_path,
            metadata_info=source_meta,
            alias_data=alias_data,
            popularity_data=popularity_data,
            age_rules=age_rules,
            extra_candidates=extra_candidates,
            extra_groups=extra_groups,
            jp_alias_cache=jp_alias_cache if jp_alias_cache is not None else {},
            general_jp_data=general_jp_data or {},
            pixiv_page=pixiv_page,
            live_lookup=True,
            live_jp_lookup=True,
            include_ai_art=(ai_tags_by_platform or {}).get("pixiv", True),
        )
        if needs_pixiv_payload else None
    )
    _raise_if_canceled(cancel_event)
    if needs_pixiv_payload:
        _emit_progress(progress_callback, "organizing_tags", stage_progress=1.0)

    llm_reverse_result = {"enabled": False, "status": "disabled", "error": ""}
    if pixiv_payload is not None:
        pixiv_payload["privacy"] = pixiv_privacy
        pixiv_payload["allow_tag_edits"] = pixiv_allow_tag_edits
        if censor_result is not None and censor_result.applied:
            if pixiv_payload.get("age_restriction") not in {"r18", "r18g"}:
                log.info("    censor: 检测到露出，强制 age_restriction=r18")
                force_pixiv_age_restriction(pixiv_payload, "r18")
        _rating_scores = tagger_result.get("rating_scores") or {}
        if _rating_scores:
            _best_rating = max(_rating_scores, key=_rating_scores.get)
            _best_score = _rating_scores[_best_rating]
            if _best_rating in ("explicit", "questionable") and _best_score > 0.5:
                if pixiv_payload.get("age_restriction") not in ("r18", "r18g"):
                    log.info(f"    tagger rating: {_best_rating}={_best_score:.2f}，升级 age_restriction → r18")
                    force_pixiv_age_restriction(pixiv_payload, "r18")
        if llm_reverse_config and llm_reverse_config.get("enabled"):
            # Skip LLM if no target consumes copy (e.g. --targets civitai)
            if not _targets_need_copy(targets):
                llm_reverse_result = {
                    "enabled": True,
                    "status": "skipped_no_target_needs",
                    "persona_id": llm_persona_id,
                    "platform": "",
                    "content_mode": "",
                    "fields": {},
                    "error": "no target requires copy (civitai-only or similar)",
                }
                log.info("    LLM 反推: 跳过（当前 targets 都不需要文案）")
            else:
                _, effective_mode = resolve_persona(llm_reverse_config, llm_persona_id, llm_content_mode)
                image_age = (pixiv_payload or {}).get("age_restriction", "all_ages")
                if not content_mode_can_handle_age(effective_mode, image_age):
                    llm_reverse_result = {
                        "enabled": True,
                        "status": "skipped_sfw_mode",
                        "persona_id": llm_persona_id,
                        "content_mode": effective_mode,
                        "fields": {},
                        "error": f"content_mode={effective_mode} does not cover image age={image_age}",
                    }
                    log.info(
                        f"    LLM 反推: 跳过——content_mode={effective_mode}，图分级 {image_age}"
                    )
                else:
                    _raise_if_canceled(cancel_event)
                    _ctx = _build_llm_extra_context(pixiv_payload, source_meta=source_meta)
                    log.info(f"    LLM 反推: extra_context={_ctx!r}")
                    log.info("    LLM 反推: 正在生成文案")
                    _emit_progress(progress_callback, "generating_copy", stage_progress=0.0)
                    llm_reverse_result = infer_image_copy(
                        image_path=Path(pixiv_clean.output_path) if pixiv_clean else image_path,
                        config=llm_reverse_config,
                        persona_id=llm_persona_id,
                        content_mode=llm_content_mode,
                        extra_context=_ctx,
                        cancel_event=cancel_event,
                        event_callback=lambda event, details: _emit_llm_retry_progress(
                            progress_callback, event, details
                        ),
                    )
                    if llm_reverse_result.get("status") == "ok":
                        llm_keywords = _extract_llm_visual_keywords(llm_reverse_result)
                        keyword_tagging = {
                            "status": "no_keywords",
                            "candidate_count": len(llm_keywords),
                            "added_count": 0,
                            "added_tags": [],
                            "final_tag_count": len(pixiv_payload.get("final_tags") or []),
                        }
                        if llm_keywords:
                            previous_tags = list(pixiv_payload.get("final_tags") or [])
                            previous_age = str(pixiv_payload.get("age_restriction") or "all_ages")
                            try:
                                rebuilt_payload = build_pixiv_payload(
                                    image_path=image_path,
                                    metadata_info=source_meta,
                                    alias_data=alias_data,
                                    popularity_data=popularity_data,
                                    age_rules=age_rules,
                                    extra_candidates=extra_candidates,
                                    extra_groups=_merge_llm_keywords_into_groups(extra_groups, llm_keywords),
                                    jp_alias_cache=jp_alias_cache if jp_alias_cache is not None else {},
                                    general_jp_data=general_jp_data or {},
                                    pixiv_page=pixiv_page,
                                    live_lookup=True,
                                    live_jp_lookup=True,
                                    include_ai_art=(ai_tags_by_platform or {}).get("pixiv", True),
                                )
                                rebuilt_payload["privacy"] = pixiv_privacy
                                rebuilt_payload["allow_tag_edits"] = pixiv_allow_tag_edits
                                if previous_age in {"r18", "r18g"}:
                                    force_pixiv_age_restriction(rebuilt_payload, previous_age)
                                previous_tag_set = set(previous_tags)
                                added_tags = [
                                    tag for tag in rebuilt_payload.get("final_tags") or []
                                    if tag not in previous_tag_set
                                ]
                                pixiv_payload = rebuilt_payload
                                keyword_tagging.update(
                                    status="applied",
                                    added_count=len(added_tags),
                                    added_tags=added_tags,
                                    final_tag_count=len(pixiv_payload.get("final_tags") or []),
                                )
                                log.info(
                                    "    LLM 视觉标签: 候选 %s，新增 %s，最终 %s 个 — %s",
                                    len(llm_keywords),
                                    len(added_tags),
                                    len(pixiv_payload.get("final_tags") or []),
                                    ", ".join(pixiv_payload.get("final_tags") or []),
                                )
                            except Exception as exc:
                                keyword_tagging.update(
                                    status="failed",
                                    error=f"{type(exc).__name__}: {exc}",
                                )
                                log.warning("    LLM 视觉标签整理失败，保留原标签: %s", exc)
                        else:
                            log.info("    LLM 视觉标签: 模型未返回 keywords，保留原标签")
                        llm_reverse_result["tagging"] = keyword_tagging
                        apply_llm_result_to_pixiv_payload(pixiv_payload, llm_reverse_result)
                        log.info(
                            f"    LLM 反推: 已生成标题/简介/视觉标签 ({llm_reverse_result.get('content_mode', 'sfw')})"
                        )
                    else:
                        log.warning(
                            f"    LLM 反推: {llm_reverse_result.get('status')} — {llm_reverse_result.get('error', '')}"
                        )
                    _emit_progress(
                        progress_callback,
                        "generating_copy",
                        stage_progress=1.0 if llm_reverse_result.get("status") == "ok" else 0.0,
                    )
            _raise_if_canceled(cancel_event)

    if needs_pixiv_payload:
        _emit_progress(progress_callback, "watermarking", stage_progress=0.0)
    watermark_failed = False
    watermark_error = ""
    watermark_result = {
        "renderer": watermark_spec.renderer if watermark_spec is not None else "text",
        "enabled": bool(watermark_spec and watermark_spec.enabled),
        "applied": False,
        "output_path": str(pixiv_clean.output_path) if pixiv_clean is not None else "",
        "status": "disabled",
    }
    if watermark_spec is not None and watermark_spec.enabled:
        if pixiv_clean is None:
            watermark_result["status"] = "skipped_no_sanitized_artifact"
        elif watermark_service is None:
            watermark_failed = True
            watermark_error = "watermark service unavailable"
        else:
            try:
                watermark_result = {
                    "enabled": True,
                    "status": "ok",
                    **watermark_service.render(Path(pixiv_clean.output_path), watermark_spec).to_dict(),
                }
                label = "图片水印" if watermark_spec.renderer == "image" else "文字水印"
                log.info(f"    {label}: 已写入无元数据发布副本")
            except WatermarkError as exc:
                watermark_failed = True
                watermark_error = str(exc)
        if watermark_failed:
            watermark_result.update({"status": "failed", "error": watermark_error})
            label = "图片水印" if watermark_spec is not None and watermark_spec.renderer == "image" else "文字水印"
            log.error(f"    {label}失败: {watermark_error}")
    _raise_if_canceled(cancel_event)
    if needs_pixiv_payload:
        _emit_progress(
            progress_callback,
            "watermarking",
            stage_progress=0.0 if watermark_failed else 1.0,
        )

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_path": str(image_path),
        "targets": targets,
        "dry_run": False,
        "status_by_target": {
            target: (
                "failed"
                if watermark_failed and PLATFORM_RULES.get(target, {}).get("needs_sanitize")
                else "pending"
            )
            for target in targets
        },
        "errors": [f"Watermark failed: {watermark_error}"] if watermark_failed else [],
        "watermark": watermark_result,
        "civitai": {
            "clean_copy_path": str(civitai_copy) if civitai_copy else "",
            "post_url": "",
            "skip_reason": civitai_block_reason,
        },
        "pixiv": {
            "clean_copy_path": str(pixiv_clean.output_path) if pixiv_clean else "",
            "metadata_check": {
                "status": pixiv_metadata_check.get("status", "skipped"),
                "detected_types": pixiv_metadata_check.get("detected_types", []),
                "details": pixiv_metadata_check.get("details", []),
            },
            "raw_candidates": pixiv_payload["raw_candidates"] if pixiv_payload else [],
            "metadata_entity_hits": pixiv_payload["metadata_entity_hits"] if pixiv_payload else [],
            "popularity_decisions": pixiv_payload["popularity_decisions"] if pixiv_payload else [],
            "final_tags": pixiv_payload["final_tags"] if pixiv_payload else [],
            "entity_tags": pixiv_payload.get("entity_tags", []) if pixiv_payload else [],
            "rejected_tags": pixiv_payload["rejected_tags"] if pixiv_payload else [],
            "domain": pixiv_payload["domain"] if pixiv_payload else "",
            "title_ja": pixiv_payload["title_ja"] if pixiv_payload else "",
            "title_zh": pixiv_payload["title_zh"] if pixiv_payload else "",
            "caption_ja": pixiv_payload["caption_ja"] if pixiv_payload else "",
            "caption_zh": pixiv_payload["caption_zh"] if pixiv_payload else "",
            "age_restriction": pixiv_payload["age_restriction"] if pixiv_payload else "",
            "ai_generated": pixiv_payload["ai_generated"] if pixiv_payload else False,
            "privacy": pixiv_privacy,
            "allow_tag_edits": pixiv_allow_tag_edits,
            "post_url": "",
            "llm_reverse": llm_reverse_result,
            "tagger": {
                "status": tagger_result.get("status", "disabled"),
                "available": tagger_result.get("available", False),
                "top_tags": list(tagger_result.get("flat_tags", []))[:30],
                "tagger_type": tagger_result.get("tagger_type", "cl"),
                "details": tagger_result.get("details", []),
            },
            "censor": censor_result.to_dict() if censor_result is not None else {"status": "disabled", "applied": False},
        },
        "source_metadata": {
            "status": source_meta.get("status", "unknown"),
            "detected_types": source_meta.get("detected_types", []),
            "details": source_meta.get("details", []),
        },
    }

    pixiv_ready = not watermark_failed
    if "pixiv" in targets:
        status = pixiv_metadata_check.get("status")
        if watermark_failed:
            manifest["status_by_target"]["pixiv"] = "failed"
        elif not pixiv_metadata_check.get("available", False):
            # Validator unavailable. sanitize_image_for_pixiv already strips
            # metadata with PIL, so proceeding is safe.
            # Only warn when haintag root exists but the module can't be loaded
            # (unexpected). When haintag is simply not installed, stay silent.
            haintag_root = _resolve_haintag_root()
            if haintag_root.exists():
                log.warning("    metadata validator unavailable (haintag found but import failed); continuing")
        elif status != "clean":
            pixiv_ready = False
            manifest["status_by_target"]["pixiv"] = "failed"
            manifest["errors"].append(
                f"Pixiv clean copy metadata validation failed: {status} {pixiv_metadata_check.get('details', [])}"
            )
    if civitai_blocked:
        manifest["status_by_target"]["civitai"] = "skipped_civitai_safety"
    append_validation_case(image_path, files["validation"], manifest)
    return manifest, pixiv_ready


def _select_by_sort(images: list, sort_mode: str, count: int) -> list:
    n = min(count, len(images))
    if sort_mode == "name_asc":
        return sorted(images, key=lambda f: f.name.lower())[:n]
    if sort_mode == "name_desc":
        return sorted(images, key=lambda f: f.name.lower(), reverse=True)[:n]
    if sort_mode == "time_asc":
        return sorted(images, key=lambda f: f.stat().st_mtime)[:n]
    if sort_mode == "time_desc":
        return sorted(images, key=lambda f: f.stat().st_mtime, reverse=True)[:n]
    return random.sample(images, n)


def _pixiv_retry_decision(
    result: PixivPostResult,
    steps: list,
    *,
    attempt: int,
    max_retries: int,
) -> str:
    submitted = result.maybe_posted or any(
        getattr(step, "name", "") == "publish_click" and getattr(step, "ok", False)
        for step in steps
    )
    if submitted:
        return "stop_uncertain"
    if result.error_code == "pixiv_rate_limited" or "captcha" in result.error_code:
        return "stop_batch"
    if result.batch_fatal:
        return "stop_batch"
    if attempt < max_retries:
        return "retry_safe"
    return "stop"


def _acquire_pixiv_profile_for_task(*, cancel_event=None, interaction_callback=None):
    _raise_if_canceled(cancel_event)
    try:
        return PIXIV_SESSION.acquire("publishing")
    except PixivProfileInUseError:
        if interaction_callback is not None:
            interaction_callback(None)
        raise
    except OSError as exc:
        raise PixivFlowError(
            "pixiv_session_state_unavailable",
            f"Pixiv 会话状态无法读取或写入：{type(exc).__name__}: {exc}",
        ) from exc


def _validate_confirmed_pixiv_url(url: str) -> str:
    value = str(url or "").strip()
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    artwork_id = extract_artwork_id(value)
    if not artwork_id or not (host == "pixiv.net" or host.endswith(".pixiv.net")):
        raise PixivFlowError(
            "pixiv_success_url_invalid",
            f"Pixiv 成功回执缺少有效作品 URL：{value or '<empty>'}",
            maybe_posted=True,
        )
    return f"https://www.pixiv.net/artworks/{artwork_id}"


_ITEM_SUCCESS_TARGET_STATUSES = {"success", "skipped_already_done", "skipped_civitai_safety", "dry_run"}


def _task_target_results(manifest: dict, targets: list[str]) -> dict[str, dict[str, str]]:
    results: dict[str, dict[str, str]] = {}
    statuses = manifest.get("status_by_target") if isinstance(manifest.get("status_by_target"), dict) else {}
    for target in targets:
        payload = manifest.get(target) if isinstance(manifest.get(target), dict) else {}
        status = str(statuses.get(target) or "failed")
        error_code = str(payload.get("error_code") or "")
        if not error_code:
            if status == "failed":
                error_code = f"{target}_upload_failed"
            elif status == "canceled":
                error_code = "task_canceled"
            elif status == "maybe_posted":
                error_code = "pixiv_submit_unconfirmed"
        results[target] = {
            "status": status,
            "post_url": str(payload.get("post_url") or "") if status in {"success", "skipped_already_done"} else "",
            "error_code": error_code,
        }
    return results


def _task_item_outcome(
    manifest: dict,
    targets: list[str],
    *,
    source_available: bool,
    canceled: bool = False,
) -> dict:
    target_results = _task_target_results(manifest, targets)
    status_values = [detail["status"] for detail in target_results.values()]
    statuses = set(status_values)
    finalization = manifest.get("finalization") if isinstance(manifest.get("finalization"), dict) else {}
    if finalization.get("error_code") == "source_archive_failed":
        status = "failed"
        reason_code = "source_archive_failed"
    elif "maybe_posted" in statuses:
        status = "uncertain"
        reason_code = next(
            (detail["error_code"] for detail in target_results.values() if detail["status"] == "maybe_posted"),
            "pixiv_submit_unconfirmed",
        )
    elif canceled or "canceled" in statuses:
        status = "canceled"
        reason_code = "task_canceled"
    else:
        successful = sum(value in _ITEM_SUCCESS_TARGET_STATUSES for value in status_values)
        if status_values and successful == len(status_values):
            if bool(manifest.get("dry_run")) or finalization.get("status") == "archived":
                status = "succeeded"
                reason_code = ""
            else:
                status = "failed"
                reason_code = "source_archive_failed"
        elif successful:
            status = "partial"
            reason_code = next(
                (detail["error_code"] for detail in target_results.values() if detail["error_code"]),
                "partial_target_failure",
            )
        else:
            status = "failed"
            reason_code = next(
                (detail["error_code"] for detail in target_results.values() if detail["error_code"]),
                "publishing_failed",
            )
    return {
        "item_status": status,
        "retryable": bool(source_available and status in {"partial", "failed", "canceled"}),
        "reason_code": reason_code,
        "targets": target_results,
    }


def cmd_upload(args):
    progress_callback = getattr(args, "progress_callback", None)
    interaction_callback = getattr(args, "interaction_callback", None)
    _emit_progress(progress_callback, "initializing", stage_progress=0.1)
    files = ensure_runtime_files(SCRIPT_DIR)
    alias_data = load_json(files["aliases"], {})
    popularity_data = load_json(files["popularity"], {})
    age_rules = load_json(files["age_rules"], {})
    _emit_progress(progress_callback, "initializing", stage_progress=0.3)
    hain_bridge, tagger_bridge = _make_bridges()
    _tagger_probe = getattr(tagger_bridge, "_model_dir", None) or getattr(tagger_bridge, "_dir", None)
    requested_targets = {
        part.strip().lower()
        for part in str(getattr(args, "targets", "")).split(",")
        if part.strip()
    }
    if "pixiv" in requested_targets:
        if not _tagger_probe:
            log.info(
                "本地 tagger: 未配置，将先用 prompt/文件名候选；启用 LLM 时会补充视觉标签"
                "（本地模型可在 web 设置面板或 launcher [6] 配置）"
            )
    jp_alias_cache = load_json(files["jp_aliases"], {})
    general_jp_data = load_json(files["general_jp"], {})
    danbooru_jp_map = load_json(files["danbooru_jp"], {})
    if danbooru_jp_map:
        general_jp_data["_danbooru_map"] = danbooru_jp_map
        log.info(f"Danbooru→JP 词典已加载: {len(danbooru_jp_map)} 条")
    civitai_safety_cfg = load_json(files["civitai_safety"], {})
    llm_reverse_config = load_llm_reverse_config()
    _emit_progress(progress_callback, "initializing", stage_progress=0.5)
    llm_reverse_enabled = bool(getattr(args, "llm_reverse", False)) and llm_reverse_config.get("enabled")
    if getattr(args, "llm_reverse", False) and not llm_reverse_enabled:
        log.warning("LLM 反推: 已请求但未启用或配置不完整，将跳过")

    no_ai_tags = getattr(args, "no_ai_tags", None) or ""
    requested_ai_tags = getattr(args, "ai_tags_by_platform", None) or {}
    if no_ai_tags:
        skip = {part.strip().lower() for part in no_ai_tags.split(",") if part.strip()}
        requested_ai_tags["pixiv"] = not ({"all", "pixiv"} & skip)
    args.ai_tags_by_platform = {"pixiv": bool(requested_ai_tags.get("pixiv", True))}

    UPLOAD_DIR.mkdir(exist_ok=True)
    DONE_DIR.mkdir(exist_ok=True)

    targets = parse_targets(args.targets)
    watermark_service, watermark_spec = _load_watermark_for_targets(targets)
    _emit_progress(progress_callback, "initializing", stage_progress=0.65)

    all_images = sorted(
        file for file in UPLOAD_DIR.iterdir()
        if file.is_file() and file.suffix.lower() in IMAGE_EXTENSIONS
    )
    selected_names = [str(name) for name in (getattr(args, "files", None) or [])]
    preselected_images: list[Path] | None = None
    if selected_names:
        upload_by_name = {file.name.lower(): file for file in all_images}
        _emit_progress(
            progress_callback,
            "items_registered",
            items=[
                {"name": name, "retryable": name.lower() in upload_by_name}
                for name in selected_names
            ],
            targets=targets,
        )
        missing_names = [name for name in selected_names if name.lower() not in upload_by_name]
        if missing_names:
            missing_lookup = {name.lower() for name in missing_names}
            failed_so_far = 0
            for item_index, name in enumerate(selected_names, 1):
                if name.lower() not in missing_lookup:
                    continue
                failed_so_far += 1
                _emit_progress(
                    progress_callback,
                    "item_complete",
                    item_index=item_index,
                    item_name=name,
                    item_status="failed",
                    retryable=False,
                    reason_code="source_file_missing",
                    targets={
                        target: {
                            "status": "failed",
                            "post_url": "",
                            "error_code": "source_file_missing",
                        }
                        for target in targets
                    },
                    total=len(selected_names),
                    current=failed_so_far,
                    succeeded=0,
                    failed=failed_so_far,
                    canceled=0,
                )
            log.error(f"指定文件已不在 upload/，任务未开始: {missing_names}")
            return {
                "status": "missing_files",
                "reason_code": "source_file_missing",
                "total": len(selected_names),
                "processed": len(missing_names),
                "succeeded": 0,
                "failed": len(missing_names),
                "canceled": 0,
                "unprocessed": len(selected_names) - len(missing_names),
                "missing_files": missing_names,
            }
        preselected_images = [upload_by_name[name.lower()] for name in selected_names]
    elif not all_images:
        log.info(f"upload/ 目录没有图片。\n  {UPLOAD_DIR}")
        return {
            "status": "no_work",
            "reason_code": "no_images_available",
            "total": 0,
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "canceled": 0,
            "unprocessed": 0,
        }

    # Auto-censor is opt-in through models/auto_censor.pt. Runtime tunables
    # only affect Pixiv's sanitized publishing copy.
    censor_engine = None
    censor_secondary = None
    censor_classes = DEFAULT_CENSOR_CLASSES
    needs_pixiv_pipeline = any(PLATFORM_RULES.get(t, {}).get("needs_sanitize") for t in targets)
    if needs_pixiv_pipeline:
        model_path = SCRIPT_DIR / "models" / "auto_censor.pt"
        if model_path.exists():
            cfg = load_json(files["censor_config"], {})
            mode = cfg.get("mode", "mosaic")
            conf = float(cfg.get("conf_threshold", 0.55))
            bar_count = int(cfg.get("bar_count", 4))
            classes_spec = cfg.get("enabled_classes", "")
            if isinstance(classes_spec, list):
                classes_spec = ",".join(str(x) for x in classes_spec)
            censor_classes = parse_class_set(classes_spec)
            box_expand_raw = cfg.get("box_expand", {})
            box_expand = {
                CENSOR_CLASS_BY_NAME[k]: float(v)
                for k, v in box_expand_raw.items()
                if k in CENSOR_CLASS_BY_NAME
            }
            box_expand_default = float(cfg.get("box_expand_default", 0.0))
            class_thresholds_raw = cfg.get("class_thresholds", {})
            class_thresholds = {
                CENSOR_CLASS_BY_NAME[k]: float(v)
                for k, v in class_thresholds_raw.items()
                if k in CENSOR_CLASS_BY_NAME
            }
            censor_engine = CensorEngine(
                model_path,
                conf_threshold=conf,
                mode=mode,
                bar_count=bar_count,
                box_expand=box_expand,
                box_expand_default=box_expand_default,
                class_thresholds=class_thresholds,
            )
            if cfg.get("secondary_enabled", False):
                sec_model = cfg.get("secondary_model") or None
                sec_conf = float(cfg.get("secondary_conf", 0.25))
                sec_level = str(cfg.get("secondary_level", "s"))
                censor_secondary = DeepghsDetector(
                    model_name=sec_model,
                    conf=sec_conf,
                    level=sec_level,
                )
            log.info(
                f"自动打码: 已启用 (mode={mode}, conf={conf}, classes={sorted(censor_classes)}"
                f", expand_default={box_expand_default}, class_thresholds={class_thresholds}"
                f", secondary={'deepghs' if censor_secondary else 'off'})"
            )
        else:
            log.info("自动打码: 未启用（如需放模型到 models/auto_censor.pt + pip install ultralytics opencv-python）")
    _emit_progress(progress_callback, "initializing", stage_progress=0.8)
    sort_mode = getattr(args, "sort", "random")
    if preselected_images is not None:
        image_files = preselected_images
    else:
        requested = max(0, int(getattr(args, "count", 0) or 0))
        count = min(requested, len(all_images)) if requested else min(random.randint(1, 5), len(all_images))
        image_files = _select_by_sort(all_images, sort_mode, count)
        _emit_progress(
            progress_callback,
            "items_registered",
            items=[{"name": image.name, "retryable": True} for image in image_files],
            targets=targets,
        )

    image_queue = [(image, targets) for image in image_files]
    _emit_progress(
        progress_callback,
        "initializing",
        stage_progress=1.0,
        total=len(image_queue),
    )
    all_processed_targets = list(targets)
    log.info(f"upload/ {len(all_images)} 张，本次处理 {len(image_files)} 张；目标：{targets}\n")

    temp_dir = make_temp_dir("civitai_upload_")
    civitai_dir = temp_dir / "civitai"
    pixiv_dir = temp_dir / "pixiv"
    civitai_dir.mkdir(exist_ok=True)
    pixiv_dir.mkdir(exist_ok=True)

    civitai_context = pixiv_context = None
    civitai_page = pixiv_page = None
    success_count = 0
    fail_count = 0
    canceled_count = 0
    consecutive_failures = 0
    abort_threshold = max(1, int(args.abort_after_failures))
    playwright = None
    target_success_counts = {target: 0 for target in all_processed_targets}
    target_fail_counts = {target: 0 for target in all_processed_targets}
    target_canceled_counts = {target: 0 for target in all_processed_targets}
    pixiv_lease = None
    pixiv_next_post_at = 0.0
    pixiv_batch_fatal = False
    pixiv_batch_error_code = ""
    batch_stop_reason = ""
    civitai_human: HumanSession | None = None
    pixiv_human: HumanSession | None = None
    pixiv_posts_attempted = 0
    last_pixiv_artwork_url = ""

    try:
        if not args.dry_run and "pixiv" in targets:
            pixiv_lease = _acquire_pixiv_profile_for_task(
                cancel_event=getattr(args, "cancel_event", None),
                interaction_callback=interaction_callback,
            )
            if interaction_callback is not None:
                interaction_callback(None)
        if not args.dry_run and targets:
            playwright = sync_playwright().start()
        if playwright is not None and "civitai" in targets:
            civitai_context, civitai_page = open_civitai_browser(playwright)

        _cancel_ev = getattr(args, "cancel_event", None)
        if civitai_page is not None:
            civitai_human = HumanSession(civitai_page, cancel_event=_cancel_ev)
        for index, (orig_path, effective_targets) in enumerate(image_queue, 1):
            if _cancel_ev and _cancel_ev.is_set():
                batch_stop_reason = "task_canceled"
                log.info("收到取消信号，停止上传")
                break
            def report_image(stage: str, **details) -> None:
                details.setdefault("item_index", index)
                details.setdefault("item_name", orig_path.name)
                details.setdefault("total", len(image_queue))
                details.setdefault("current", success_count + fail_count)
                details.setdefault("succeeded", success_count)
                details.setdefault("failed", fail_count)
                _emit_progress(progress_callback, stage, **details)

            log.info(f"[{index}/{len(image_queue)}] {orig_path.name}")
            if "pixiv" in effective_targets:
                log.info("    Pixiv 准备: 正在处理图片、标签和文案，完成后自动打开投稿页")
            prior_successes = find_target_successes(files["manifests"], orig_path)
            skip_targets = {t for t in effective_targets if t in prior_successes}
            if skip_targets:
                uncertain_targets = sorted(t for t in skip_targets if not prior_successes.get(t))
                confirmed_targets = sorted(t for t in skip_targets if prior_successes.get(t))
                if confirmed_targets:
                    log.info(f"    跳过已成功目标: {confirmed_targets}（继承历史 post_url）")
                if uncertain_targets:
                    log.warning(
                        f"    跳过结果不确定的历史投稿: {uncertain_targets}"
                        "（投稿按钮已点击，禁止自动重试）"
                    )
            manifest_path = create_manifest_path(files["manifests"], orig_path)
            manifest, pixiv_ready = create_upload_manifest(
                image_path=orig_path,
                targets=effective_targets,
                files=files,
                hain_bridge=hain_bridge,
                alias_data=alias_data,
                popularity_data=popularity_data,
                age_rules=age_rules,
                civitai_dir=civitai_dir,
                pixiv_dir=pixiv_dir,
                pixiv_privacy=args.pixiv_privacy,
                pixiv_allow_tag_edits=parse_bool_flag(args.pixiv_allow_tag_edits),
                tagger_bridge=tagger_bridge,
                jp_alias_cache=jp_alias_cache,
                general_jp_data=general_jp_data,
                pixiv_page=None,
                censor_engine=censor_engine,
                censor_secondary=censor_secondary,
                censor_classes=censor_classes,
                civitai_safety_cfg=civitai_safety_cfg,
                llm_reverse_config=llm_reverse_config if llm_reverse_enabled else None,
                llm_persona_id=getattr(args, "llm_persona", ""),
                llm_content_mode=getattr(args, "llm_content_mode", ""),
                ai_tags_by_platform=getattr(args, "ai_tags_by_platform", None),
                watermark_service=watermark_service,
                watermark_spec=watermark_spec,
                cancel_event=_cancel_ev,
                progress_callback=report_image,
            )
            _raise_if_canceled(_cancel_ev)
            # Persist any new JP aliases learned during this image's payload build
            save_json(files["jp_aliases"], jp_alias_cache)
            tagger_status = manifest.get("pixiv", {}).get("tagger", {}).get("status", "disabled")
            if "pixiv" in effective_targets and tagger_status not in {"ok", "disabled", "haintag_root_missing", "model_dir_not_configured", "onnxruntime_not_installed"}:
                log.warning(
                    f"    本地 tagger 不可用: {tagger_status}"
                    "（继续上传；先用 prompt/文件名候选，启用 LLM 时补充视觉标签）"
                )
            manifest["dry_run"] = bool(args.dry_run)
            report_image("saving_manifest", stage_progress=0.0)
            write_manifest(manifest_path, manifest)
            if "pixiv" in effective_targets:
                save_json(files["popularity"], popularity_data)
            report_image("saving_manifest", stage_progress=1.0)

            if args.dry_run:
                for target in effective_targets:
                    if manifest["status_by_target"].get(target) == "pending":
                        manifest["status_by_target"][target] = "dry_run"
                write_manifest(manifest_path, manifest)
                log.info("    dry-run 完成，未执行发布。")
                report_image("finalizing_image", stage_progress=1.0)
                success_count += 1
                report_image(
                    "item_complete",
                    current=index,
                    succeeded=success_count,
                    failed=fail_count,
                    canceled=canceled_count,
                    **_task_item_outcome(
                        manifest,
                        effective_targets,
                        source_available=orig_path.exists(),
                    ),
                )
                continue

            all_succeeded = True
            cancel_requested = False

            if "civitai" in effective_targets:
                report_image("publishing_civitai", stage_progress=0.0)
                if "civitai" in skip_targets:
                    inherited_url = prior_successes["civitai"]
                    manifest["civitai"]["post_url"] = inherited_url
                    manifest["status_by_target"]["civitai"] = "skipped_already_done"
                    report_image("publishing_civitai", stage_progress=1.0)
                    log.info(f"    Civitai 已发过，跳过: {inherited_url}")
                elif manifest["status_by_target"].get("civitai") == "skipped_civitai_safety":
                    reason = manifest["civitai"].get("skip_reason", "")
                    report_image("publishing_civitai", stage_progress=1.0)
                    log.info(f"    Civitai 安全跳过: {reason}")
                else:
                    civitai_copy = Path(manifest["civitai"]["clean_copy_path"])
                    try:
                        civitai_url = create_civitai_post(civitai_page, civitai_copy, args.delay, cancel_event=_cancel_ev, human=civitai_human)
                    except InterruptedError:
                        cancel_requested = True
                        manifest["civitai"]["error_code"] = "task_canceled"
                        civitai_url = None
                    except Exception as exc:
                        manifest["civitai"]["error_code"] = str(
                            getattr(exc, "code", "civitai_upload_failed")
                        )
                        log.error(f"    Civitai 发布异常: {exc}")
                        log.debug(traceback.format_exc())
                        civitai_url = None
                    if civitai_url:
                        manifest["civitai"]["post_url"] = civitai_url
                        manifest["status_by_target"]["civitai"] = "success"
                        report_image("publishing_civitai", stage_progress=1.0)
                        log.info(f"    Civitai 发布成功: {civitai_url}")
                    elif cancel_requested and manifest["status_by_target"].get("civitai") == "pending":
                        manifest["status_by_target"]["civitai"] = "canceled"
                        manifest["civitai"]["error_code"] = "task_canceled"
                        manifest["errors"].append("Civitai upload canceled")
                        all_succeeded = False
                    else:
                        manifest["status_by_target"]["civitai"] = "failed"
                        manifest["civitai"]["error_code"] = str(
                            manifest["civitai"].get("error_code") or "civitai_upload_failed"
                        )
                        manifest["errors"].append("Civitai upload failed")
                        all_succeeded = False
                report_image(
                    "item_target",
                    targets=_task_target_results(manifest, ["civitai"]),
                )

            if "pixiv" in effective_targets:
                pixiv_browser_error = ""
                pixiv_browser_error_code = ""
                if (
                    "pixiv" not in skip_targets
                    and pixiv_ready
                    and not cancel_requested
                ):
                    normal_interval_remaining = pixiv_next_post_at - time.monotonic()
                    if normal_interval_remaining > 0:
                        try:
                            _sleep_with_cancel(normal_interval_remaining, _cancel_ev)
                        except InterruptedError:
                            cancel_requested = True
                    report_image("opening_pixiv", stage_progress=0.0)
                    if pixiv_page is None and not cancel_requested:
                        log.info("    Pixiv 准备完成: 正在打开浏览器并填写投稿表单")
                        try:
                            pixiv_context, pixiv_page = open_pixiv_browser(playwright)
                        except Exception as exc:
                            pixiv_browser_error = str(exc)
                            pixiv_browser_error_code = str(getattr(exc, "code", "pixiv_browser_start_failed"))
                            try:
                                PIXIV_SESSION.update_verified(
                                    "error",
                                    error_code=pixiv_browser_error_code,
                                    error=pixiv_browser_error,
                                )
                            except OSError as state_exc:
                                log.error(
                                    "    无法持久化 Pixiv 浏览器错误状态 "
                                    "[pixiv_session_state_unavailable]："
                                    f"{type(state_exc).__name__}: {state_exc}"
                                )
                                pixiv_browser_error_code = "pixiv_session_state_unavailable"
                                pixiv_browser_error = (
                                    f"{pixiv_browser_error}; session state: "
                                    f"{type(state_exc).__name__}: {state_exc}"
                                )
                            pixiv_batch_fatal = True
                            pixiv_batch_error_code = pixiv_browser_error_code
                            log.error(f"    Pixiv 浏览器启动失败: {pixiv_browser_error}")
                            log.debug(traceback.format_exc())
                    if pixiv_page is not None and not pixiv_browser_error and not cancel_requested:
                        report_image("opening_pixiv", stage_progress=1.0, activity={})
                        if pixiv_human is None:
                            pixiv_human = HumanSession(pixiv_page, cancel_event=_cancel_ev)
                            try:
                                warm_up_pixiv_session(pixiv_page, pixiv_human, cancel_event=_cancel_ev)
                            except InterruptedError:
                                cancel_requested = True
                            except PixivFlowError as warm_exc:
                                # 热身期间浏览器被关闭，按浏览器不可用处理
                                pixiv_browser_error = str(warm_exc)
                                pixiv_browser_error_code = str(getattr(warm_exc, "code", "pixiv_browser_closed"))
                                log.error(f"    Pixiv 会话热身中断: {pixiv_browser_error}")

                if "pixiv" in skip_targets:
                    inherited_url = prior_successes["pixiv"]
                    manifest["pixiv"]["post_url"] = inherited_url
                    if inherited_url:
                        manifest["status_by_target"]["pixiv"] = "skipped_already_done"
                        log.info(f"    Pixiv 已发过，跳过: {inherited_url}")
                    else:
                        manifest["status_by_target"]["pixiv"] = "maybe_posted"
                        manifest["errors"].append(
                            "Pixiv previous submission was uncertain; automatic retry blocked"
                        )
                        pixiv_batch_fatal = True
                        pixiv_batch_error_code = "pixiv_previous_submission_uncertain"
                        all_succeeded = False
                        log.error("    Pixiv 历史投稿结果不确定，已禁止自动重试并停止批次")
                elif not pixiv_ready:
                    all_succeeded = False
                elif cancel_requested:
                    manifest["status_by_target"]["pixiv"] = "canceled"
                    manifest["errors"].append("Pixiv upload canceled")
                    all_succeeded = False
                elif pixiv_browser_error:
                    manifest["status_by_target"]["pixiv"] = "failed"
                    manifest["pixiv"]["error_code"] = pixiv_browser_error_code
                    manifest["errors"].append(
                        f"Pixiv browser unavailable [{pixiv_browser_error_code}]: {pixiv_browser_error}"
                    )
                    all_succeeded = False
                else:
                    pixiv_copy = Path(manifest["pixiv"]["clean_copy_path"])
                    payload = {
                        "title_ja": manifest["pixiv"]["title_ja"],
                        "title_zh": manifest["pixiv"]["title_zh"],
                        "caption_ja": manifest["pixiv"]["caption_ja"],
                        "caption_zh": manifest["pixiv"]["caption_zh"],
                        "final_tags": manifest["pixiv"]["final_tags"],
                        "age_restriction": manifest["pixiv"]["age_restriction"],
                        "privacy": manifest["pixiv"]["privacy"],
                        "allow_tag_edits": manifest["pixiv"]["allow_tag_edits"],
                        "domain": manifest["pixiv"].get("domain", "original"),
                    }
                    max_retries = max(0, int(args.pixiv_max_retries))
                    pixiv_url = None
                    pixiv_steps: list = []
                    pixiv_result = PixivPostResult(None, pixiv_steps)
                    if pixiv_posts_attempted > 0 and pixiv_human is not None:
                        # 相邻投稿之间的低概率浏览过渡，避免机械化的连续投稿序列
                        try:
                            pixiv_browse_transition(
                                pixiv_page,
                                pixiv_human,
                                cancel_event=_cancel_ev,
                                artwork_url=last_pixiv_artwork_url or None,
                            )
                        except InterruptedError:
                            cancel_requested = True
                        except PixivFlowError as transition_exc:
                            pixiv_browser_error = str(transition_exc)
                            pixiv_browser_error_code = str(getattr(transition_exc, "code", "pixiv_browser_closed"))
                            log.error(f"    Pixiv 浏览过渡中断: {pixiv_browser_error}")
                    pixiv_posts_attempted += 1
                    for attempt in range(max_retries + 1):
                        pixiv_steps = []
                        try:
                            report_image("filling_pixiv", stage_progress=0.0, activity={})
                            # A safe pre-click retry reuses the same context but
                            # must reload a clean upload form before refilling it.
                            if attempt > 0:
                                ensure_on_pixiv_upload_page(
                                    pixiv_page,
                                    cancel_event=_cancel_ev,
                                    interaction_callback=lambda activity: report_image(
                                        "opening_pixiv",
                                        stage_progress=0.0,
                                        activity=activity or {},
                                    ),
                                )
                                report_image("filling_pixiv", stage_progress=0.0, activity={})
                            raw_result = create_pixiv_post(
                                pixiv_page,
                                payload,
                                pixiv_copy,
                                args.delay,
                                log_dir=LOG_DIR,
                                cancel_event=_cancel_ev,
                                progress_callback=report_image,
                                # Route interaction state through the current
                                # image reporter so item/stage counters survive
                                # the waiting_input transition unchanged.
                                interaction_callback=lambda activity: report_image(
                                    "filling_pixiv",
                                    activity=activity or {},
                                ),
                                human=pixiv_human,
                            )
                            if isinstance(raw_result, PixivPostResult):
                                pixiv_result = raw_result
                            else:
                                legacy_url, legacy_steps = raw_result
                                pixiv_result = PixivPostResult(legacy_url, list(legacy_steps))
                            pixiv_url = pixiv_result.url
                            pixiv_steps = pixiv_result.steps
                            if pixiv_url:
                                try:
                                    pixiv_url = _validate_confirmed_pixiv_url(pixiv_url)
                                except PixivFlowError as exc:
                                    log.error("    Pixiv 成功回执校验失败 [%s]: %s", exc.code, exc)
                                    pixiv_result = PixivPostResult(
                                        None,
                                        pixiv_steps,
                                        error_code=exc.code,
                                        batch_fatal=True,
                                        maybe_posted=True,
                                    )
                                    pixiv_url = None
                        except InterruptedError as exc:
                            cancel_requested = True
                            pixiv_url = None
                            pixiv_steps = list(getattr(exc, "pixiv_steps", pixiv_steps))
                            submitted_before_cancel = any(
                                getattr(step, "name", "") == "publish_click" and getattr(step, "ok", False)
                                for step in pixiv_steps
                            )
                            pixiv_result = PixivPostResult(
                                None,
                                pixiv_steps,
                                error_code=(
                                    "pixiv_task_canceled_after_submit"
                                    if submitted_before_cancel
                                    else "pixiv_task_canceled"
                                ),
                                batch_fatal=True,
                                maybe_posted=submitted_before_cancel,
                            )
                            break
                        except Exception as exc:
                            error_code = str(getattr(exc, "code", "pixiv_publish_exception"))
                            log.error(f"    Pixiv 发布异常 (attempt {attempt + 1}) [{error_code}]: {exc}")
                            log.debug(traceback.format_exc())
                            pixiv_url = None
                            error_steps = getattr(exc, "pixiv_steps", None)
                            pixiv_steps = list(error_steps) if error_steps is not None else list(pixiv_steps)
                            submitted_before_error = any(
                                getattr(step, "name", "") == "publish_click" and getattr(step, "ok", False)
                                for step in pixiv_steps
                            )
                            pixiv_result = PixivPostResult(
                                None,
                                pixiv_steps,
                                error_code=error_code,
                                batch_fatal=bool(getattr(exc, "batch_fatal", True)),
                                maybe_posted=bool(getattr(exc, "maybe_posted", False) or submitted_before_error),
                            )

                        if pixiv_url:
                            last_pixiv_artwork_url = pixiv_url
                            interval = (
                                pixiv_human.between_posts_delay(args.delay)
                                if pixiv_human is not None
                                else max(0.0, float(args.delay))
                            )
                            pixiv_next_post_at = time.monotonic() + interval
                            break

                        retry_decision = _pixiv_retry_decision(
                            pixiv_result,
                            pixiv_steps,
                            attempt=attempt,
                            max_retries=max_retries,
                        )
                        if retry_decision == "stop_uncertain":
                            pixiv_batch_fatal = True
                            pixiv_batch_error_code = pixiv_result.error_code or "pixiv_submit_unconfirmed"
                            log.warning("    Pixiv 投稿已经点击，结果未确认；禁止自动重试并终止剩余 Pixiv 队列")
                            break

                        if retry_decision == "stop_batch":
                            pixiv_batch_fatal = True
                            pixiv_batch_error_code = pixiv_result.error_code or "pixiv_batch_stopped"
                            log.error(
                                f"    Pixiv 发布失败 [{pixiv_batch_error_code}]，终止剩余 Pixiv 队列"
                            )
                            break

                        if retry_decision == "retry_safe":
                            retry_delay = (attempt + 1) * 3
                            log.info(
                                f"    Pixiv 点击前失败，{retry_delay} 秒后重试 "
                                f"({attempt + 2}/{max_retries + 1})..."
                            )
                            try:
                                _sleep_with_cancel(retry_delay, _cancel_ev)
                            except InterruptedError:
                                cancel_requested = True
                                pixiv_result = PixivPostResult(
                                    None,
                                    pixiv_steps,
                                    error_code="pixiv_task_canceled",
                                    batch_fatal=True,
                                )
                                break
                            continue

                        # No retry budget remains. This is an ordinary item
                        # failure unless the result itself explicitly requires
                        # stopping the Pixiv batch.
                        if pixiv_result.batch_fatal:
                            pixiv_batch_fatal = True
                            pixiv_batch_error_code = pixiv_result.error_code or "pixiv_batch_stopped"
                        break

                    manifest["pixiv"]["upload_steps"] = [step.to_dict() for step in pixiv_steps]
                    manifest["pixiv"]["error_code"] = pixiv_result.error_code
                    if pixiv_result.maybe_posted:
                        log.warning(
                            "    Pixiv 已点击投稿但结果未确认，记为 maybe_posted；"
                            "原图继续保留在 upload/，后续批次不会自动重投"
                        )
                        manifest["pixiv"]["post_url"] = ""
                        manifest["status_by_target"]["pixiv"] = "maybe_posted"
                        manifest["errors"].append(
                            f"Pixiv result uncertain [{pixiv_result.error_code or 'pixiv_submit_unconfirmed'}]"
                        )
                        all_succeeded = False
                    elif cancel_requested and not pixiv_url:
                        manifest["status_by_target"]["pixiv"] = "canceled"
                        manifest["errors"].append("Pixiv upload canceled [pixiv_task_canceled]")
                        all_succeeded = False
                    elif pixiv_url:
                        manifest["pixiv"]["post_url"] = pixiv_url
                        manifest["status_by_target"]["pixiv"] = "success"
                        report_image("verifying_pixiv", stage_progress=1.0, activity={})
                        log.info(f"    Pixiv 发布成功: {pixiv_url}")
                    else:
                        failed_steps = [step for step in pixiv_steps if not step.ok]
                        if failed_steps:
                            summary = "; ".join(f"{step.name}:{step.reason}" for step in failed_steps)
                            error_msg = f"Pixiv upload failed [{pixiv_result.error_code or 'pixiv_upload_failed'}] at [{summary}]"
                        else:
                            error_msg = f"Pixiv upload failed [{pixiv_result.error_code or 'pixiv_upload_failed'}]"
                        if manifest["status_by_target"].get("pixiv") != "failed":
                            manifest["status_by_target"]["pixiv"] = "failed"
                            manifest["errors"].append(error_msg)
                        all_succeeded = False

                report_image(
                    "item_target",
                    targets=_task_target_results(manifest, ["pixiv"]),
                )

            write_manifest(manifest_path, manifest)

            for target in effective_targets:
                status = manifest["status_by_target"].get(target)
                if status in {"success", "skipped_already_done", "skipped_civitai_safety"}:
                    target_success_counts[target] += 1
                elif status in {"failed", "maybe_posted"}:
                    target_fail_counts[target] += 1
                elif status == "canceled":
                    target_canceled_counts[target] += 1

            if all_succeeded:
                report_image("finalizing_image", stage_progress=0.0)
                try:
                    dest = move_to_done(orig_path)
                except OSError as exc:
                    all_succeeded = False
                    manifest["finalization"] = {
                        "status": "failed",
                        "error_code": "source_archive_failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    manifest["errors"].append(
                        f"Source archive failed [source_archive_failed]: {type(exc).__name__}: {exc}"
                    )
                    write_manifest(manifest_path, manifest)
                    log.error(
                        "    发布已确认成功，但无法把原图移出 upload/ "
                        f"[source_archive_failed]：{type(exc).__name__}: {exc}"
                    )
                else:
                    manifest["finalization"] = {
                        "status": "archived",
                        "source_path": str(orig_path),
                        "done_path": str(dest),
                        "archived_at": datetime.now().isoformat(timespec="seconds"),
                    }
                    write_manifest(manifest_path, manifest)
                    log.info(f"    已移动到: {dest.name}")
                    report_image("finalizing_image", stage_progress=1.0)
                    success_count += 1
                    consecutive_failures = 0
            if not all_succeeded:
                target_summaries = []
                for target in effective_targets:
                    status = manifest["status_by_target"].get(target, "pending")
                    if status == "success":
                        target_summaries.append(f"{target} 成功")
                    elif status == "skipped_already_done":
                        target_summaries.append(f"{target} 已发过")
                    elif status == "skipped_civitai_safety":
                        target_summaries.append(f"{target} 安全过滤跳过")
                    elif status == "failed":
                        target_summaries.append(f"{target} 失败")
                    elif status == "maybe_posted":
                        target_summaries.append(f"{target} 结果待人工确认")
                    else:
                        target_summaries.append(f"{target} {status}")
                log.error(f"    {'，'.join(target_summaries)}，文件保留在 upload/")
                if cancel_requested:
                    canceled_count += 1
                else:
                    fail_count += 1
                ok_statuses = {"success", "skipped_already_done", "skipped_civitai_safety"}
                any_target_ok = any(manifest["status_by_target"].get(target) in ok_statuses for target in effective_targets)
                if any_target_ok:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
            report_image(
                "item_complete",
                current=success_count + fail_count + canceled_count,
                succeeded=success_count,
                failed=fail_count,
                canceled=canceled_count,
                **_task_item_outcome(
                    manifest,
                    effective_targets,
                    source_available=orig_path.exists(),
                    canceled=cancel_requested,
                ),
            )
            if pixiv_batch_fatal:
                batch_stop_reason = pixiv_batch_error_code or "pixiv_batch_stopped"
                log.error(
                    f"\nPixiv 队列已安全停止 [{batch_stop_reason}]，"
                    "剩余图片与 manifest 保留"
                )
                break
            if cancel_requested:
                batch_stop_reason = "task_canceled"
                log.info("\n任务已取消，剩余图片与 manifest 保留")
                break
            if consecutive_failures >= abort_threshold:
                batch_stop_reason = "consecutive_failures"
                log.error(f"\n连续 {consecutive_failures} 张失败，中断本次批次（避免触发风控）")
                break

        if args.dry_run:
            log.info(f"\n完成。dry-run 样本 {success_count}，未实际发布。")
        else:
            if len(all_processed_targets) == 1:
                log.info(f"\n完成。成功 {success_count}，未成功 {fail_count}。")
            else:
                log.info(f"\n完成。全部目标成功 {success_count}，未全部成功 {fail_count}。")
            for target in all_processed_targets:
                log.info(
                    f"  {target}: 成功 {target_success_counts.get(target, 0)}，"
                    f"失败 {target_fail_counts.get(target, 0)}，"
                    f"取消 {target_canceled_counts.get(target, 0)}"
                )
    finally:
        if civitai_context is not None:
            try:
                civitai_context.close()
            except Exception as exc:
                log.warning("关闭 Civitai 浏览器失败: %s: %s", type(exc).__name__, exc)
        if pixiv_context is not None:
            try:
                close_pixiv_browser(pixiv_context)
            except Exception as exc:
                log.warning("关闭 Pixiv 浏览器失败: %s: %s", type(exc).__name__, exc)
        if playwright is not None:
            try:
                playwright.stop()
            except Exception as exc:
                log.warning("停止 Playwright 失败: %s: %s", type(exc).__name__, exc)
        if pixiv_lease is not None:
            PIXIV_SESSION.release(pixiv_lease)
        shutil.rmtree(temp_dir, ignore_errors=True)

    processed_count = success_count + fail_count + canceled_count
    unprocessed_count = max(0, len(image_queue) - processed_count)
    all_items_succeeded = success_count == len(image_queue) and not fail_count and not canceled_count
    if all_items_succeeded:
        status = "success"
        batch_stop_reason = ""
    elif canceled_count or (getattr(args, "cancel_event", None) and args.cancel_event.is_set()):
        status = "canceled"
        batch_stop_reason = batch_stop_reason or "task_canceled"
    elif fail_count or unprocessed_count:
        status = "failed"
        batch_stop_reason = batch_stop_reason or "publishing_failed"
    else:
        status = "success"
    return {
        "status": status,
        "reason_code": batch_stop_reason,
        "total": len(image_queue),
        "processed": processed_count,
        "succeeded": success_count,
        "failed": fail_count,
        "canceled": canceled_count,
        "unprocessed": unprocessed_count,
        "targets": all_processed_targets,
        "target_succeeded": target_success_counts,
        "target_failed": target_fail_counts,
        "target_canceled": target_canceled_counts,
    }


def cmd_pixiv_fit_collect(args):
    files = ensure_runtime_files(SCRIPT_DIR)
    alias_data = load_json(files["aliases"], {})

    log.info(
        f"Pixiv 样本采集开始：目标 {args.target_count} 张，流量门槛 "
        f"bookmark>={args.min_bookmarks} 或 like>={args.min_likes} 或 view>={args.min_views}"
    )

    with sync_playwright() as pw:
        context, page = open_pixiv_browser(pw, profile_dir=PIXIV_RULE_FIT_PROFILE_DIR)
        try:
            result = collect_rule_fit_sample_manifests(
                context=context,
                page=page,
                sample_dir=files["rule_fit_samples"],
                manifest_dir=files["rule_fit_manifests"],
                alias_data=alias_data,
                target_count=args.target_count,
                per_source_limit=args.per_source_limit,
                min_bookmarks=args.min_bookmarks,
                min_likes=args.min_likes,
                min_views=args.min_views,
                min_score=args.min_score,
                min_original=args.min_original,
                min_fanart=args.min_fanart,
                min_r18=args.min_r18,
            )
        finally:
            context.close()

    report_path = create_rule_fit_report_path(files["rule_fit_reports"], "collect")
    save_json(report_path, result["stats"])
    log.info(
        f"采集完成：本轮处理 {result['stats']['processed_count']} 张，"
        f"累计有效样本 {result['stats']['effective_count']} 张，统计已写入 {report_path.name}"
    )


def cmd_pixiv_fit_compare(args):
    files = ensure_runtime_files(SCRIPT_DIR)
    alias_data = load_json(files["aliases"], {})
    popularity_data = load_json(files["popularity"], {})
    age_rules = load_json(files["age_rules"], {})
    jp_alias_cache = load_json(files["jp_aliases"], {})
    general_jp_data = load_json(files["general_jp"], {})
    danbooru_jp_map = load_json(files["danbooru_jp"], {})
    if danbooru_jp_map:
        general_jp_data["_danbooru_map"] = danbooru_jp_map
    metadata_bridge, tagger_bridge = _make_bridges()

    result = compare_rule_fit_samples(
        manifest_dir=files["rule_fit_manifests"],
        alias_data=alias_data,
        popularity_data=popularity_data,
        age_rules=age_rules,
        metadata_bridge=metadata_bridge,
        tagger_bridge=tagger_bridge,
        jp_alias_cache=jp_alias_cache,
        general_jp_data=general_jp_data,
        live_lookup=not args.no_live_lookup,
    )
    save_json(files["jp_aliases"], jp_alias_cache)
    save_json(files["popularity"], popularity_data)
    report_path = create_rule_fit_report_path(files["rule_fit_reports"], "compare")
    save_json(report_path, result)
    log.info(
        f"对比完成：sidecar {result['count']} 份，完整 compare {result['compared_count']} 张，"
        f"缺图跳过 {result['skipped_image_missing_count']} 张，摘要写入 {report_path.name}"
    )


def cmd_pixiv_fit_report(args):
    files = ensure_runtime_files(SCRIPT_DIR)
    compare_paths = sorted(
        path for path in files["rule_fit_manifests"].iterdir()
        if path.is_file() and path.name.endswith(".compare.json")
    )
    if not compare_paths:
        log.info("没有找到 compare sidecar，请先运行 pixiv-fit-compare。")
        return

    compare_results = [load_json(path, {}) for path in compare_paths]
    report = summarize_rule_fit_report(compare_results)
    json_path = create_rule_fit_report_path(files["rule_fit_reports"], "summary")
    md_path = json_path.with_suffix(".md")
    save_json(json_path, report)
    md_path.write_text(render_rule_fit_report_markdown(report), encoding="utf-8")
    log.info(f"汇总报告已写入：{json_path.name} / {md_path.name}")


def main():
    parser = argparse.ArgumentParser(description="Pixiv Uploader CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sp_upload = subparsers.add_parser("upload", help="批量上传 upload/ 目录的图片")
    sp_upload.add_argument("--delay", type=float, default=10, help="每个 post 间隔秒数（默认10）")
    sp_upload.add_argument("--targets", default="civitai", help="发布目标，逗号分隔：civitai,pixiv")
    sp_upload.add_argument("--dry-run", action="store_true", help="只生成 manifest 和清洗副本，不实际发布")
    sp_upload.add_argument("--pixiv-privacy", default="public", choices=["public", "logged_in", "mypixiv", "private"])
    sp_upload.add_argument("--pixiv-allow-tag-edits", default="false", help="Pixiv 是否允许他人编辑标签（true/false）")
    sp_upload.add_argument("--pixiv-max-retries", type=int, default=1, help="Pixiv 失败重试次数（默认 1，publish 已点击则不重试）")
    sp_upload.add_argument("--abort-after-failures", type=int, default=3, help="连续失败 N 张后中断批次，避免触发风控（默认 3）")
    sp_upload.add_argument("--llm-reverse", action="store_true", help="用 LLM 为 Pixiv 生成标题和简介")
    sp_upload.add_argument("--llm-persona", default="", help="LLM 人设 ID")
    sp_upload.add_argument("--llm-content-mode", default="", choices=["", "sfw", "nsfw"], help="LLM 文案模式")
    sp_upload.add_argument("--no-ai-tags", default="", nargs="?", const="all",
                           help="不为 Pixiv 添加 AI 标签")
    sp_upload.add_argument("--count", type=int, default=0, help="本次发几张（默认 0 = 随机 1-5）")
    sp_upload.add_argument(
        "--sort", default="random",
        choices=["random", "name_asc", "name_desc", "time_asc", "time_desc"],
        help="选图排序（默认 random）",
    )

    sp_collect = subparsers.add_parser("pixiv-fit-collect", help="采集 Pixiv 规则拟合样本")
    sp_collect.add_argument("--target-count", type=int, default=50, help="目标样本数（默认50）")
    sp_collect.add_argument("--per-source-limit", type=int, default=40, help="每个入口最多抓取的作品数（默认40）")
    sp_collect.add_argument("--min-bookmarks", type=int, default=800, help="高流量最低书签数（默认800）")
    sp_collect.add_argument("--min-likes", type=int, default=200, help="高流量最低爱心数（默认200）")
    sp_collect.add_argument("--min-views", type=int, default=12000, help="高流量最低浏览量（默认12000）")
    sp_collect.add_argument("--min-score", type=float, default=12000, help="综合流量分最低值（默认12000）")
    sp_collect.add_argument("--min-original", type=int, default=15, help="原创样本最少数量（默认15）")
    sp_collect.add_argument("--min-fanart", type=int, default=15, help="二创样本最少数量（默认15）")
    sp_collect.add_argument("--min-r18", type=int, default=10, help="R-18/R-18G 样本最少数量（默认10）")

    sp_compare = subparsers.add_parser("pixiv-fit-compare", help="对 Pixiv 拟合样本执行本地检测与差异对比")
    sp_compare.add_argument("--no-live-lookup", action="store_true", help="不实时刷新 Pixiv 标签热度缓存")

    subparsers.add_parser("pixiv-fit-report", help="汇总样本对比结果并输出报告")

    args = parser.parse_args()

    ensure_runtime_layout(SCRIPT_DIR)
    setup_logging()
    log.info(f"=== 启动 Pixiv Uploader CLI {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
    prune_logs()
    cleanup_done_dir()

    try:
        if args.command == "upload":
            cmd_upload(args)
        elif args.command == "pixiv-fit-collect":
            cmd_pixiv_fit_collect(args)
        elif args.command == "pixiv-fit-compare":
            cmd_pixiv_fit_compare(args)
        elif args.command == "pixiv-fit-report":
            cmd_pixiv_fit_report(args)
    except KeyboardInterrupt:
        log.info("\n用户中断。")
    except Exception as exc:
        log.error(f"\n致命错误: {exc}")
        log.debug(traceback.format_exc())
        raise

    log.info("=== 结束 ===")


if __name__ == "__main__":
    main()
