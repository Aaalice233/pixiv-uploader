from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from ..paths import runtime_paths

log = logging.getLogger(__name__)
_PROFILE_LEASES_LOCK = threading.RLock()
_PROFILE_LEASES: dict[str, "PixivProfileLease"] = {}

PIXIV_PROFILE_DIR = Path.home() / ".civitai_splitter_pixiv_chrome"
PIXIV_RUNTIME_DIR = runtime_paths().pixiv
PIXIV_SESSION_STATE_PATH = PIXIV_RUNTIME_DIR / "session.json"
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


PIXIV_SESSION = PixivSessionStore()
