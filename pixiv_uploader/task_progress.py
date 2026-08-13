from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ProgressStage:
    id: str
    weight: float


@dataclass(frozen=True)
class ProgressProfile:
    stages: tuple[ProgressStage, ...]
    per_item: bool = False
    startup_share: float = 0.04
    completion_share: float = 0.02

    def __post_init__(self) -> None:
        if not self.stages:
            raise ValueError("progress profile must contain at least one stage")
        if any(stage.weight <= 0 for stage in self.stages):
            raise ValueError("progress stage weights must be positive")
        if self.startup_share < 0 or self.completion_share < 0:
            raise ValueError("progress profile shares cannot be negative")
        if self.startup_share + self.completion_share >= 1:
            raise ValueError("progress profile must reserve room for work stages")


_UPLOAD_BASE_STAGES = (
    ProgressStage("reading_metadata", 5),
    ProgressStage("preparing_artifacts", 8),
)
_UPLOAD_PIXIV_PREP_STAGES = (
    ProgressStage("censoring", 5),
    ProgressStage("tagging", 9),
    ProgressStage("organizing_tags", 9),
)
_UPLOAD_PIXIV_FINAL_PREP_STAGES = (
    ProgressStage("watermarking", 5),
)
_UPLOAD_PIXIV_PUBLISH_STAGES = (
    ProgressStage("opening_pixiv", 7),
    ProgressStage("filling_pixiv", 13),
    ProgressStage("submitting_pixiv", 7),
    ProgressStage("verifying_pixiv", 12),
)


def _targets_from_params(command: int, params: dict[str, Any]) -> set[str]:
    default = "pixiv" if command == 3 else "civitai,pixiv"
    raw = params.get("targets", default)
    if isinstance(raw, (list, tuple, set)):
        values: Iterable[Any] = raw
    else:
        values = str(raw or "").split(",")
    return {str(value).strip().lower() for value in values if str(value).strip()}


def build_progress_profile(command: int, params: dict[str, Any] | None = None) -> ProgressProfile:
    params = params or {}
    if command in (2, 3):
        targets = _targets_from_params(command, params)
        stages = list(_UPLOAD_BASE_STAGES)
        if "civitai" in targets:
            stages.append(ProgressStage("safety_check", 3))
        if "pixiv" in targets:
            stages.extend(_UPLOAD_PIXIV_PREP_STAGES)
            if bool(params.get("llm_reverse")):
                stages.append(ProgressStage("generating_copy", 18))
            stages.extend(_UPLOAD_PIXIV_FINAL_PREP_STAGES)
        stages.append(ProgressStage("saving_manifest", 4))
        if "civitai" in targets:
            stages.append(ProgressStage("publishing_civitai", 28))
        if "pixiv" in targets:
            stages.extend(_UPLOAD_PIXIV_PUBLISH_STAGES)
        stages.append(ProgressStage("finalizing_image", 4))
        return ProgressProfile(tuple(stages), per_item=True)
    if command == 4:
        return ProgressProfile((ProgressStage("installing", 1),))
    if command == 5:
        return ProgressProfile((
            ProgressStage("checking_updates", 45),
            ProgressStage("applying_update", 50),
            ProgressStage("finalizing", 5),
        ))
    if command == 6:
        return ProgressProfile((
            ProgressStage("preparing_copy", 15),
            ProgressStage("generating_copy", 75),
            ProgressStage("finalizing", 10),
        ))
    return ProgressProfile((ProgressStage("working", 1),))


