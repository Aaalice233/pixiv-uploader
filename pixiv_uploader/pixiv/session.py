from __future__ import annotations

import json
import logging
import os
import random
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from ..paths import runtime_paths

log = logging.getLogger(__name__)
_RISK_FILE_LOCK = threading.RLock()
_PROFILE_LEASES_LOCK = threading.RLock()
_PROFILE_LEASES: dict[str, "PixivProfileLease"] = {}

PIXIV_PROFILE_DIR = Path.home() / ".civitai_splitter_pixiv_chrome"
PIXIV_RUNTIME_DIR = runtime_paths().pixiv
PIXIV_SESSION_STATE_PATH = PIXIV_RUNTIME_DIR / "session.json"
PIXIV_RISK_STATE_PATH = PIXIV_RUNTIME_DIR / "risk_state.json"
PIXIV_LOGIN_TIMEOUT_SECONDS = 15 * 60
PIXIV_CAPTCHA_TIMEOUT_SECONDS = 10 * 60
PIXIV_PROFILE_ID_FILE = ".pixiv_uploader_profile_id"

SESSION_STATES = frozenset(
    {"missing", "unverified", "checking", "authenticated", "login_required", "error", "in_use"}
)


class PixivFlowError(RuntimeError):
    def __init__(self, code: str, message: str, *, batch_fatal: bool = True, maybe_posted: bool = False):
        super().__init__(message)
        self.code = code
        self.batch_fatal = batch_fatal
        self.maybe_posted = maybe_posted


class PixivProfileInUseError(PixivFlowError):
    def __init__(self, owner: str | None = None):
        owner_text = f"（当前流程：{owner}）" if owner else ""
        super().__init__("pixiv_profile_in_use", f"Pixiv 浏览器资料正在使用中{owner_text}")
        self.owner = owner or ""


@dataclass(frozen=True)
class PixivProfileLease:
    owner: str
    acquired_at: str


