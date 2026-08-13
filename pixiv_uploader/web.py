from __future__ import annotations

import argparse
import builtins
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import random
import threading
import time
import uuid
import webbrowser
from datetime import datetime, timedelta
from io import TextIOBase
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

from .paths import FRONTEND_DIST_DIR, FRONTEND_PUBLIC_DIR, PROJECT_ROOT
from .pixiv.llm_platforms import PLATFORM_SPECS
from .pixiv.llm_reverse import (
    build_llm_retry_activity,
    default_llm_reverse_config,
    infer_image_copy,
    mask_llm_config,
    normalize_llm_reverse_config,
    validate_llm_reverse_config,
)
from .pixiv.session import (
    PIXIV_PROFILE_DIR,
    PIXIV_SESSION,
    PixivFlowError,
    PixivProfileInUseError,
    PixivRateController,
)
from .pixiv.storage import ensure_runtime_files, load_json, save_json
from .pixiv.support import run_pixiv_login_flow
from .pixiv.tagger_settings import (
    load_haintag_settings as _load_haintag_settings,
    resolve_cl_model_dir,
    resolve_pixai_model_dir,
    save_haintag_settings as _save_haintag_settings,
)
from .runtime import ensure_runtime_layout
from .task_progress import TaskProgressState, build_progress_profile
from .watermark import (
    MAX_FONT_UPLOAD_BYTES,
    MAX_IMAGE_UPLOAD_BYTES,
    WatermarkError,
    WatermarkService,
)

SCRIPT_DIR = PROJECT_ROOT
CIVITAI_PROFILE_DIR = Path.home() / ".civitai_splitter_chrome"
PORT = int(os.environ.get("WEB_PORT", "7788"))
CONFIG_FILE = SCRIPT_DIR / "config.json"
UPLOAD_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})