STAGE_LABELS = {
    "queued": "等待执行",
    "initializing": "初始化任务",
    "reading_metadata": "读取图片与元数据",
    "safety_check": "检查内容安全",
    "preparing_artifacts": "准备发布副本",
    "censoring": "检测并处理敏感区域",
    "tagging": "识别图片标签",
    "organizing_tags": "整理标签与分级",
    "generating_copy": "生成文案与视觉标签",
    "watermarking": "应用水印",
    "saving_manifest": "保存发布清单",
    "publishing_civitai": "发布到 Civitai",
    "opening_pixiv": "打开 Pixiv 投稿页",
    "filling_pixiv": "填写 Pixiv 投稿表单",
    "submitting_pixiv": "提交 Pixiv 投稿",
    "verifying_pixiv": "确认 Pixiv 发布结果",
    "finalizing_image": "整理已发布文件",
    "installing": "安装打码模型",
    "checking_updates": "检查项目更新",
    "applying_update": "应用项目更新",
    "preparing_copy": "准备图片与提示词",
    "finalizing": "整理任务结果",
    "working": "处理中",
    "completing": "完成收尾",
    "done": "全部完成",
    "failed": "任务执行失败",
    "canceled": "任务已取消",
}


def stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage.replace("_", " "))


_ITEM_TERMINAL_STATUSES = frozenset({
    "succeeded", "partial", "failed", "uncertain", "canceled", "unprocessed",
})
_RETRYABLE_ITEM_STATUSES = frozenset({"partial", "failed", "canceled", "unprocessed"})
_TARGET_STATUSES = frozenset({
    "queued", "pending", "running", "success", "failed", "canceled", "maybe_posted",
    "skipped_already_done", "skipped_civitai_safety", "dry_run",
})
_SAFE_CODE_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_SAFE_PLATFORM_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _safe_name(value: Any) -> str:
    return str(value or "").replace("\x00", "").replace("\\", "/").rsplit("/", 1)[-1][:255]


def _safe_code(value: Any) -> str:
    return _SAFE_CODE_RE.sub("_", str(value or "").strip())[:96].strip("_")


def _safe_post_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        return ""
    return parsed._replace(query="", fragment="").geturl()[:2048]


