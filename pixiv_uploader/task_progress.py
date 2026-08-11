from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


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
    if command == 1:
        return ProgressProfile((
            ProgressStage("split_discover", 15),
            ProgressStage("split_download", 30),
            ProgressStage("split_publish", 50),
            ProgressStage("finalizing", 5),
        ))
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
    "split_discover": "读取 Civitai 帖子",
    "split_download": "下载与写入元数据",
    "split_publish": "发布到 Civitai",
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


class TaskProgressState:
    """Monotonic, stage-based task progress independent from log wording.

    Running work is capped below 100%. Only ``finish("done")`` can produce 1.0,
    so a failed or canceled publish can never look fully published.
    """

    VERSION = 3

    def __init__(self, profile: ProgressProfile, total: int = 0) -> None:
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
        self._weight_total = sum(stage.weight for stage in profile.stages)
        cumulative = 0.0
        self._stage_offsets: dict[str, tuple[int, float, float]] = {}
        for index, stage in enumerate(profile.stages, 1):
            self._stage_offsets[stage.id] = (index, cumulative, stage.weight)
            cumulative += stage.weight

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

    def _apply_counts(
        self,
        *,
        total: int | None,
        current: int | None,
        succeeded: int | None,
        failed: int | None,
        canceled: int | None,
    ) -> None:
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
        stage_progress: float = 0.0,
        overall_progress: float | None = None,
        item_index: int | None = None,
        item_name: str | None = None,
        activity: dict[str, Any] | None = None,
        total: int | None = None,
        current: int | None = None,
        succeeded: int | None = None,
        failed: int | None = None,
        canceled: int | None = None,
    ) -> dict[str, Any]:
        self._apply_counts(
            total=total,
            current=current,
            succeeded=succeeded,
            failed=failed,
            canceled=canceled,
        )
        previous_stage = self.stage
        if stage == "item_complete":
            if item_index is not None:
                self.item_index = max(0, int(item_index))
            if item_name is not None:
                self.item_name = str(item_name)
            if activity is not None:
                self.activity = dict(activity)
            return self.snapshot()

        fraction = self._fraction(stage_progress)
        raw_progress = self.progress
        stage_index = self.stage_index

        if stage == "initializing":
            raw_progress = self.profile.startup_share * fraction
            stage_index = 1
        elif stage == "completing":
            raw_progress = (
                self.profile.startup_share
                + self.work_share
                + self.profile.completion_share * fraction
            )
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
            self.item_name = str(item_name)
        if activity is not None:
            self.activity = dict(activity)
        elif str(stage) != previous_stage:
            self.activity = {}
        self.stage = str(stage)
        self.stage_progress = fraction
        self.stage_index = stage_index
        self.progress = min(0.999, max(self.progress, raw_progress))
        return self.snapshot()

    def finish(self, status: str) -> dict[str, Any]:
        if status == "done":
            self.progress = 1.0
            self.stage = "done"
            self.stage_progress = 1.0
            self.stage_index = self.stage_count
            self.activity = {}
            if self.profile.per_item and self.total:
                self.current = self.total
        elif status == "canceled":
            self.progress = min(self.progress, 0.99)
            self.activity = {}
            if self.stage == "queued":
                self.stage = "canceled"
        else:
            self.progress = min(self.progress, 0.99)
            self.activity = {}
            if self.stage == "queued":
                self.stage = "failed"
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
        }