class PixivSessionStore:
    def __init__(
        self,
        *,
        profile_dir: Path = PIXIV_PROFILE_DIR,
        state_path: Path = PIXIV_SESSION_STATE_PATH,
    ) -> None:
        self.profile_dir = Path(profile_dir)
        self.state_path = Path(state_path)
        self._lock = threading.RLock()
        self._lease: PixivProfileLease | None = None
        self._runtime_state: str | None = None
        self._listeners: list[Callable[[dict[str, Any]], None]] = []

    def _lease_key(self) -> str:
        try:
            return os.path.normcase(str(self.profile_dir.resolve(strict=False)))
        except OSError:
            return os.path.normcase(str(self.profile_dir.absolute()))

    def add_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def _load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, TypeError) as exc:
            log.warning("无法读取 Pixiv 会话状态，已按未验证处理：%s (%s)", self.state_path, exc)
            return {}

    def _write(self, value: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.state_path.parent,
            prefix=f".{self.state_path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
        except Exception:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
            raise

    def _profile_id(self, *, create: bool = False) -> str:
        marker = self.profile_dir / PIXIV_PROFILE_ID_FILE
        try:
            value = marker.read_text(encoding="utf-8").strip()
            if value:
                return value
        except FileNotFoundError:
            pass
        except OSError:
            if not create:
                return ""
            raise
        if not create or not self.profile_dir.exists():
            return ""
        value = uuid.uuid4().hex
        marker.write_text(value + "\n", encoding="utf-8")
        return value

    def ensure_profile_identity(self) -> str:
        with self._lock:
            return self._profile_id(create=True)

    def snapshot(self) -> dict[str, Any]:
        # Lease operations always lock the process-wide registry before the
        # store. Keep the same order here so concurrent SSE snapshots cannot
        # deadlock with acquire/release.
        with _PROFILE_LEASES_LOCK, self._lock:
            stored = self._load()
            profile_exists = self.profile_dir.exists()
            stored_state = str(stored.get("state") or "")
            active_lease = _PROFILE_LEASES.get(self._lease_key())
            if active_lease is not None:
                state = self._runtime_state if self._lease == active_lease and self._runtime_state else "in_use"
            elif not profile_exists:
                state = "missing"
            else:
                candidate = stored_state or "unverified"
                if candidate == "authenticated" and (
                    not stored.get("profile_id")
                    or stored.get("profile_id") != self._profile_id()
                ):
                    candidate = "unverified"
                state = candidate if candidate in SESSION_STATES - {"checking", "in_use", "missing"} else "unverified"
            profile_identity_matches = bool(
                profile_exists
                and stored.get("profile_id")
                and stored.get("profile_id") == self._profile_id()
            )
            verified_state = (
                stored_state
                if profile_identity_matches and stored_state in {"authenticated", "login_required", "error"}
                else ""
            )
            return {
                "state": state,
                "verified_state": verified_state,
                "last_verified_at": stored.get("last_verified_at"),
                "last_error_code": str(stored.get("last_error_code") or ""),
                "last_error": str(stored.get("last_error") or ""),
                "in_use_by": active_lease.owner if active_lease else "",
                "in_use_since": active_lease.acquired_at if active_lease else None,
                "profile_exists": profile_exists,
            }

    def _notify(self) -> None:
        snapshot = self.snapshot()
        with self._lock:
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback(snapshot)
            except Exception:
                log.exception("Pixiv 会话状态监听器执行失败")

    def update_verified(self, state: str, *, error_code: str = "", error: str = "") -> dict[str, Any]:
        if state not in SESSION_STATES - {"checking", "in_use", "missing", "unverified"}:
            raise ValueError(f"无法持久化 Pixiv 会话状态: {state}")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            value = {
                "state": state,
                "last_verified_at": now,
                "last_error_code": error_code,
                "last_error": error,
                "profile_id": self._profile_id(create=True),
            }
            self._write(value)
        self._notify()
        return self.snapshot()

    def clear(self) -> None:
        with self._lock:
            try:
                self.state_path.unlink(missing_ok=True)
            except OSError:
                log.exception("清除 Pixiv 会话状态失败")
                raise
        self._notify()

    def acquire(self, owner: str) -> PixivProfileLease:
        with _PROFILE_LEASES_LOCK, self._lock:
            active_lease = _PROFILE_LEASES.get(self._lease_key())
            if active_lease is not None:
                raise PixivProfileInUseError(active_lease.owner)
            lease = PixivProfileLease(owner=owner, acquired_at=datetime.now(timezone.utc).isoformat())
            _PROFILE_LEASES[self._lease_key()] = lease
            self._lease = lease
            self._runtime_state = "checking" if owner.startswith("login") else "in_use"
        self._notify()
        return lease

    def release(self, lease: PixivProfileLease) -> None:
        changed = False
        with _PROFILE_LEASES_LOCK, self._lock:
            if self._lease == lease and _PROFILE_LEASES.get(self._lease_key()) == lease:
                _PROFILE_LEASES.pop(self._lease_key(), None)
                self._lease = None
                self._runtime_state = None
                changed = True
        if changed:
            self._notify()

    @contextmanager
    def lease(self, owner: str) -> Iterator[PixivProfileLease]:
        lease = self.acquire(owner)
        try:
            yield lease
        finally:
            self.release(lease)


class PixivRateController:
    _RANGES = {
        1: (2 * 60, 5 * 60),
        2: (5 * 60, 10 * 60),
        3: (15 * 60, 30 * 60),
    }

    def __init__(
        self,
        *,
        state_path: Path = PIXIV_RISK_STATE_PATH,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        random_uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.state_path = Path(state_path)
        self.clock = clock
        self.sleeper = sleeper
        self.random_uniform = random_uniform
        self._lock = _RISK_FILE_LOCK
        self._state = self._load()
        self._decay_idle_risk()

    def _default(self) -> dict[str, Any]:
        return {
            "risk_level": 0,
            "cooldown_until": 0.0,
            "last_risk_at": 0.0,
            "consecutive_safe_successes": 0,
            "last_risk_key": "",
            "cooldown_reason": "",
        }

    def _load(self) -> dict[str, Any]:
        value = self._default()
        try:
            stored = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                value.update(stored)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as exc:
            log.warning("无法读取 Pixiv 风险状态，已使用安全默认值：%s (%s)", self.state_path, exc)
        try:
            value["risk_level"] = max(0, min(3, int(value.get("risk_level") or 0)))
            value["cooldown_until"] = max(0.0, float(value.get("cooldown_until") or 0.0))
            value["last_risk_at"] = max(0.0, float(value.get("last_risk_at") or 0.0))
            value["consecutive_safe_successes"] = max(0, int(value.get("consecutive_safe_successes") or 0))
        except (TypeError, ValueError, OverflowError):
            log.warning("Pixiv 风险状态字段损坏，已恢复安全默认值：%s", self.state_path)
            value = self._default()
        value["last_risk_key"] = str(value.get("last_risk_key") or "")
        value["cooldown_reason"] = str(value.get("cooldown_reason") or "")
        return value

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.state_path.parent,
            prefix=f".{self.state_path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                descriptor = -1
                json.dump(self._state, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
        except Exception:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
            raise

    def _decay_idle_risk(self) -> None:
        with self._lock:
            self._state = self._load()
            last_risk = float(self._state.get("last_risk_at") or 0.0)
            if self._state["risk_level"] > 0 and last_risk:
                elapsed_periods = int(max(0.0, self.clock() - last_risk) // (24 * 60 * 60))
                if elapsed_periods > 0:
                    applied_periods = min(int(self._state["risk_level"]), elapsed_periods)
                    self._state["risk_level"] -= applied_periods
                    self._state["last_risk_at"] = last_risk + applied_periods * 24 * 60 * 60
                    self._state["consecutive_safe_successes"] = 0
                    if self._state["risk_level"] == 0:
                        self._state["last_risk_key"] = ""
                    self._save()
            now = self.clock()
            if self._state["cooldown_until"] and float(self._state["cooldown_until"]) <= now:
                self._state["cooldown_until"] = 0.0
                self._state["cooldown_reason"] = ""
                self._save()

    def snapshot(self) -> dict[str, Any]:
        self._decay_idle_risk()
        with self._lock:
            now = self.clock()
            cooldown_until = float(self._state["cooldown_until"])
            return {
                "risk_level": int(self._state["risk_level"]),
                "cooldown_until": datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat() if cooldown_until > now else None,
                "cooldown_remaining_seconds": max(0, int(cooldown_until - now + 0.999)),
                "cooldown_reason": str(self._state.get("cooldown_reason") or ""),
                "last_risk_at": datetime.fromtimestamp(float(self._state["last_risk_at"]), timezone.utc).isoformat() if self._state["last_risk_at"] else None,
            }

    @staticmethod
    def parse_retry_after(value: str | None, *, now: float | None = None) -> float:
        if not value:
            return 0.0
        try:
            return max(0.0, float(value.strip()))
        except (TypeError, ValueError):
            pass
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, parsed.timestamp() - (time.time() if now is None else now))
        except (TypeError, ValueError, OverflowError):
            return 0.0

    def record_risk(self, signal: str, *, work_key: str = "", retry_after: float = 0.0) -> dict[str, Any]:
        duplicate_snapshot: dict[str, Any] | None = None
        with self._lock:
            self._state = self._load()
            risk_key = f"captcha:{work_key}" if signal == "captcha" and work_key else ""
            duplicate = bool(risk_key and risk_key == self._state.get("last_risk_key"))
            if duplicate:
                now = self.clock()
                cooldown_until = float(self._state["cooldown_until"])
                duplicate_snapshot = {
                    "risk_level": int(self._state["risk_level"]),
                    "cooldown_until": (
                        datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()
                        if cooldown_until > now
                        else None
                    ),
                    "cooldown_remaining_seconds": max(0, int(cooldown_until - now + 0.999)),
                    "cooldown_reason": str(self._state.get("cooldown_reason") or ""),
                    "last_risk_at": (
                        datetime.fromtimestamp(float(self._state["last_risk_at"]), timezone.utc).isoformat()
                        if self._state["last_risk_at"]
                        else None
                    ),
                }
            else:
                self._state["risk_level"] = min(3, int(self._state["risk_level"]) + 1)
                self._state["last_risk_at"] = self.clock()
                if risk_key:
                    self._state["last_risk_key"] = risk_key
                level = max(1, int(self._state["risk_level"]))
                low, high = self._RANGES[level]
                adaptive_delay = self.random_uniform(low, high)
                delay = max(float(retry_after or 0.0), adaptive_delay)
                self._state["cooldown_until"] = max(float(self._state["cooldown_until"]), self.clock() + delay)
                self._state["cooldown_reason"] = signal
                self._state["consecutive_safe_successes"] = 0
                self._save()
        if duplicate_snapshot is not None:
            return duplicate_snapshot
        return self.snapshot()

    def record_success(self, *, risk_signal: bool = False) -> dict[str, Any]:
        with self._lock:
            self._state = self._load()
            if risk_signal:
                self._state["consecutive_safe_successes"] = 0
            elif self._state["risk_level"] > 0:
                self._state["consecutive_safe_successes"] += 1
                if self._state["consecutive_safe_successes"] >= 3:
                    self._state["risk_level"] -= 1
                    self._state["consecutive_safe_successes"] = 0
                    self._state["last_risk_key"] = ""
            else:
                self._state["consecutive_safe_successes"] = 0
            self._save()
        return self.snapshot()

    def schedule_baseline(self, delay_seconds: float) -> dict[str, Any]:
        delay = max(0.0, float(delay_seconds))
        if delay <= 0:
            return self.snapshot()
        with self._lock:
            self._state = self._load()
            now = self.clock()
            baseline = self.random_uniform(delay * 0.8, delay * 1.4)
            baseline_until = now + baseline
            active_cooldown_until = float(self._state["cooldown_until"])
            # A persisted risk/429 cooldown always wins. Baseline jitter is only
            # the normal interval after no stronger cooldown remains active.
            stronger_cooldown_active = bool(
                active_cooldown_until > now
                and str(self._state.get("cooldown_reason") or "") != "baseline"
            )
            if not stronger_cooldown_active and baseline_until >= active_cooldown_until:
                self._state["cooldown_until"] = baseline_until
                self._state["cooldown_reason"] = "baseline"
            self._save()
        return self.snapshot()

    def wait(
        self,
        *,
        cancel_event=None,
        activity_callback: Callable[..., None] | None = None,
        poll_seconds: float = 1.0,
    ) -> None:
        last_reported: int | None = None
        while True:
            snapshot = self.snapshot()
            remaining = int(snapshot["cooldown_remaining_seconds"])
            if remaining <= 0:
                with self._lock:
                    self._state = self._load()
                    if float(self._state["cooldown_until"]) <= self.clock() and self._state["cooldown_until"]:
                        self._state["cooldown_until"] = 0.0
                        self._state["cooldown_reason"] = ""
                        self._save()
                if activity_callback:
                    activity_callback(None)
                return
            if cancel_event is not None and cancel_event.is_set():
                if activity_callback:
                    activity_callback(None)
                raise InterruptedError("task canceled during Pixiv cooldown")
            if activity_callback and remaining != last_reported:
                activity_callback(
                    {
                        "kind": "pixiv_cooldown",
                        "reason": snapshot["cooldown_reason"],
                        "risk_level": snapshot["risk_level"],
                        "remaining_seconds": remaining,
                        "deadline": snapshot["cooldown_until"],
                    }
                )
                last_reported = remaining
            sleep_for = min(max(0.05, poll_seconds), remaining)
            if cancel_event is not None and hasattr(cancel_event, "wait"):
                if cancel_event.wait(sleep_for):
                    continue
            else:
                self.sleeper(sleep_for)


PIXIV_SESSION = PixivSessionStore()