class TaskProgressState:
    """Monotonic batch progress plus authoritative per-item publishing state."""

    VERSION = 4

    def __init__(
        self,
        profile: ProgressProfile,
        total: int = 0,
        *,
        items: Iterable[Any] | None = None,
        targets: Iterable[str] | None = None,
    ) -> None:
        self.profile = profile
        self.total = max(0, int(total or 0))
        self.progress = 0.0
        self.stage = "queued"
        self.stage_progress = 0.0
        self.stage_index = 0
        self.item_index = 0
        self.item_name = ""
        self.activity: dict[str, Any] = {}
        self.current = 0
        self.succeeded = 0
        self.failed = 0
        self.canceled = 0
        self.items: list[dict[str, Any]] = []
        self.finished_status = ""
        self._weight_total = sum(stage.weight for stage in profile.stages)
        cumulative = 0.0
        self._stage_offsets: dict[str, tuple[int, float, float]] = {}
        for index, stage in enumerate(profile.stages, 1):
            self._stage_offsets[stage.id] = (index, cumulative, stage.weight)
            cumulative += stage.weight
        if items is not None:
            self.register_items(items, targets=targets)

    @property
    def stage_count(self) -> int:
        return len(self.profile.stages) + 2

    @property
    def work_share(self) -> float:
        return 1.0 - self.profile.startup_share - self.profile.completion_share

    @staticmethod
    def _fraction(value: float | int | None) -> float:
        try:
            return max(0.0, min(1.0, float(value or 0.0)))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _target_plan(targets: Iterable[str] | None) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for value in targets or ():
            platform = str(value or "").strip().lower()
            if _SAFE_PLATFORM_RE.fullmatch(platform):
                result[platform] = {"status": "queued", "post_url": "", "error_code": ""}
        return result

    @staticmethod
    def _normalize_target_results(raw_targets: Any) -> dict[str, dict[str, str]]:
        if not isinstance(raw_targets, dict):
            return {}
        result: dict[str, dict[str, str]] = {}
        for raw_platform, raw_detail in raw_targets.items():
            platform = str(raw_platform or "").strip().lower()
            if not _SAFE_PLATFORM_RE.fullmatch(platform):
                continue
            detail = raw_detail if isinstance(raw_detail, dict) else {"status": raw_detail}
            status = str(detail.get("status") or "failed").strip().lower()
            if status not in _TARGET_STATUSES:
                status = "failed"
            post_url = ""
            if status in {"success", "skipped_already_done"}:
                post_url = _safe_post_url(detail.get("post_url"))
            result[platform] = {
                "status": status,
                "post_url": post_url,
                "error_code": _safe_code(detail.get("error_code")),
            }
        return result

    def register_items(
        self,
        raw_items: Iterable[Any],
        *,
        targets: Iterable[str] | None = None,
    ) -> None:
        if self.items and any(item["status"] != "queued" for item in self.items):
            return
        default_targets = self._target_plan(targets)
        planned: list[dict[str, Any]] = []
        for raw_item in raw_items:
            data = raw_item if isinstance(raw_item, dict) else {"name": raw_item}
            name = _safe_name(data.get("name"))
            if not name:
                continue
            position = len(planned) + 1
            item_targets = self._normalize_target_results(data.get("targets"))
            if not item_targets:
                item_targets = {platform: dict(detail) for platform, detail in default_targets.items()}
            planned.append({
                "index": position,
                "name": name,
                "status": "queued",
                "stage": "queued",
                "stage_progress": 0.0,
                "retryable": bool(data.get("retryable", True)),
                "reason_code": "",
                "targets": item_targets,
            })
        self.items = planned
        self.total = len(planned)
        self._sync_counts()

    def _item_at(self, item_index: int | None) -> dict[str, Any] | None:
        try:
            index = int(item_index or 0)
        except (TypeError, ValueError):
            return None
        if index <= 0 or index > len(self.items):
            return None
        return self.items[index - 1]

    def _sync_counts(self) -> None:
        if not self.items:
            return
        self.total = len(self.items)
        self.succeeded = sum(item["status"] == "succeeded" for item in self.items)
        self.canceled = sum(item["status"] == "canceled" for item in self.items)
        self.failed = sum(item["status"] in {"partial", "failed", "uncertain"} for item in self.items)
        self.current = self.succeeded + self.failed + self.canceled

    def _complete_item(
        self,
        *,
        item_index: int | None,
        item_name: str | None,
        item_status: str | None,
        retryable: bool | None,
        reason_code: str | None,
        targets: Any,
    ) -> None:
        item = self._item_at(item_index)
        if item is None:
            return
        status = str(item_status or "failed").strip().lower()
        if status not in _ITEM_TERMINAL_STATUSES:
            status = "failed"
        if item_name:
            item["name"] = _safe_name(item_name) or item["name"]
        normalized_targets = self._normalize_target_results(targets)
        if normalized_targets:
            item["targets"] = normalized_targets
        item["status"] = status
        item["retryable"] = bool(retryable) and status in _RETRYABLE_ITEM_STATUSES
        item["reason_code"] = _safe_code(reason_code)
        if status == "succeeded":
            item["stage"] = "done"
            item["stage_progress"] = 1.0
        elif status == "canceled":
            item["stage"] = "canceled"
        elif status == "unprocessed":
            item["stage"] = "queued"
            item["stage_progress"] = 0.0
        else:
            item["stage_progress"] = self._fraction(item.get("stage_progress"))
        self.item_index = item["index"]
        self.item_name = item["name"]
        self._sync_counts()

    def _apply_counts(
        self,
        *,
        total: int | None,
        current: int | None,
        succeeded: int | None,
        failed: int | None,
        canceled: int | None,
    ) -> None:
        if self.items:
            self._sync_counts()
            return
        if total is not None:
            self.total = max(0, int(total))
        if current is not None:
            self.current = max(self.current, max(0, int(current)))
        if self.total:
            self.current = min(self.current, self.total)
        if succeeded is not None:
            self.succeeded = max(0, int(succeeded))
        if failed is not None:
            self.failed = max(0, int(failed))
        if canceled is not None:
            self.canceled = max(0, int(canceled))

    def advance(
        self,
        stage: str,
        *,
        stage_progress: float | None = None,
        overall_progress: float | None = None,
        item_index: int | None = None,
        item_name: str | None = None,
        activity: dict[str, Any] | None = None,
        total: int | None = None,
        current: int | None = None,
        succeeded: int | None = None,
        failed: int | None = None,
        canceled: int | None = None,
        items: Iterable[Any] | None = None,
        item_status: str | None = None,
        retryable: bool | None = None,
        reason_code: str | None = None,
        targets: Any = None,
    ) -> dict[str, Any]:
        if stage == "items_registered":
            self.register_items(items or (), targets=targets if not isinstance(targets, dict) else targets.keys())
            return self.snapshot()

        previous_stage = self.stage
        if stage == "item_target":
            item = self._item_at(item_index)
            normalized_targets = self._normalize_target_results(targets)
            if item is not None and normalized_targets:
                item["targets"].update(normalized_targets)
                if item["status"] == "queued":
                    item["status"] = "running"
                self.item_index = item["index"]
                self.item_name = item["name"]
                self._sync_counts()
            return self.snapshot()
        if stage == "item_complete":
            if self.items:
                self._complete_item(
                    item_index=item_index,
                    item_name=item_name,
                    item_status=item_status,
                    retryable=retryable,
                    reason_code=reason_code,
                    targets=targets,
                )
            else:
                self._apply_counts(
                    total=total,
                    current=current,
                    succeeded=succeeded,
                    failed=failed,
                    canceled=canceled,
                )
            if activity is not None:
                self.activity = dict(activity)
            return self.snapshot()

        self._apply_counts(
            total=total,
            current=current,
            succeeded=succeeded,
            failed=failed,
            canceled=canceled,
        )
        fraction = (
            self.stage_progress
            if stage_progress is None and str(stage) == previous_stage
            else self._fraction(stage_progress)
        )
        raw_progress = self.progress
        stage_index = self.stage_index

        if stage == "initializing":
            raw_progress = self.profile.startup_share * fraction
            stage_index = 1
        elif stage == "completing":
            raw_progress = self.profile.startup_share + self.work_share + self.profile.completion_share * fraction
            stage_index = self.stage_count
        elif stage in self._stage_offsets:
            profile_index, offset, weight = self._stage_offsets[stage]
            stage_fraction = (offset + weight * fraction) / self._weight_total
            if self.profile.per_item:
                if item_index is not None:
                    self.item_index = max(1, int(item_index))
                elif self.item_index <= 0:
                    self.item_index = 1
                divisor = max(1, self.total)
                bounded_index = min(self.item_index, divisor)
                work_fraction = ((bounded_index - 1) + stage_fraction) / divisor
            else:
                work_fraction = stage_fraction
            raw_progress = self.profile.startup_share + self.work_share * work_fraction
            stage_index = profile_index + 1

        if overall_progress is not None and stage not in {"initializing", "completing"}:
            raw_progress = self.profile.startup_share + self.work_share * self._fraction(overall_progress)

        if item_index is not None:
            self.item_index = max(0, int(item_index))
        if item_name is not None:
            self.item_name = _safe_name(item_name)
        item = self._item_at(self.item_index)
        if item is not None and stage in self._stage_offsets:
            for other in self.items:
                if other is not item and other["status"] == "running":
                    other["status"] = "failed"
                    other["retryable"] = True
                    other["reason_code"] = "unexpected_item_transition"
                    for target_detail in other["targets"].values():
                        if target_detail["status"] in {"queued", "pending", "running"}:
                            target_detail["status"] = "failed"
                            target_detail["error_code"] = "unexpected_item_transition"
            if item["status"] == "queued":
                item["status"] = "running"
            if item["status"] == "running":
                item["stage"] = str(stage)
                item["stage_progress"] = fraction
                for target_detail in item["targets"].values():
                    if target_detail["status"] == "queued":
                        target_detail["status"] = "pending"
            self.item_name = item["name"]
            self._sync_counts()
        if activity is not None:
            self.activity = dict(activity)
        elif str(stage) != previous_stage:
            self.activity = {}
        self.stage = str(stage)
        self.stage_progress = fraction
        self.stage_index = stage_index
        self.progress = min(0.999, max(self.progress, raw_progress))
        return self.snapshot()

    def reconcile_source_availability(self, available_names: Iterable[str]) -> None:
        available = {_safe_name(name).lower() for name in available_names}
        for item in self.items:
            if item["status"] in _RETRYABLE_ITEM_STATUSES | {"queued", "running"}:
                item["retryable"] = item["name"].lower() in available
            if item["status"] in {"succeeded", "uncertain"}:
                item["retryable"] = False
        self._sync_counts()

    def _close_items(self, status: str, reason_code: str) -> None:
        if not self.items:
            return
        for item in self.items:
            if item["status"] == "running":
                item_reason = _safe_code(reason_code or ("task_canceled" if status == "canceled" else "unexpected_task_failure"))
                for target_detail in item["targets"].values():
                    if target_detail["status"] in {"queued", "pending", "running"}:
                        target_detail["status"] = "canceled" if status == "canceled" else "failed"
                        target_detail["error_code"] = item_reason
                target_statuses = {detail["status"] for detail in item["targets"].values()}
                success_statuses = {"success", "skipped_already_done", "skipped_civitai_safety", "dry_run"}
                if status == "canceled":
                    item["status"] = "canceled"
                    item["stage"] = "canceled"
                elif "maybe_posted" in target_statuses:
                    item["status"] = "uncertain"
                elif target_statuses & success_statuses and not target_statuses <= success_statuses:
                    item["status"] = "partial"
                else:
                    item["status"] = "failed"
                item["retryable"] = bool(item["retryable"] and item["status"] in _RETRYABLE_ITEM_STATUSES)
                item["reason_code"] = item_reason
            elif item["status"] == "queued":
                item["status"] = "unprocessed"
                item["stage"] = "queued"
                item["stage_progress"] = 0.0
                item["retryable"] = bool(item["retryable"])
                item["reason_code"] = _safe_code(reason_code or ("task_canceled" if status == "canceled" else "batch_stopped"))
        self._sync_counts()

    def finish(self, status: str, *, reason_code: str = "") -> dict[str, Any]:
        requested = str(status or "failed")
        if requested == "done" and self.items and not all(item["status"] == "succeeded" for item in self.items):
            requested = "failed"
            reason_code = reason_code or "incomplete_item_state"
        if requested == "done":
            if self.items:
                self._sync_counts()
            self.progress = 1.0
            self.stage = "done"
            self.stage_progress = 1.0
            self.stage_index = self.stage_count
            self.activity = {}
            if self.profile.per_item and self.total:
                self.current = self.total
        elif requested == "canceled":
            self._close_items("canceled", reason_code)
            self.progress = min(self.progress, 0.99)
            self.activity = {}
            if self.stage == "queued":
                self.stage = "canceled"
        else:
            requested = "failed"
            self._close_items("failed", reason_code)
            self.progress = min(self.progress, 0.99)
            self.activity = {}
            if self.stage == "queued":
                self.stage = "failed"
        self.finished_status = requested
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {
            "progress_version": self.VERSION,
            "progress": round(self.progress, 6),
            "stage": self.stage,
            "stage_label": stage_label(self.stage),
            "stage_progress": round(self.stage_progress, 6),
            "stage_index": self.stage_index,
            "stage_count": self.stage_count,
            "item_index": self.item_index,
            "item_name": self.item_name,
            "activity": dict(self.activity),
            "current": self.current,
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "canceled": self.canceled,
            "items": [
                {
                    **item,
                    "targets": {platform: dict(detail) for platform, detail in item["targets"].items()},
                }
                for item in self.items
            ],
        }