def _find_chrome_executable() -> str | None:
    for name in ("google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser"):
        executable = shutil.which(name)
        if executable:
            return executable
    for candidate in (
        Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _open_login_browser(profile_dir: Path, url: str) -> None:
    chrome = _find_chrome_executable()
    if chrome is None:
        raise RuntimeError("Google Chrome was not found")
    profile_dir.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [
            chrome,
            f"--user-data-dir={profile_dir}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            "--disable-sync",
            "--no-first-run",
            url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _load_config() -> dict:
    payload = load_json(CONFIG_FILE, {})
    return payload if isinstance(payload, dict) else {}


def _save_config(cfg: dict) -> None:
    save_json(CONFIG_FILE, cfg)


def _watermark_service() -> WatermarkService:
    return WatermarkService(ensure_runtime_layout(SCRIPT_DIR).watermark)


def _censor_config_path() -> Path:
    return ensure_runtime_files(SCRIPT_DIR)["censor_config"]


_censor_deps_cache: tuple[float, bool] | None = None


def _censor_deps_ok() -> bool:
    """Whether ultralytics + opencv-python are importable (cached 30s)."""
    global _censor_deps_cache
    now = time.monotonic()
    if _censor_deps_cache is not None and now - _censor_deps_cache[0] < 30:
        return _censor_deps_cache[1]
    try:
        import cv2  # noqa: F401
        import ultralytics  # noqa: F401
        ok = True
    except ImportError:
        ok = False
    _censor_deps_cache = (now, ok)
    return ok


def _watermark_local_only():
    if request.remote_addr in {"127.0.0.1", "::1"}:
        return None
    return _api_error("local_only", 403, detail="watermark settings are available only from localhost")


# Apply saved config to env on startup
_startup_cfg = _load_config()
if _startup_cfg.get("api_key"):
    os.environ.setdefault("CIVITAI_API_KEY", _startup_cfg["api_key"])

# ── Shared state ───────────────────────────────────────────────
TASKS: dict[str, dict] = {}
TASKS_LOCK = threading.Lock()

_scheduler_timer: threading.Timer | None = None
_scheduler_lock = threading.Lock()
_shutdown_timer: threading.Timer | None = None
_shutdown_lock = threading.Lock()
# Serializes tasks that replace process-global sys.stdout/stderr/builtins.input
_EXEC_LOCK = threading.Lock()
# Makes Pixiv task admission, standalone login, and profile clearing atomic.
# The queued task record or acquired profile lease becomes the durable
# reservation before this lock is released.
_pixiv_admission_lock = threading.Lock()
_pixiv_login_lock = threading.Lock()
_pixiv_login_thread: threading.Thread | None = None
_pixiv_login_cancel: threading.Event | None = None

SSE_CLIENTS: list[queue.Queue] = []
CLIENTS_LOCK = threading.Lock()

_pixai_tasks: dict[str, dict] = {}
_pixai_tasks_lock = threading.Lock()

CMD_LABELS = {
    2: ("发布图片", "Civitai + Pixiv"),
    3: ("发布到 Pixiv", "Pixiv"),
    4: ("安装打码模型", "本地处理"),
    5: ("检查更新", "本地处理"),
    6: ("生成 Pixiv 文案与标签", "本地处理"),
}
MAINTENANCE_COMMANDS = frozenset({4, 5})
CMD_LOG_SOURCES = {
    2: "publish",
    3: "pixiv",
    4: "setup",
    5: "update",
    6: "llm",
}

# ── Flask app ──────────────────────────────────────────────────
app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024


def _api_error(code: str, status: int = 400, *, detail: str = "", **params):
    if code == "generic" and "reason" not in params:
        params["reason"] = detail
    payload = {
        "error": detail or code,
        "error_code": code,
        "error_params": params,
    }
    return jsonify(payload), status


@app.errorhandler(413)
def _handle_request_too_large(_error):
    if request.path.startswith("/api/"):
        return _api_error("request_too_large", 413, detail="上传内容超过 512 MB 限制")
    return "request too large", 413


@app.errorhandler(404)
def _handle_not_found(_error):
    if request.path.startswith("/api/"):
        return _api_error("not_found", 404, detail="接口不存在")
    return "not found", 404


@app.errorhandler(405)
def _handle_method_not_allowed(_error):
    if request.path.startswith("/api/"):
        return _api_error("method_not_allowed", 405, detail="请求方法不受支持")
    return "method not allowed", 405


def _has_active_tasks() -> bool:
    with TASKS_LOCK:
        return any(t.get("status") in ("queued", "running", "waiting_input") for t in TASKS.values())


def _cancel_scheduler() -> dict:
    global _scheduler_timer
    cfg = _load_config()
    sched = _scheduler_from_config(cfg)
    sched["enabled"] = False
    sched["next_fire_at"] = None
    cfg["scheduler"] = sched
    _save_config(cfg)
    with _scheduler_lock:
        if _scheduler_timer is not None:
            _scheduler_timer.cancel()
            _scheduler_timer = None
    return sched


def _exit_when_idle(force: bool = False) -> None:
    with CLIENTS_LOCK:
        has_clients = bool(SSE_CLIENTS)
    if has_clients and not force:
        return
    _cancel_scheduler()
    if _has_active_tasks():
        _schedule_idle_shutdown(force=force)
        return
    time.sleep(0.2)
    os._exit(0)


def _schedule_idle_shutdown(force: bool = False) -> None:
    global _shutdown_timer
    with _shutdown_lock:
        if _shutdown_timer is not None:
            _shutdown_timer.cancel()
        _shutdown_timer = threading.Timer(5.0, _exit_when_idle, kwargs={"force": force})
        _shutdown_timer.daemon = True
        _shutdown_timer.start()


def _cancel_idle_shutdown() -> None:
    global _shutdown_timer
    with _shutdown_lock:
        if _shutdown_timer is not None:
            _shutdown_timer.cancel()
            _shutdown_timer = None


# ── SSE helpers ────────────────────────────────────────────────
def _broadcast_sse(event_type: str, data: dict) -> None:
    payload = {"type": event_type, "data": data}
    with CLIENTS_LOCK:
        dead = []
        for q in SSE_CLIENTS:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            SSE_CLIENTS.remove(q)


def _pixiv_session_payload(session_snapshot: dict | None = None) -> dict:
    payload = dict(session_snapshot or PIXIV_SESSION.snapshot())
    payload.update(PixivRateController().snapshot())
    return payload


def _broadcast_pixiv_session(session_snapshot: dict) -> None:
    payload = _pixiv_session_payload(session_snapshot)
    _broadcast_sse(
        "status_update",
        {
            "pixiv_session": payload,
            "pixiv_logged_in": payload.get("state") == "authenticated",
        },
    )


PIXIV_SESSION.add_listener(_broadcast_pixiv_session)


_TASK_PRIVATE_FIELDS = {"thread", "log_lines", "pending_input", "cancel_event"}


def _task_snapshot(task: dict) -> dict:
    return {key: value for key, value in task.items() if key not in _TASK_PRIVATE_FIELDS}


def _plain_file_name(value) -> str:
    return str(value or "").replace("\x00", "").replace("\\", "/").rsplit("/", 1)[-1][:255]


def _progress_item_plan(command: int, params: dict) -> tuple[list[dict], list[str]]:
    if command not in (2, 3):
        return [], []
    names = params.get("files") or []
    if not isinstance(names, list):
        return [], []
    default_targets = "pixiv" if command == 3 else "civitai,pixiv"
    targets = [
        part.strip().lower()
        for part in str(params.get("targets", default_targets)).split(",")
        if part.strip()
    ]
    return [{"name": _plain_file_name(name), "retryable": True} for name in names], targets


class _TaskProgressController:
    def __init__(self, task_id: str, command: int, params: dict) -> None:
        with TASKS_LOCK:
            total = int(TASKS.get(task_id, {}).get("total") or 0)
        planned_items, targets = _progress_item_plan(command, params)
        self._task_id = task_id
        self._command = command
        self._state = TaskProgressState(
            build_progress_profile(command, params),
            total=total,
            items=planned_items if planned_items else None,
            targets=targets,
        )

    def report(self, stage: str, **details) -> None:
        with TASKS_LOCK:
            task = TASKS.get(self._task_id)
            if task is None or task.get("status") in {"done", "failed", "canceled"}:
                return
            fields = self._state.advance(stage, **details)
            task.update(fields)
            activity = task.get("activity") or {}
            if activity.get("kind") == "pixiv_interaction":
                task["status"] = "waiting_input"
            elif task.get("status") == "waiting_input":
                cancel_event = task.get("cancel_event")
                if not (cancel_event and cancel_event.is_set()):
                    task["status"] = "running"
            task["count"] = f"{task['current']} / {task['total']}" if task["total"] else "—"
            snap = _task_snapshot(task)
        _broadcast_sse("task_update", snap)

    def interaction(self, activity: dict | None) -> None:
        with TASKS_LOCK:
            task = TASKS.get(self._task_id)
            if task is None or task.get("status") in {"done", "failed", "canceled"}:
                return
            task.update(self._state.advance(self._state.stage, activity=dict(activity or {})))
            if activity:
                task["status"] = "waiting_input"
            else:
                cancel_event = task.get("cancel_event")
                if task.get("status") == "waiting_input" and not (cancel_event and cancel_event.is_set()):
                    task["status"] = "running"
            snap = _task_snapshot(task)
        _broadcast_sse("task_update", snap)

    def store_result(self, result) -> None:
        with TASKS_LOCK:
            task = TASKS.get(self._task_id)
            if task is None:
                return
            task["result"] = result
            if isinstance(result, dict):
                task.update(
                    self._state.advance(
                        "item_complete",
                        total=result.get("total"),
                        current=result.get("processed"),
                        succeeded=result.get("succeeded"),
                        failed=result.get("failed"),
                        canceled=result.get("canceled"),
                    )
                )

    def finish(self, status: str, *, reason_code: str = "") -> None:
        with TASKS_LOCK:
            task = TASKS.get(self._task_id)
            if task is None:
                return
            if self._command in (2, 3):
                upload_dir = SCRIPT_DIR / "upload"
                try:
                    available_names = (
                        [path.name for path in upload_dir.iterdir() if path.is_file()]
                        if upload_dir.exists()
                        else []
                    )
                except OSError:
                    available_names = []
                self._state.reconcile_source_availability(available_names)
            task.update(self._state.finish(status, reason_code=reason_code))
            task["status"] = self._state.finished_status or status
            task["count"] = f"{task['current']} / {task['total']}" if task["total"] else "—"
            snap = _task_snapshot(task)
        _broadcast_sse("task_update", snap)


def _initial_progress(command: int, params: dict, total: int) -> dict:
    planned_items, targets = _progress_item_plan(command, params)
    return TaskProgressState(
        build_progress_profile(command, params),
        total=total,
        items=planned_items if planned_items else None,
        targets=targets,
    ).snapshot()


def _new_task_record(
    task_id: str,
    command: int,
    params: dict,
    *,
    title: str,
    target: str,
    total: int,
) -> dict:
    return {
        "id": task_id,
        "title": title,
        "status": "queued",
        "target": target,
        "count": f"0 / {total}" if total else "—",
        "eta": "—",
        "cmd": command,
        "category": "maintenance" if command in MAINTENANCE_COMMANDS else "workflow",
        "params": params,
        "cancel_flag": False,
        "cancel_event": threading.Event(),
        "created_at": datetime.now().strftime("%H:%M:%S"),
        "log_lines": [],
        "pending_input": None,
        "thread": None,
        **_initial_progress(command, params, total),
    }


def _push_log_line(task_id: str, lvl: str, src: str, msg: str) -> None:
    entry = {
        "t":       datetime.now().strftime("%H:%M:%S.%f")[:12],
        "lvl":     lvl.upper().replace("WARNING", "WARN").replace("ERROR", "ERR"),
        "src":     src,
        "msg":     msg,
        "task_id": task_id,
    }
    with TASKS_LOCK:
        if task_id in TASKS:
            TASKS[task_id]["log_lines"].append(entry)
    _broadcast_sse("log", entry)


def _set_task_status(task_id: str, status: str, progress: float | None = None) -> None:
    with TASKS_LOCK:
        if task_id not in TASKS:
            return
        task = TASKS[task_id]
        task["status"] = status
        if progress is not None:
            task["progress"] = progress
        if status in {"failed", "canceled"}:
            if task.get("stage") == "queued":
                task["stage"] = status
            task["progress"] = min(float(task.get("progress") or 0.0), 0.99)
        elif status == "done":
            task["stage"] = "done"
            task["stage_progress"] = 1.0
            task["progress"] = 1.0
        snap = _task_snapshot(task)
    _broadcast_sse("task_update", snap)


def _is_task_canceled(task_id: str) -> bool:
    with TASKS_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return False
        ev = task.get("cancel_event")
        return bool(ev and ev.is_set())


# ── Log capture ────────────────────────────────────────────────
class _ThreadWriter(TextIOBase):
    def __init__(self, original, task_id: str, lvl: str) -> None:
        self._orig = original
        self._task_id = task_id
        self._lvl = lvl

    def write(self, text: str) -> int:
        if text and text.strip():
            _push_log_line(self._task_id, self._lvl, "worker", text.rstrip())
        return self._orig.write(text)

    def flush(self) -> None:
        self._orig.flush()


class _SseLogHandler(logging.Handler):
    def __init__(self, task_id: str, source: str = "app") -> None:
        super().__init__()
        self._task_id = task_id
        self._source = source

    def emit(self, record: logging.LogRecord) -> None:
        lvl = record.levelname
        _push_log_line(self._task_id, lvl, self._source, self.format(record))


# ── Input capture (挂起线程等前端回复) ─────────────────────────
class _WebInput:
    def __init__(self, task_id: str) -> None:
        self._task_id = task_id

    def __call__(self, prompt: str = "") -> str:
        ev = threading.Event()
        result = ["\n"]
        with TASKS_LOCK:
            if self._task_id in TASKS:
                TASKS[self._task_id]["pending_input"] = {"prompt": prompt, "event": ev, "result": result}
        _set_task_status(self._task_id, "waiting_input")
        _broadcast_sse("input_required", {"task_id": self._task_id, "prompt": prompt})
        ev.wait()
        with TASKS_LOCK:
            task = TASKS.get(self._task_id)
            if task:
                task.pop("pending_input", None)
                if task.get("cancel_event") and task["cancel_event"].is_set():
                    return ""
        _set_task_status(self._task_id, "running")
        return result[0]


# ── Task runner ────────────────────────────────────────────────
def _run_task(task_id: str, cmd: int, params: dict) -> None:
    progress = _TaskProgressController(task_id, cmd, params)
    with TASKS_LOCK:
        cancel_event = TASKS[task_id].get("cancel_event")

    if cancel_event and cancel_event.is_set():
        _push_log_line(task_id, "INFO", "worker", "任务已取消")
        progress.finish("canceled")
        return

    with _EXEC_LOCK:
        if cancel_event and cancel_event.is_set():
            _push_log_line(task_id, "INFO", "worker", "任务已取消")
            progress.finish("canceled")
            return
        _set_task_status(task_id, "running")
        progress.report("initializing", stage_progress=0.05)
        _run_task_locked(task_id, cmd, params, progress)


def _run_task_locked(
    task_id: str,
    cmd: int,
    params: dict,
    progress: _TaskProgressController,
) -> None:
    orig_stdout = sys.stdout
    orig_stderr = sys.stderr
    orig_input  = builtins.input
    sys.stdout  = _ThreadWriter(orig_stdout, task_id, "INFO")
    sys.stderr  = _ThreadWriter(orig_stderr, task_id, "ERR")
    builtins.input = _WebInput(task_id)

    app_logger = logging.getLogger("pixiv_uploader")
    app_logger.setLevel(logging.DEBUG)
    sse_handler = _SseLogHandler(task_id, CMD_LOG_SOURCES.get(cmd, "app"))
    sse_handler.setFormatter(logging.Formatter('%(message)s'))
    app_logger.addHandler(sse_handler)

    with TASKS_LOCK:
        cancel_event = TASKS[task_id].get("cancel_event")

    try:
        if cancel_event and cancel_event.is_set():
            _push_log_line(task_id, "INFO", "worker", "任务已取消")
            progress.finish("canceled")
            return

        if cmd in (2, 3):
            default_targets = "civitai,pixiv" if cmd == 2 else "pixiv"
            from .publishing import cmd_upload

            args = argparse.Namespace(
                targets=params.get("targets", default_targets),
                count=params.get("count", 0),
                files=params.get("files", []),
                sort=params.get("sort", "random"),
                delay=params.get("delay", 10),
                dry_run=False,
                pixiv_privacy="public",
                pixiv_allow_tag_edits="false",
                pixiv_max_retries=1,
                abort_after_failures=3,
                llm_reverse=params.get("llm_reverse", False),
                llm_persona=params.get("llm_persona", ""),
                llm_content_mode=params.get("llm_content_mode", ""),
                ai_tags_by_platform={"pixiv": bool((params.get("ai_tags_by_platform") or {}).get("pixiv", True))},
                cancel_event=cancel_event,
                progress_callback=progress.report,
                interaction_callback=progress.interaction,
            )
            result = cmd_upload(args)
            progress.store_result(result)
            _broadcast_sse("images_changed", {})
            result_reason = str(result.get("reason_code") or "") if isinstance(result, dict) else ""
            if _is_task_canceled(task_id) or (isinstance(result, dict) and result.get("status") == "canceled"):
                _push_log_line(task_id, "INFO", "worker", "任务已取消")
                progress.finish("canceled", reason_code=result_reason or "task_canceled")
                return
            if isinstance(result, dict) and result.get("status") != "success":
                progress.finish("failed", reason_code=result_reason or "batch_stopped")
                return

        elif cmd == 4:
            progress.report("installing", stage_progress=0.05)
            # setup_censor has interactive pip install — run as subprocess
            proc = subprocess.Popen(
                [sys.executable, "-m", "pixiv_uploader.pixiv.setup_censor"],
                cwd=str(SCRIPT_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in proc.stdout:
                _push_log_line(task_id, "INFO", "setup", line.rstrip())
                if _is_task_canceled(task_id):
                    proc.terminate()
                    break
            proc.wait()
            if _is_task_canceled(task_id):
                _push_log_line(task_id, "INFO", "worker", "任务已取消")
                progress.finish("canceled")
                return
            if proc.returncode:
                raise RuntimeError(f"打码模型安装失败，退出码 {proc.returncode}")
            progress.report("installing", stage_progress=1.0)

        elif cmd == 5:
            from . import cli as _launcher
            progress.report("checking_updates", stage_progress=0.05)
            # Patch _do_pull to capture output into web log (subprocess.call bypasses sys.stdout)
            def _web_do_pull() -> bool:
                progress.report("applying_update", stage_progress=0.05)
                try:
                    r = subprocess.run(
                        ["git", "-C", str(SCRIPT_DIR), "pull", "--ff-only"],
                        timeout=60, capture_output=True, text=True,
                    )
                    if r.stdout: print(r.stdout.rstrip())
                    if r.stderr: print(r.stderr.rstrip())
                    if r.returncode == 0:
                        progress.report("applying_update", stage_progress=1.0)
                    return r.returncode == 0
                except Exception as exc:
                    print(f"  pull 失败: {exc}")
                    return False
            _orig_do_pull = _launcher._do_pull
            _launcher._do_pull = _web_do_pull
            try:
                _launcher.cmd_check_update(cancel_event=cancel_event)
            finally:
                _launcher._do_pull = _orig_do_pull
            if _is_task_canceled(task_id):
                _push_log_line(task_id, "INFO", "worker", "任务已取消")
                progress.finish("canceled")
                return
            progress.report("finalizing", stage_progress=1.0)

        elif cmd == 6:
            progress.report("preparing_copy", stage_progress=0.2)
            cfg = normalize_llm_reverse_config(_load_config().get("llm_reverse"))
            image_name = str(params.get("image", "")).strip()
            image_path = SCRIPT_DIR / "upload" / Path(image_name).name if image_name else None
            image_url = str(params.get("image_url", "")).strip() or None
            progress.report("generating_copy", stage_progress=0.0)
            result = infer_image_copy(
                image_path=image_path,
                image_url=image_url,
                config=cfg,
                persona_id=params.get("llm_persona", ""),
                content_mode=params.get("llm_content_mode", ""),
                cancel_event=cancel_event,
                event_callback=lambda event, details: progress.report(
                    "generating_copy",
                    stage_progress=float(details.get("progress") or 0.0),
                    activity=build_llm_retry_activity(event, details),
                ),
            )
            progress.report("generating_copy", stage_progress=1.0)
            progress.report("finalizing", stage_progress=0.8)
            progress.store_result(result)
            _push_log_line(task_id, "INFO", "worker", f"LLM 反推: {result.get('status')}")
            if result.get("status") != "ok":
                progress.finish("failed")
                return

        if cancel_event and cancel_event.is_set():
            _push_log_line(task_id, "INFO", "worker", "任务已取消")
            progress.finish("canceled")
        else:
            progress.report("completing", stage_progress=0.8)
            progress.finish("done")
            if cmd not in (2, 3):
                _broadcast_sse("images_changed", {})

    except InterruptedError:
        _push_log_line(task_id, "INFO", "worker", "任务已取消")
        progress.finish("canceled", reason_code="task_canceled")
    except SystemExit as exc:
        _push_log_line(task_id, "ERR", "worker", f"Task exited: {exc}")
        progress.finish("failed", reason_code="task_exited")
    except Exception as exc:
        _push_log_line(task_id, "ERR", "worker", f"Task error: {exc}")
        progress.finish("failed", reason_code="unexpected_task_failure")

    finally:
        app_logger.removeHandler(sse_handler)
        sys.stdout    = orig_stdout
        sys.stderr    = orig_stderr
        builtins.input = orig_input


# ── Routes ─────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(FRONTEND_PUBLIC_DIR, "index.html")


@app.route("/frontend/<path:filename>")
def frontend_static(filename):
    if filename.startswith("dist/"):
        return send_from_directory(FRONTEND_DIST_DIR, filename.removeprefix("dist/"))
    return send_from_directory(FRONTEND_PUBLIC_DIR, filename)


@app.route("/api/run/<int:cmd>", methods=["POST"])
def api_run(cmd):
    if cmd not in CMD_LABELS:
        return _api_error("invalid_command")
    params = request.get_json(silent=True) or {}
    if not isinstance(params, dict):
        return _api_error("invalid_task_params", detail="任务参数必须是对象")
    params = dict(params)
    uses_pixiv = False
    label, target = CMD_LABELS[cmd]
    if cmd in (2, 3):
        try:
            targets = _validate_target_string(params.get("targets", "civitai,pixiv" if cmd == 2 else "pixiv"))
        except ValueError as exc:
            code = "target_required" if not str(params.get("targets", "")).strip() else "invalid_targets"
            return _api_error(code, detail=str(exc))
        files = params.get("files") or []
        if not isinstance(files, list):
            return _api_error("files_must_be_array", detail="files 必须是数组")
        normalized_files = [_plain_file_name(name) for name in files]
        if any(Path(name).suffix.lower() not in UPLOAD_IMAGE_SUFFIXES for name in normalized_files):
            return _api_error("unsupported_file_in_list", detail="files 中包含不支持的图片格式")
        params["targets"] = targets
        params["files"] = normalized_files
        uses_pixiv = "pixiv" in {part.strip().lower() for part in targets.split(",")}
        target = _target_label(targets)
        if params["files"]:
            label = f"发布 {len(params['files'])} 张图片"
    requested_total = len(params.get("files") or [])
    if not requested_total:
        try:
            requested_total = max(0, int(params.get("count") or 0))
        except (TypeError, ValueError):
            requested_total = 0
    task_id = uuid.uuid4().hex[:8]
    task = _new_task_record(
        task_id,
        cmd,
        params,
        title=label,
        target=target,
        total=requested_total,
    )
    t = threading.Thread(target=_run_task, args=(task_id, cmd, params), daemon=True)
    task["thread"] = t
    if uses_pixiv:
        with _pixiv_admission_lock:
            pixiv_snapshot = PIXIV_SESSION.snapshot()
            pixiv_owner = str(pixiv_snapshot.get("in_use_by") or "")
            if pixiv_owner or _has_active_pixiv_task():
                return _api_error(
                    "pixiv_profile_in_use",
                    409,
                    detail="已有 Pixiv 流程正在使用或等待使用 Profile",
                    owner=pixiv_owner or "publishing_task",
                )
            with TASKS_LOCK:
                TASKS[task_id] = task
    else:
        with TASKS_LOCK:
            TASKS[task_id] = task
    initial_snapshot = _task_snapshot(task)
    _broadcast_sse("task_update", initial_snapshot)

    try:
        t.start()
    except Exception as exc:
        _TaskProgressController(task_id, cmd, params).finish(
            "failed",
            reason_code="task_start_failed",
        )
        return _api_error("task_start_failed", 500, detail=str(exc), reason=str(exc))
    return jsonify({"task_id": task_id, "task": initial_snapshot})


@app.route("/api/tasks")
def api_tasks():
    with TASKS_LOCK:
        result = [_task_snapshot(task) for task in TASKS.values()]
    return jsonify(result)


@app.route("/api/tasks/<task_id>/cancel", methods=["POST"])
def api_cancel(task_id):
    with TASKS_LOCK:
        if task_id not in TASKS:
            return _api_error("task_not_found", 404, detail="not found")
        TASKS[task_id]["cancel_flag"] = True
        ev = TASKS[task_id].get("cancel_event")
        pending = TASKS[task_id].get("pending_input")
        snapshot = _task_snapshot(TASKS[task_id])
    if ev:
        ev.set()
    _broadcast_sse("task_update", snapshot)
    if pending:
        pending["result"][0] = ""
        pending["event"].set()
    return jsonify({"ok": True})


@app.route("/api/tasks/<task_id>/resume", methods=["POST"])
def api_resume(task_id):
    body = request.get_json(silent=True) or {}
    answer = body.get("answer", "\n")
    with TASKS_LOCK:
        pending = TASKS.get(task_id, {}).get("pending_input")
    if not pending:
        return _api_error("no_pending_input", 404, detail="no pending input")
    pending["result"][0] = answer
    pending["event"].set()
    return jsonify({"ok": True})


@app.route("/api/tasks/<task_id>/remove", methods=["POST"])
def api_remove(task_id):
    with TASKS_LOCK:
        if task_id not in TASKS:
            return _api_error("task_not_found", 404, detail="not found")
        if TASKS[task_id].get("status") in {"queued", "running", "waiting_input"}:
            return _api_error("active_task", 409, detail="运行中的任务需要先取消")
        del TASKS[task_id]
    _broadcast_sse("task_remove", {"id": task_id})
    return jsonify({"ok": True})


@app.route("/api/settings", methods=["POST"])
def api_settings():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _api_error("settings_must_be_object", detail="设置内容必须是对象")
    cfg = _load_config()
    if "api_key" in body:
        if not isinstance(body["api_key"], str):
            return _api_error("settings_must_be_object", detail="api_key must be a string")
        cfg["api_key"] = body["api_key"].strip()
        os.environ["CIVITAI_API_KEY"] = cfg["api_key"]
    _save_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/watermark-config", methods=["GET"])
def api_watermark_config_get():
    blocked = _watermark_local_only()
    if blocked is not None:
        return blocked
    try:
        return jsonify(_watermark_service().config_payload())
    except WatermarkError as exc:
        return _api_error("generic", detail=str(exc))


@app.route("/api/watermark-config", methods=["POST"])
def api_watermark_config_set():
    blocked = _watermark_local_only()
    if blocked is not None:
        return blocked
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _api_error("invalid_watermark_config", detail="invalid watermark configuration")
    try:
        service = _watermark_service()
        service.save_config(body)
        return jsonify({"ok": True, **service.config_payload()})
    except WatermarkError as exc:
        return _api_error("generic", detail=str(exc))


@app.route("/api/watermark-font", methods=["POST"])
def api_watermark_font_import():
    blocked = _watermark_local_only()
    if blocked is not None:
        return blocked
    uploaded = request.files.get("font")
    if uploaded is None or not uploaded.filename:
        return _api_error("font_required", detail="font file is required")
    try:
        data = uploaded.read(MAX_FONT_UPLOAD_BYTES + 1)
        service = _watermark_service()
        font = service.import_font(uploaded.filename, data)
        return jsonify({"ok": True, "font": font.to_dict(), **service.config_payload()})
    except WatermarkError as exc:
        return _api_error("generic", detail=str(exc))
    finally:
        uploaded.close()


@app.route("/api/watermark-font/<path:file_name>", methods=["DELETE"])
def api_watermark_font_delete(file_name: str):
    blocked = _watermark_local_only()
    if blocked is not None:
        return blocked
    try:
        service = _watermark_service()
        if not service.delete_font(file_name):
            return _api_error("font_not_found", 404, detail="font not found")
        return jsonify({"ok": True, **service.config_payload()})
    except WatermarkError as exc:
        return _api_error("generic", detail=str(exc))


@app.route("/api/watermark-image", methods=["POST"])
def api_watermark_image_import():
    blocked = _watermark_local_only()
    if blocked is not None:
        return blocked
    uploaded = request.files.get("image")
    if uploaded is None or not uploaded.filename:
        return _api_error("image_required", detail="image file is required")
    try:
        data = uploaded.read(MAX_IMAGE_UPLOAD_BYTES + 1)
        if len(data) > MAX_IMAGE_UPLOAD_BYTES:
            maximum_mb = MAX_IMAGE_UPLOAD_BYTES // (1024 * 1024)
            return _api_error(
                "watermark_image_too_large",
                detail=f"Watermark images must be smaller than {maximum_mb} MB",
                maximum_mb=maximum_mb,
            )
        service = _watermark_service()
        file_name = service.import_image(uploaded.filename, data)
        return jsonify({"ok": True, "file_name": file_name, **service.config_payload()})
    except WatermarkError as exc:
        return _api_error("generic", detail=str(exc))
    finally:
        uploaded.close()


@app.route("/api/watermark-image/<path:file_name>", methods=["DELETE"])
def api_watermark_image_delete(file_name: str):
    blocked = _watermark_local_only()
    if blocked is not None:
        return blocked
    try:
        service = _watermark_service()
        if not service.delete_image(file_name):
            return _api_error("image_not_found", 404, detail="image not found")
        return jsonify({"ok": True, **service.config_payload()})
    except WatermarkError as exc:
        return _api_error("generic", detail=str(exc))


@app.route("/api/watermark-image/<path:file_name>")
def api_watermark_image_get(file_name: str):
    """Serve the stored watermark image so the UI can preview it (PNG alpha intact)."""
    blocked = _watermark_local_only()
    if blocked is not None:
        return blocked
    try:
        path = _watermark_service().image_store.path_for(file_name)
    except WatermarkError as exc:
        return _api_error("generic", detail=str(exc))
    if not path.is_file():
        return _api_error("image_not_found", 404, detail="image not found")
    return send_from_directory(path.parent, path.name)


# Censor preset levels. Maps preset name → enabled_classes string. Class names
# come from pixiv/censor.py CENSOR_CLASS_NAMES: anus, cum, dick, tits, vagina.
_CENSOR_PRESETS = {
    "off":    [],
    "japan":  ["dick", "vagina", "anus", "cum"],
    "strict": ["dick", "vagina", "anus", "cum", "tits"],
}


@app.route("/api/censor-preset", methods=["POST"])
def api_censor_preset():
    """Switch the auto-censor preset (off / japan / strict).

    Rewrites the runtime Pixiv censor config plus the derived classes so the
    next upload sees the change without restarting the server.
    """
    body = request.get_json(silent=True) or {}
    preset = str(body.get("preset", "")).strip().lower()
    if preset not in _CENSOR_PRESETS:
        return _api_error("invalid_censor_preset", detail=f"unknown preset: {preset}")
    censor_path = _censor_config_path()
    existing = load_json(censor_path, {})
    if not isinstance(existing, dict):
        existing = {}
    existing["preset"] = preset
    existing["enabled_classes"] = list(_CENSOR_PRESETS[preset])
    save_json(censor_path, existing)
    return jsonify({"ok": True, "preset": preset, "enabled_classes": existing["enabled_classes"]})


@app.route("/api/censor-config", methods=["POST"])
def api_censor_config():
    """Update censor settings (mode, conf_threshold, bar_count)."""
    body = request.get_json(silent=True) or {}
    censor_path = _censor_config_path()
    existing = load_json(censor_path, {})
    if not isinstance(existing, dict):
        existing = {}
    if "mode" in body:
        m = str(body["mode"]).strip().lower()
        if m in {"mosaic", "blur", "bar", "heart"}:
            existing["mode"] = m
    if "conf_threshold" in body:
        try:
            v = float(body["conf_threshold"])
            existing["conf_threshold"] = max(0.1, min(0.95, round(v, 2)))
        except (ValueError, TypeError):
            pass
    if "bar_count" in body:
        try:
            existing["bar_count"] = max(1, min(8, int(body["bar_count"])))
        except (ValueError, TypeError):
            pass
    if "box_expand_default" in body:
        try:
            existing["box_expand_default"] = max(0.0, min(0.5, round(float(body["box_expand_default"]), 2)))
        except (ValueError, TypeError):
            pass
    if "box_expand" in body and isinstance(body["box_expand"], dict):
        existing["box_expand"] = {
            k: max(0.0, min(0.5, round(float(v), 2)))
            for k, v in body["box_expand"].items()
            if isinstance(k, str)
        }
    if "class_thresholds" in body and isinstance(body["class_thresholds"], dict):
        existing["class_thresholds"] = {
            k: max(0.05, min(0.95, round(float(v), 2)))
            for k, v in body["class_thresholds"].items()
            if isinstance(k, str)
        }
    if "secondary_enabled" in body:
        existing["secondary_enabled"] = bool(body["secondary_enabled"])
    if "secondary_conf" in body:
        try:
            existing["secondary_conf"] = max(0.05, min(0.95, round(float(body["secondary_conf"]), 2)))
        except (ValueError, TypeError):
            pass
    save_json(censor_path, existing)
    _resp_keys = ("mode", "conf_threshold", "bar_count", "box_expand_default", "box_expand",
                  "class_thresholds", "secondary_enabled", "secondary_conf")
    return jsonify({"ok": True, **{k: existing.get(k) for k in _resp_keys}})


@app.route("/api/llm-reverse-platforms", methods=["GET"])
def api_llm_reverse_platforms():
    return jsonify({pid: dict(spec, id=pid) for pid, spec in PLATFORM_SPECS.items()})


@app.route("/api/llm-reverse-models", methods=["GET"])
def api_llm_reverse_models():
    import urllib.request as _ur
    import urllib.error as _ue
    provider = request.args.get("provider", "")
    api_key  = request.args.get("api_key", "").strip()
    base_url = request.args.get("base_url", "").rstrip("/")

    # 空 api_key 时 fallback 到 saved（用户保存过但密码框看不到原值）
    if not api_key:
        saved = normalize_llm_reverse_config(_load_config().get("llm_reverse"))
        api_key = str(saved.get("api_key", ""))

    if provider == "anthropic":
        return jsonify({"models": [
            "claude-opus-4-7",
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
        ]})

    if provider == "google_gemini":
        if not api_key:
            return _api_error("api_key_required", detail="需要先填写或保存 API key")
        # 支持用户自定义 base_url（本地代理），缺省走官方
        gemini_base = base_url or "https://generativelanguage.googleapis.com"
        url = f"{gemini_base}/v1beta/models?key={api_key}"
        try:
            with _ur.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
            # Google 官方格式: {"models": [{"name":"models/X", "supportedGenerationMethods":[...]}]}
            models = [
                m["name"].split("/")[-1]
                for m in data.get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])
            ]
            if not models:
                # OpenAI 兼容代理格式: {"object":"list", "data":[{"id":"X"}]}
                models = sorted({str(m.get("id", "")) for m in data.get("data", []) if m.get("id")})
            return jsonify({"models": models})
        except _ue.HTTPError as e:
            return _api_error("upstream_http", 502, detail=f"上游返回 {e.code}（检查 API key 或代理是否可用）", status=e.code)
        except Exception as e:
            return _api_error("connection_failed", 502, detail=f"无法连接：{e}", reason=str(e))

    # openai_compatible
    if not base_url:
        return _api_error("base_url_required", detail="需要填写 base URL")
    try:
        req = _ur.Request(
            f"{base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        )
        with _ur.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        ids = sorted(m["id"] for m in data.get("data", []) if "id" in m)
        return jsonify({"models": ids})
    except _ue.HTTPError as e:
        return _api_error("upstream_http", 502, detail=f"上游返回 {e.code}（检查 API key 或 base URL）", status=e.code)
    except Exception as e:
        return _api_error("connection_failed", 502, detail=f"无法连接：{e}", reason=str(e))


@app.route("/api/llm-reverse-config", methods=["GET"])
def api_llm_reverse_config_get():
    cfg = _load_config()
    return jsonify(mask_llm_config(cfg.get("llm_reverse")))


@app.route("/api/llm-reverse-config", methods=["POST"])
def api_llm_reverse_config_post():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return _api_error("settings_must_be_object")
    cfg = _load_config()
    current = normalize_llm_reverse_config(cfg.get("llm_reverse"))
    if isinstance(body.get("retry_policy"), dict):
        body["retry_policy"] = {**current.get("retry_policy", {}), **body["retry_policy"]}
    if body.pop("clear_api_key", False):
        body["api_key"] = ""
    else:
        api_key = body.get("api_key", None)
        if api_key == "":
            body.pop("api_key", None)
        elif api_key is None:
            body["api_key"] = current.get("api_key", "")
    next_cfg = normalize_llm_reverse_config({**current, **body})
    errors = validate_llm_reverse_config(next_cfg)
    if errors:
        reason = "; ".join(errors)
        return _api_error("generic", detail=reason, reason=reason)
    cfg["llm_reverse"] = next_cfg
    _save_config(cfg)
    return jsonify(mask_llm_config(next_cfg))


def _list_upload_dir(folder: Path, source: str) -> list:
    if not folder.exists():
        return []
    return [
        {"name": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime, "source": source}
        for f in sorted(folder.iterdir())
        if f.is_file() and f.suffix.lower() in UPLOAD_IMAGE_SUFFIXES
    ]


def _active_upload_references() -> tuple[set[str], bool]:
    referenced: set[str] = set()
    blocks_all = False
    with TASKS_LOCK:
        active_tasks = [
            task
            for task in TASKS.values()
            if task.get("cmd") in (2, 3)
            and task.get("status") in {"queued", "running", "waiting_input"}
        ]
        for task in active_tasks:
            files = (task.get("params") or {}).get("files")
            if not isinstance(files, list) or not files:
                blocks_all = True
                continue
            referenced.update(os.path.normcase(Path(str(name)).name) for name in files)
    return referenced, blocks_all


@app.route("/api/images")
def api_images():
    return jsonify(_list_upload_dir(SCRIPT_DIR / "upload", "upload"))


@app.route("/api/images", methods=["DELETE"])
def api_images_delete():
    body = request.get_json(silent=True) or {}
    files = body.get("files") if isinstance(body, dict) else None
    if not isinstance(files, list):
        return _api_error("files_must_be_array", detail="files 必须是数组")

    upload_dir = SCRIPT_DIR / "upload"
    upload_root = upload_dir.resolve()
    names: list[str] = []
    candidates: dict[str, Path] = {}
    invalid: list[str] = []
    for value in files:
        name = value if isinstance(value, str) else ""
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or "\x00" in name
            or Path(name).suffix.lower() not in UPLOAD_IMAGE_SUFFIXES
        ):
            invalid.append(str(value))
            continue
        if name in candidates:
            continue
        candidate = upload_dir / name
        try:
            outside_upload = candidate.resolve().parent != upload_root
        except (OSError, RuntimeError):
            outside_upload = True
        if candidate.is_symlink() or outside_upload:
            invalid.append(name)
            continue
        names.append(name)
        candidates[name] = candidate
    if invalid:
        invalid_names = ", ".join(invalid)
        return _api_error("invalid_upload_file", detail=f"图片名称无效：{invalid_names}", files=invalid_names)
    if not names:
        return _api_error("files_required", detail="至少选择一张图片")

    referenced, blocks_all = _active_upload_references()
    in_use = [name for name in names if blocks_all or os.path.normcase(name) in referenced]
    if in_use:
        in_use_names = ", ".join(in_use)
        return _api_error(
            "upload_files_in_use",
            409,
            detail=f"图片正在发布，无法删除：{in_use_names}",
            files=in_use_names,
        )

    deleted: list[str] = []
    missing: list[str] = []
    failed: list[str] = []
    for name in names:
        candidate = candidates[name]
        if not candidate.exists():
            missing.append(name)
            continue
        if not candidate.is_file():
            failed.append(name)
            continue
        try:
            candidate.unlink()
            deleted.append(name)
        except OSError:
            failed.append(name)
    if deleted or missing:
        _broadcast_sse("images_changed", {})
    if failed:
        failed_names = ", ".join(failed)
        return _api_error(
            "upload_delete_failed",
            500,
            detail=f"无法删除以下图片：{failed_names}",
            files=failed_names,
            deleted=deleted,
            missing=missing,
        )
    return jsonify({"deleted": deleted, "missing": missing})


@app.route("/upload/<path:filename>")
def upload_file(filename):
    return send_from_directory(SCRIPT_DIR / "upload", filename)


@app.route("/api/add-upload-files", methods=["POST"])
def api_add_upload_files():
    upload_dir = SCRIPT_DIR / "upload"
    upload_dir.mkdir(parents=True, exist_ok=True)
    uploads = [uploaded for uploaded in request.files.getlist("files") if uploaded.filename]
    rejected = [
        Path(uploaded.filename).name or uploaded.filename
        for uploaded in uploads
        if Path(Path(uploaded.filename).name).suffix.lower() not in UPLOAD_IMAGE_SUFFIXES
    ]
    if rejected:
        names = ", ".join(rejected)
        return _api_error("unsupported_uploads", detail=f"不支持的图片格式：{names}", files=names)
    if not uploads:
        return _api_error("no_usable_images", detail="没有收到可用图片")
    saved: list[str] = []
    for uploaded in uploads:
        file_name = Path(uploaded.filename).name
        destination = upload_dir / file_name
        suffix_index = 2
        while destination.exists():
            destination = upload_dir / f"{Path(file_name).stem} ({suffix_index}){Path(file_name).suffix}"
            suffix_index += 1
        uploaded.save(str(destination))
        saved.append(destination.name)
    _broadcast_sse("images_changed", {})
    return jsonify({"saved": saved})


@app.route("/api/open-folder")
def api_open_folder():
    upload_dir = SCRIPT_DIR / "upload"
    upload_dir.mkdir(exist_ok=True)
    try:
        os.startfile(str(upload_dir))
    except Exception as exc:
        return _api_error("generic", 500, detail=str(exc), reason=str(exc))
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    model_path  = SCRIPT_DIR / "models" / "auto_censor.pt"
    upload_dir  = SCRIPT_DIR / "upload"
    upload_count = sum(1 for f in upload_dir.iterdir() if f.is_file() and f.suffix.lower() in UPLOAD_IMAGE_SUFFIXES) if upload_dir.exists() else 0
    api_key = os.environ.get("CIVITAI_API_KEY", "")
    if len(api_key) > 4:
        masked = "*" * (len(api_key) - 4) + api_key[-4:]
    else:
        masked = "*" * len(api_key)
    cfg = _load_config()
    watermark_enabled = False
    watermark_font_file = ""
    watermark_renderer = "text"
    watermark_file = ""
    watermark_status = "disabled"
    try:
        watermark_spec = _watermark_service().load_config()
        watermark_enabled = watermark_spec.enabled
        watermark_renderer = watermark_spec.renderer
        if watermark_spec.renderer == "image":
            watermark_file = getattr(watermark_spec, "file_name", "")
        else:
            watermark_font_file = getattr(watermark_spec.font, "file_name", "")
        watermark_status = "enabled" if watermark_enabled else "disabled"
    except WatermarkError:
        watermark_status = "invalid"
    llm_cfg = normalize_llm_reverse_config(cfg.get("llm_reverse"))
    llm_key = str(llm_cfg.get("api_key", ""))
    llm_masked = "*" * (len(llm_key) - 4) + llm_key[-4:] if len(llm_key) > 4 else "*" * len(llm_key)
    censor_path = _censor_config_path()
    censor_preset = "japan"
    censor_mode = "mosaic"
    censor_conf = 0.55
    censor_bar_count = 4
    censor_box_expand_default = 0.0
    censor_box_expand = {}
    censor_class_thresholds = {}
    censor_secondary_enabled = False
    try:
        if censor_path.exists():
            cdata = json.loads(censor_path.read_text(encoding="utf-8"))
            if isinstance(cdata, dict):
                p = str(cdata.get("preset", "")).strip().lower()
                if p in _CENSOR_PRESETS:
                    censor_preset = p
                m = str(cdata.get("mode", "")).strip().lower()
                if m in {"mosaic", "blur", "bar", "heart"}:
                    censor_mode = m
                if "conf_threshold" in cdata:
                    censor_conf = float(cdata["conf_threshold"])
                if "bar_count" in cdata:
                    censor_bar_count = int(cdata["bar_count"])
                if "box_expand_default" in cdata:
                    censor_box_expand_default = float(cdata["box_expand_default"])
                if isinstance(cdata.get("box_expand"), dict):
                    censor_box_expand = cdata["box_expand"]
                if isinstance(cdata.get("class_thresholds"), dict):
                    censor_class_thresholds = cdata["class_thresholds"]
                censor_secondary_enabled = bool(cdata.get("secondary_enabled", False))
    except Exception:
        pass
    from .version import __version__
    pixiv_session = _pixiv_session_payload()
    return jsonify({
        "version":           __version__,
        "mosaic_installed":  model_path.exists() and _censor_deps_ok(),
        "mosaic_model_exists": model_path.exists(),
        "censor_deps_ok":   _censor_deps_ok(),
        "upload_count":      upload_count,
        "has_api_key":       bool(api_key),
        "api_key_masked":    masked,
        "pixiv_session":     pixiv_session,
        "pixiv_logged_in":   pixiv_session.get("state") == "authenticated",
        "civitai_logged_in": CIVITAI_PROFILE_DIR.exists(),
        "scheduler":         _scheduler_from_config(cfg),
        "llm_reverse_enabled": bool(llm_cfg.get("enabled")),
        "llm_reverse_configured": bool(
            llm_cfg.get("api_key")
            and llm_cfg.get("model")
            and (llm_cfg.get("base_url") or llm_cfg.get("provider") in {"anthropic", "google_gemini"})
        ),
        "llm_reverse_model": llm_cfg.get("model", ""),
        "llm_reverse_api_key_masked": llm_masked,
        "censor_preset":      censor_preset,
        "censor_mode":        censor_mode,
        "censor_conf_threshold": censor_conf,
        "censor_bar_count":   censor_bar_count,
        "censor_box_expand_default": censor_box_expand_default,
        "censor_box_expand":  censor_box_expand,
        "censor_class_thresholds": censor_class_thresholds,
        "censor_secondary_enabled": censor_secondary_enabled,
        "watermark_enabled":  watermark_enabled,
        "watermark_font_file": watermark_font_file,
        "watermark_renderer":  watermark_renderer,
        "watermark_file":      watermark_file,
        "watermark_status":   watermark_status,
        "upload_defaults":    _upload_defaults_from_config(cfg),
    })


@app.route("/api/tagger-config", methods=["GET"])
def api_tagger_config_get():
    cfg = _load_config()
    ht = _load_haintag_settings()
    haintag_root = cfg.get("haintag_root", "")
    model_dir = ht.get("tagger_model_dir", "")
    pixai_dir = ht.get("pixai_tagger_model_dir", "")

    haintag_ok = False
    if haintag_root:
        root_p = Path(haintag_root)
        haintag_ok = (root_p / "native_app" / "tagger.py").exists() or \
                     (root_p / "_internal" / "native_app" / "tagger_subprocess.py").exists()

    model_ok = resolve_cl_model_dir(ht) is not None
    pixai_ok = resolve_pixai_model_dir(ht) is not None

    return jsonify({
        "haintag_root": haintag_root,
        "haintag_ok": haintag_ok,
        "model_dir": model_dir,
        "model_ok": model_ok,
        "pixai_model_dir": pixai_dir,
        "pixai_ok": pixai_ok,
        "needs_setup": not model_ok and not pixai_ok,
    })


@app.route("/api/tagger-config", methods=["POST"])
def api_tagger_config_post():
    body = request.get_json(silent=True) or {}
    changed = []

    if "haintag_root" in body:
        cfg = _load_config()
        val = body["haintag_root"].strip()
        if val:
            cfg["haintag_root"] = val
        else:
            cfg.pop("haintag_root", None)
        _save_config(cfg)
        changed.append("haintag_root")

    if "model_dir" in body:
        val = body["model_dir"].strip()
        _save_haintag_settings({"tagger_model_dir": val})
        changed.append("model_dir")

    if "pixai_model_dir" in body:
        val = body["pixai_model_dir"].strip()
        _save_haintag_settings({"pixai_tagger_model_dir": val})
        changed.append("pixai_model_dir")

    return jsonify({"ok": True, "changed": changed})


@app.route("/api/install-pixai-tagger", methods=["POST"])
def api_install_pixai_tagger():
    body = request.get_json(silent=True) or {}
    target_dir = body.get("target_dir", "").strip()
    if not target_dir:
        target_dir = str(SCRIPT_DIR / "models" / "pixai_tagger")

    task_id = uuid.uuid4().hex[:8]

    def _do_download():
        try:
            try:
                from huggingface_hub import snapshot_download
            except ImportError:
                with _pixai_tasks_lock:
                    _pixai_tasks[task_id] = {"status": "error", "error": "huggingface_hub 未安装，请先 pip install huggingface_hub", "error_code": "dependency_missing", "error_params": {"dependency": "huggingface_hub"}}
                return
            snapshot_download(
                repo_id="deepghs/pixai-tagger-v0.9-onnx",
                local_dir=target_dir,
                ignore_patterns=["*.md", ".git*"],
            )
            _save_haintag_settings({"pixai_tagger_model_dir": target_dir})
            with _pixai_tasks_lock:
                _pixai_tasks[task_id] = {"status": "done", "model_dir": target_dir}
        except Exception as exc:
            with _pixai_tasks_lock:
                _pixai_tasks[task_id] = {"status": "error", "error": str(exc)}

    with _pixai_tasks_lock:
        _pixai_tasks[task_id] = {"status": "running", "target_dir": target_dir}

    threading.Thread(target=_do_download, daemon=True).start()
    return jsonify({"ok": True, "task_id": task_id, "target_dir": target_dir})


@app.route("/api/install-pixai-tagger-status/<task_id>")
def api_install_pixai_tagger_status(task_id):
    with _pixai_tasks_lock:
        state = _pixai_tasks.get(task_id)
    if not state:
        return _api_error("task_not_found", 404, detail="not found")
    return jsonify(state)


@app.route("/api/install-cl-tagger", methods=["POST"])
def api_install_cl_tagger():
    body = request.get_json(silent=True) or {}
    target_dir = body.get("target_dir", "").strip()
    if not target_dir:
        target_dir = str(SCRIPT_DIR / "models" / "cl_tagger")

    task_id = uuid.uuid4().hex[:8]

    def _do_download():
        try:
            try:
                from huggingface_hub import snapshot_download
            except ImportError:
                with _pixai_tasks_lock:
                    _pixai_tasks[task_id] = {"status": "error", "error": "huggingface_hub 未安装，请先 pip install huggingface_hub", "error_code": "dependency_missing", "error_params": {"dependency": "huggingface_hub"}}
                return
            snapshot_download(
                repo_id="SmilingWolf/wd-vit-tagger-v3",
                local_dir=target_dir,
                ignore_patterns=["*.md", ".git*"],
            )
            _save_haintag_settings({"tagger_model_dir": target_dir})
            with _pixai_tasks_lock:
                _pixai_tasks[task_id] = {"status": "done", "model_dir": target_dir}
        except Exception as exc:
            with _pixai_tasks_lock:
                _pixai_tasks[task_id] = {"status": "error", "error": str(exc)}

    with _pixai_tasks_lock:
        _pixai_tasks[task_id] = {"status": "running", "target_dir": target_dir}

    threading.Thread(target=_do_download, daemon=True).start()
    return jsonify({"ok": True, "task_id": task_id, "target_dir": target_dir})


@app.route("/api/install-cl-tagger-status/<task_id>")
def api_install_cl_tagger_status(task_id):
    with _pixai_tasks_lock:
        state = _pixai_tasks.get(task_id)
    if not state:
        return _api_error("task_not_found", 404, detail="not found")
    return jsonify(state)


def _has_active_pixiv_task() -> bool:
    with TASKS_LOCK:
        return any(
            task.get("status") in {"queued", "running", "waiting_input"}
            and "pixiv" in {
                part.strip().lower()
                for part in str((task.get("params") or {}).get("targets", "")).split(",")
                if part.strip()
            }
            for task in TASKS.values()
        )


def _pixiv_session_response(*, ok: bool = True):
    session = _pixiv_session_payload()
    return jsonify({
        "ok": ok,
        "pixiv_session": session,
        "pixiv_logged_in": session.get("state") == "authenticated",
    })


def _pixiv_login_worker(lease, cancel_event: threading.Event) -> None:
    global _pixiv_login_thread, _pixiv_login_cancel
    try:
        from patchright.sync_api import sync_playwright

        with sync_playwright() as pw:
            run_pixiv_login_flow(
                pw,
                cancel_event=cancel_event,
                owner="login:web",
                lease=lease,
            )
    except PixivFlowError as exc:
        if exc.code == "pixiv_login_canceled":
            logging.getLogger(__name__).info("Pixiv 登录流程已取消")
        else:
            logging.getLogger(__name__).warning("Pixiv 登录流程结束 [%s]: %s", exc.code, exc)
    except Exception as exc:
        try:
            PIXIV_SESSION.update_verified(
                "error",
                error_code="pixiv_login_error",
                error=f"{type(exc).__name__}: {exc}",
            )
        except OSError:
            logging.getLogger(__name__).exception("Pixiv 登录流程异常且无法持久化错误状态")
        else:
            logging.getLogger(__name__).exception("Pixiv 登录流程异常")
    finally:
        PIXIV_SESSION.release(lease)
        with _pixiv_login_lock:
            if threading.current_thread() is _pixiv_login_thread:
                _pixiv_login_thread = None
                _pixiv_login_cancel = None
        _broadcast_pixiv_session(PIXIV_SESSION.snapshot())


@app.route("/api/pixiv-logout", methods=["POST"])
def api_pixiv_logout():
    with _pixiv_admission_lock:
        if _has_active_pixiv_task():
            return _api_error(
                "pixiv_profile_in_use",
                409,
                detail="已有未结束的 Pixiv 发布任务",
                owner="publishing_task",
            )
        try:
            lease = PIXIV_SESSION.acquire("logout:web")
        except PixivProfileInUseError as exc:
            return _api_error(
                "pixiv_profile_in_use",
                409,
                detail="Pixiv Profile 正在使用中",
                owner=exc.owner,
            )
    try:
        if PIXIV_PROFILE_DIR.exists():
            shutil.rmtree(PIXIV_PROFILE_DIR)
        PIXIV_SESSION.clear()
    except OSError as exc:
        profile_still_exists = PIXIV_PROFILE_DIR.exists()
        try:
            if profile_still_exists:
                PIXIV_SESSION.update_verified(
                    "error",
                    error_code="pixiv_profile_clear_failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
            else:
                PIXIV_SESSION.clear()
        except OSError:
            logging.getLogger(__name__).exception("Pixiv Profile 清除失败且无法同步会话状态")
        return _api_error(
            "pixiv_profile_clear_failed",
            500,
            detail=str(exc),
            reason=str(exc),
        )
    finally:
        PIXIV_SESSION.release(lease)
    return _pixiv_session_response()


@app.route("/api/civitai-logout", methods=["POST"])
def api_civitai_logout():
    with TASKS_LOCK:
        running_civitai = any(
            t.get("status") in {"running", "waiting_input"} and t.get("cmd") in (1, 2)
            for t in TASKS.values()
        )
    if running_civitai:
        return _api_error("profile_in_use", detail="civitai task is running", platform="Civitai")
    shutil.rmtree(CIVITAI_PROFILE_DIR, ignore_errors=True)
    return jsonify({"ok": True})


@app.route("/api/pixiv-open-login", methods=["POST"])
def api_pixiv_open_login():
    global _pixiv_login_thread, _pixiv_login_cancel
    with _pixiv_admission_lock:
        if _has_active_pixiv_task():
            return _api_error(
                "pixiv_profile_in_use",
                409,
                detail="已有未结束的 Pixiv 发布任务",
                owner="publishing_task",
            )
        with _pixiv_login_lock:
            if _pixiv_login_thread is not None and _pixiv_login_thread.is_alive():
                return _pixiv_session_response()
            try:
                lease = PIXIV_SESSION.acquire("login:web")
            except PixivProfileInUseError as exc:
                return _api_error(
                    "pixiv_profile_in_use",
                    409,
                    detail=str(exc),
                    owner=exc.owner,
                )
            cancel_event = threading.Event()
            thread = threading.Thread(
                target=_pixiv_login_worker,
                args=(lease, cancel_event),
                name="pixiv-login",
                daemon=True,
            )
            _pixiv_login_thread = thread
            _pixiv_login_cancel = cancel_event
            try:
                thread.start()
            except Exception as exc:
                _pixiv_login_thread = None
                _pixiv_login_cancel = None
                PIXIV_SESSION.release(lease)
                return _api_error("pixiv_login_start_failed", 500, detail=str(exc), reason=str(exc))
    return _pixiv_session_response()


@app.route("/api/pixiv-login-cancel", methods=["POST"])
def api_pixiv_login_cancel():
    with _pixiv_login_lock:
        cancel_event = _pixiv_login_cancel
        thread = _pixiv_login_thread
    if cancel_event is not None and thread is not None and thread.is_alive():
        cancel_event.set()
    return _pixiv_session_response()


@app.route("/api/civitai-open-login", methods=["POST"])
def api_civitai_open_login():
    try:
        _open_login_browser(CIVITAI_PROFILE_DIR, "https://civitai.com/login?returnUrl=/")
    except Exception as exc:
        logging.getLogger(__name__).warning(f"civitai login browser: {exc}")
        return _api_error("generic", 500, detail=str(exc), reason=str(exc))
    return jsonify({"ok": True})


@app.route("/api/upload-defaults", methods=["GET"])
def api_upload_defaults_get():
    return jsonify(_upload_defaults_from_config(_load_config()))


@app.route("/api/upload-defaults", methods=["POST"])
def api_upload_defaults_set():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _api_error("settings_must_be_object", detail="设置内容必须是对象")
    cfg = _load_config()
    current = _upload_defaults_from_config(cfg)
    merged = {**current, **data}
    try:
        if "targets" in data:
            merged["targets"] = _validate_target_string(data["targets"])
    except ValueError as exc:
        return _api_error("invalid_targets", detail=str(exc))
    cfg["upload_defaults"] = merged
    cfg["upload_defaults"] = _upload_defaults_from_config(cfg)
    _save_config(cfg)
    return jsonify({"ok": True, "upload_defaults": cfg["upload_defaults"]})


def _sched_default() -> dict:
    return {
        "enabled": False,
        "targets": "civitai,pixiv",
        "count": 1,
        "sort": "random",
        "min_hours": 0.4,
        "max_hours": 0.8,
        "next_fire_at": None,
        "llm_reverse": False,
        "llm_persona": "",
        "llm_content_mode": "",
        "ai_tags_by_platform": {"pixiv": True},
    }


def _target_ids(value: object) -> list[str]:
    values = value if isinstance(value, (list, tuple, set)) else str(value or "").split(",")
    requested = [str(item).strip().lower() for item in values if str(item).strip()]
    return list(dict.fromkeys(requested))


def _normalize_target_string(value: object) -> str:
    targets = [item for item in _target_ids(value) if item in {"civitai", "pixiv"}]
    return ",".join(targets) or "civitai,pixiv"


def _validate_target_string(value: object) -> str:
    requested = _target_ids(value)
    unsupported = [item for item in requested if item not in {"civitai", "pixiv"}]
    if unsupported:
        raise ValueError(f"不支持的发布平台：{', '.join(unsupported)}")
    if not requested:
        raise ValueError("至少选择一个发布平台")
    return ",".join(requested)


def _target_label(value: object) -> str:
    targets = _target_ids(value)
    return " + ".join("Civitai" if item == "civitai" else "Pixiv" for item in targets)


def _upload_defaults_from_config(cfg: dict) -> dict:
    raw = cfg.get("upload_defaults") if isinstance(cfg.get("upload_defaults"), dict) else {}
    sort_mode = str(raw.get("sort", raw.get("sort_mode", "time_desc")))
    if sort_mode not in {"random", "manual", "name_asc", "name_desc", "time_asc", "time_desc"}:
        sort_mode = "time_desc"
    content_mode = str(raw.get("llm_content_mode", ""))
    if content_mode not in {"", "sfw", "nsfw"}:
        content_mode = ""
    ai_tags = raw.get("ai_tags_by_platform")
    return {
        "targets": _normalize_target_string(raw.get("targets", "civitai,pixiv")),
        "sort": sort_mode,
        "llm_reverse": bool(raw.get("llm_reverse", False)),
        "llm_persona": str(raw.get("llm_persona", "")),
        "llm_content_mode": content_mode,
        "ai_tags_by_platform": {
            "pixiv": bool(ai_tags.get("pixiv", True)) if isinstance(ai_tags, dict) else True,
        },
    }


def _scheduler_from_config(cfg: dict) -> dict:
    defaults = _sched_default()
    stored = cfg.get("scheduler") if isinstance(cfg.get("scheduler"), dict) else {}
    sched = {key: stored.get(key, value) for key, value in defaults.items()}
    sched["targets"] = _normalize_target_string(sched["targets"])
    ai_tags = sched.get("ai_tags_by_platform")
    sched["ai_tags_by_platform"] = {
        "pixiv": bool(ai_tags.get("pixiv", True)) if isinstance(ai_tags, dict) else True
    }
    return sched


def _broadcast_scheduler(sched: dict) -> None:
    _broadcast_sse("scheduler_update", dict(sched))


def _arm_scheduler(cfg: dict) -> None:
    global _scheduler_timer
    sched = _scheduler_from_config(cfg)
    with _scheduler_lock:
        if _scheduler_timer is not None:
            _scheduler_timer.cancel()
            _scheduler_timer = None
        if sched.get("enabled"):
            now = datetime.now()
            delay: float | None = None
            next_fire = sched.get("next_fire_at")
            if next_fire:
                try:
                    rem = (datetime.fromisoformat(next_fire) - now).total_seconds()
                    if rem > 0:
                        delay = rem
                except Exception:
                    pass
            if delay is None:
                min_h = max(0.001, float(sched.get("min_hours", 1.0)))
                max_h = max(min_h, float(sched.get("max_hours", 3.0)))
                delay = random.uniform(min_h, max_h) * 3600
                sched["next_fire_at"] = (now + timedelta(seconds=delay)).isoformat(timespec="seconds")
            cfg["scheduler"] = sched
            _save_config(cfg)
            t = threading.Timer(delay, _scheduler_fire)
            t.daemon = True
            _scheduler_timer = t
            t.start()
    _broadcast_scheduler(sched)


def _scheduler_fire() -> None:
    global _scheduler_timer
    with _scheduler_lock:
        _scheduler_timer = None
    cfg = _load_config()
    sched = _scheduler_from_config(cfg)
    if not sched.get("enabled"):
        return
    upload_dir = SCRIPT_DIR / "upload"
    has_images = upload_dir.exists() and any(
        f.is_file() and f.suffix.lower() in UPLOAD_IMAGE_SUFFIXES for f in upload_dir.iterdir()
    )
    sched["next_fire_at"] = None
    cfg["scheduler"] = sched
    _save_config(cfg)
    targets_str = sched.get("targets", "civitai,pixiv")
    targets_pixiv = "pixiv" in {part.strip().lower() for part in targets_str.split(",")}
    pixiv_busy = False
    admitted = False
    task_id = ""
    task = None
    thread = None
    if has_images:
        count = max(1, int(sched.get("count", 1)))
        sort_mode = sched.get("sort", "random")
        tl = targets_str.lower()
        cmd = 3 if ("pixiv" in tl and "civitai" not in tl) else 2
        params = {
            "count": count, "files": [], "targets": targets_str, "sort": sort_mode,
            "llm_reverse": bool(sched.get("llm_reverse")),
            "llm_persona": sched.get("llm_persona", ""),
            "llm_content_mode": sched.get("llm_content_mode", ""),
            "ai_tags_by_platform": sched.get("ai_tags_by_platform") or {},
        }
        task_id = uuid.uuid4().hex[:8]
        task = _new_task_record(
            task_id,
            cmd,
            params,
            title=f"自动发布 {count} 张图片",
            target=_target_label(targets_str),
            total=count,
        )
        thread = threading.Thread(target=_run_task, args=(task_id, cmd, params), daemon=True)
        task["thread"] = thread

        if targets_pixiv:
            with _pixiv_admission_lock:
                pixiv_owner = str(PIXIV_SESSION.snapshot().get("in_use_by") or "")
                with TASKS_LOCK:
                    any_running = any(
                        item.get("status") in {"running", "queued", "waiting_input"}
                        for item in TASKS.values()
                    )
                    pixiv_task_busy = any(
                        item.get("status") in {"running", "queued", "waiting_input"}
                        and "pixiv" in {
                            part.strip().lower()
                            for part in str((item.get("params") or {}).get("targets", "")).split(",")
                            if part.strip()
                        }
                        for item in TASKS.values()
                    )
                    pixiv_busy = bool(pixiv_owner) or pixiv_task_busy
                    if not any_running and not pixiv_busy:
                        TASKS[task_id] = task
                        admitted = True
        else:
            with TASKS_LOCK:
                any_running = any(
                    item.get("status") in {"running", "queued", "waiting_input"}
                    for item in TASKS.values()
                )
                if not any_running:
                    TASKS[task_id] = task
                    admitted = True

    if admitted and task is not None and thread is not None:
        _broadcast_sse("task_update", _task_snapshot(task))
        try:
            thread.start()
        except Exception as exc:
            app.logger.exception("定时发布任务线程启动失败: %s", exc)
            _set_task_status(task_id, "failed")
    elif pixiv_busy:
        app.logger.info("Pixiv Profile 正在使用中，本轮定时发布已安全延后")
    _arm_scheduler(cfg)


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    _schedule_idle_shutdown(force=True)
    return jsonify({"ok": True})


@app.route("/api/scheduler", methods=["POST"])
def api_scheduler():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _api_error("settings_must_be_object", detail="设置内容必须是对象")
    cfg = _load_config()
    sched = _scheduler_from_config(cfg)
    try:
        if "enabled" in body:
            sched["enabled"] = bool(body["enabled"])
        if "min_hours" in body:
            sched["min_hours"] = max(0.001, float(body["min_hours"]))
        if "max_hours" in body:
            sched["max_hours"] = max(0.001, float(body["max_hours"]))
        if "count" in body:
            sched["count"] = max(1, min(100, int(body["count"])))
        if "targets" in body:
            sched["targets"] = _validate_target_string(body["targets"])
    except (TypeError, ValueError) as exc:
        return _api_error("invalid_scheduler", detail=str(exc) or "定时发布参数无效")
    if "sort" in body:
        sched["sort"] = body["sort"] if body["sort"] in ("random", "name_asc", "name_desc", "time_asc", "time_desc") else "random"
    if "llm_reverse" in body:
        sched["llm_reverse"] = bool(body["llm_reverse"])
    if "llm_persona" in body:
        sched["llm_persona"] = str(body["llm_persona"])
    if "llm_content_mode" in body:
        sched["llm_content_mode"] = body["llm_content_mode"] if body["llm_content_mode"] in ("sfw", "nsfw", "") else ""
    if "ai_tags_by_platform" in body and isinstance(body["ai_tags_by_platform"], dict):
        sched["ai_tags_by_platform"] = {"pixiv": bool(body["ai_tags_by_platform"].get("pixiv", True))}
    if "pixiv" not in _target_ids(sched["targets"]):
        sched["llm_reverse"] = False
    if sched.get("min_hours", 1.0) > sched.get("max_hours", 3.0):
        return _api_error("invalid_interval", detail="min_hours > max_hours")
    if any(k in body for k in ("enabled", "min_hours", "max_hours")):
        sched["next_fire_at"] = None
    cfg["scheduler"] = sched
    _save_config(cfg)
    if sched.get("enabled"):
        _arm_scheduler(cfg)
    else:
        with _scheduler_lock:
            global _scheduler_timer
            if _scheduler_timer is not None:
                _scheduler_timer.cancel()
                _scheduler_timer = None
        _broadcast_scheduler(sched)
    return jsonify({"ok": True, "scheduler": sched})


@app.route("/api/stream")
def api_stream():
    client_q: queue.Queue = queue.Queue(maxsize=500)
    _cancel_idle_shutdown()
    with CLIENTS_LOCK:
        SSE_CLIENTS.append(client_q)

    def generate():
        # Push current state snapshot on connect
        with TASKS_LOCK:
            all_tasks = [_task_snapshot(task) for task in TASKS.values()]
            recent_logs = []
            for t in TASKS.values():
                recent_logs.extend(t.get("log_lines", [])[-10:])
        recent_logs.sort(key=lambda x: x.get("t", ""))

        pending_inputs = []
        with TASKS_LOCK:
            for t in TASKS.values():
                pi = t.get("pending_input")
                if pi:
                    pending_inputs.append({"task_id": t["id"], "prompt": pi.get("prompt", "")})

        for snap in all_tasks:
            yield f"event: task_update\ndata: {json.dumps(snap, ensure_ascii=False)}\n\n"
        yield f"event: scheduler_update\ndata: {json.dumps(_scheduler_from_config(_load_config()), ensure_ascii=False)}\n\n"
        pixiv_session = _pixiv_session_payload()
        status_update = {
            "pixiv_session": pixiv_session,
            "pixiv_logged_in": pixiv_session.get("state") == "authenticated",
        }
        yield f"event: status_update\ndata: {json.dumps(status_update, ensure_ascii=False)}\n\n"
        for entry in recent_logs[-50:]:
            yield f"event: log\ndata: {json.dumps(entry, ensure_ascii=False)}\n\n"
        for pi in pending_inputs:
            yield f"event: input_required\ndata: {json.dumps(pi, ensure_ascii=False)}\n\n"

        try:
            while True:
                try:
                    item = client_q.get(timeout=25)
                    yield f"event: {item['type']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            with CLIENTS_LOCK:
                if client_q in SSE_CLIENTS:
                    SSE_CLIENTS.remove(client_q)
                has_clients = bool(SSE_CLIENTS)
            if not has_clients:
                _schedule_idle_shutdown()

    return Response(
        stream_with_context(generate()),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Entry point ────────────────────────────────────────────────
def main() -> None:
    ensure_runtime_layout(SCRIPT_DIR)
    _init_cfg = _load_config()
    if _init_cfg.get("scheduler", {}).get("enabled"):
        _arm_scheduler(_init_cfg)
    url = f"http://localhost:{PORT}"
    print(f"Starting web server at {url}")
    print("Press Ctrl+C to stop.")
    threading.Timer(1.5, webbrowser.open, args=[url]).start()
    app.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
