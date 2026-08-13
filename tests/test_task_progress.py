from __future__ import annotations

import unittest
from unittest.mock import patch

from pixiv_uploader.task_progress import TaskProgressState, build_progress_profile
import pixiv_uploader.web as web
from pixiv_uploader.publishing import _task_item_outcome


class TaskProgressStateTests(unittest.TestCase):
    def test_upload_progress_is_monotonic_and_only_done_reaches_one_hundred(self) -> None:
        state = TaskProgressState(
            build_progress_profile(3, {"targets": "pixiv", "llm_reverse": True}),
            total=1,
        )
        values = [state.advance("initializing", stage_progress=1.0)["progress"]]
        for stage in (
            "reading_metadata",
            "preparing_artifacts",
            "censoring",
            "tagging",
            "organizing_tags",
            "generating_copy",
            "watermarking",
            "saving_manifest",
            "opening_pixiv",
            "filling_pixiv",
            "submitting_pixiv",
            "verifying_pixiv",
            "finalizing_image",
        ):
            values.append(
                state.advance(stage, stage_progress=1.0, item_index=1)["progress"]
            )

        self.assertEqual(values, sorted(values))
        self.assertLess(values[-1], 1.0)
        self.assertEqual(state.stage_index, state.stage_count - 1)
        self.assertLess(state.finish("failed")["progress"], 1.0)
        done = state.finish("done")
        self.assertEqual(done["progress"], 1.0)
        self.assertEqual(done["stage"], "done")
        self.assertEqual(done["stage_index"], done["stage_count"])

    def test_current_counts_only_completed_items(self) -> None:
        state = TaskProgressState(
            build_progress_profile(2, {"targets": "civitai,pixiv"}),
            total=2,
        )
        preparing = state.advance(
            "tagging",
            stage_progress=0.5,
            item_index=1,
            item_name="first.png",
        )
        self.assertEqual(preparing["current"], 0)
        self.assertEqual(preparing["item_index"], 1)

        completed = state.advance(
            "item_complete",
            item_index=1,
            current=1,
            succeeded=1,
        )
        self.assertEqual(completed["current"], 1)
        self.assertEqual(completed["succeeded"], 1)
        self.assertEqual(completed["stage"], "tagging")

        second = state.advance("reading_metadata", item_index=2, stage_progress=0.0)
        self.assertGreater(second["progress"], preparing["progress"])
        self.assertLess(second["progress"], 1.0)

    def test_two_item_plan_tracks_transitions_counts_and_monotonic_progress(self) -> None:
        state = TaskProgressState(
            build_progress_profile(3, {"targets": "pixiv"}),
            items=["first.png", "second.png"],
            targets=["pixiv"],
        )
        snapshots = [state.snapshot()]
        snapshots.append(state.advance("reading_metadata", item_index=1, stage_progress=0.0))
        self.assertEqual(snapshots[-1]["items"][0]["status"], "running")
        snapshots.append(state.advance("verifying_pixiv", item_index=1, stage_progress=1.0))
        snapshots.append(state.advance(
            "item_complete",
            item_index=1,
            item_status="succeeded",
            retryable=False,
            targets={"pixiv": {"status": "success", "post_url": "https://www.pixiv.net/artworks/1"}},
        ))
        snapshots.append(state.advance("reading_metadata", item_index=2, stage_progress=0.0))
        snapshots.append(state.advance(
            "item_complete",
            item_index=2,
            item_status="failed",
            retryable=True,
            reason_code="pixiv_upload_failed",
            targets={"pixiv": {"status": "failed", "error_code": "pixiv_upload_failed"}},
        ))

        values = [snapshot["progress"] for snapshot in snapshots]
        self.assertEqual(values, sorted(values))
        self.assertLess(values[-1], 1.0)
        self.assertEqual(snapshots[-1]["current"], 2)
        self.assertEqual(snapshots[-1]["succeeded"], 1)
        self.assertEqual(snapshots[-1]["failed"], 1)
        self.assertEqual([item["status"] for item in snapshots[-1]["items"]], ["succeeded", "failed"])
        self.assertEqual(state.finish("failed")["progress_version"], 4)

    def test_snapshot_sanitizes_file_paths_and_public_links(self) -> None:
        state = TaskProgressState(
            build_progress_profile(3, {"targets": "pixiv"}),
            items=[r"C:\\private\\secret.png"],
            targets=["pixiv"],
        )
        snapshot = state.advance(
            "item_complete",
            item_index=1,
            item_status="succeeded",
            retryable=False,
            targets={
                "pixiv": {
                    "status": "success",
                    "post_url": "https://www.pixiv.net/artworks/1?token=secret#private",
                }
            },
        )

        self.assertEqual(snapshot["items"][0]["name"], "secret.png")
        self.assertEqual(snapshot["items"][0]["targets"]["pixiv"]["post_url"], "https://www.pixiv.net/artworks/1")
        self.assertNotIn("secret", snapshot["items"][0]["targets"]["pixiv"]["post_url"])

    def test_batch_close_marks_current_and_remaining_items_without_retrying_uncertain(self) -> None:
        state = TaskProgressState(
            build_progress_profile(3, {"targets": "pixiv"}),
            items=["uncertain.png", "current.png", "later.png"],
            targets=["pixiv"],
        )
        state.advance(
            "item_complete",
            item_index=1,
            item_status="uncertain",
            retryable=True,
            reason_code="pixiv_submit_unconfirmed",
            targets={"pixiv": {"status": "maybe_posted", "error_code": "pixiv_submit_unconfirmed"}},
        )
        state.advance("filling_pixiv", item_index=2, stage_progress=0.4)
        state.reconcile_source_availability(["current.png", "later.png"])
        closed = state.finish("failed", reason_code="pixiv_batch_stopped")

        self.assertEqual([item["status"] for item in closed["items"]], ["uncertain", "failed", "unprocessed"])
        self.assertFalse(closed["items"][0]["retryable"])
        self.assertTrue(closed["items"][1]["retryable"])
        self.assertTrue(closed["items"][2]["retryable"])
        self.assertEqual(closed["current"], 2)
        self.assertEqual(closed["failed"], 2)

    def test_partial_result_keeps_completed_target_when_batch_aborts_unexpectedly(self) -> None:
        state = TaskProgressState(
            build_progress_profile(2, {"targets": "civitai,pixiv"}),
            items=["partial.png"],
            targets=["civitai", "pixiv"],
        )
        state.advance("publishing_civitai", item_index=1, stage_progress=1.0)
        state.advance(
            "item_target",
            item_index=1,
            targets={"civitai": {"status": "success", "post_url": "https://civitai.com/images/1"}},
        )
        closed = state.finish("failed", reason_code="unexpected_task_failure")

        self.assertEqual(closed["items"][0]["status"], "partial")
        self.assertEqual(closed["items"][0]["targets"]["civitai"]["status"], "success")
        self.assertEqual(closed["items"][0]["targets"]["pixiv"]["status"], "failed")
        self.assertTrue(closed["items"][0]["retryable"])

    def test_activity_is_exposed_during_stage_and_cleared_on_transition(self) -> None:
        state = TaskProgressState(
            build_progress_profile(3, {"targets": "pixiv", "llm_reverse": True}),
            total=1,
        )
        activity = {
            "kind": "llm_retry",
            "event": "retry_scheduled",
            "attempt": 1,
            "max_attempts": 6,
        }

        retrying = state.advance(
            "generating_copy",
            stage_progress=0.1,
            item_index=1,
            activity=activity,
        )
        self.assertEqual(retrying["activity"], activity)

        next_stage = state.advance("watermarking", stage_progress=0.0, item_index=1)
        self.assertEqual(next_stage["activity"], {})

        state.advance("watermarking", stage_progress=0.1, item_index=1, activity=activity)
        canceled = state.finish("canceled")
        self.assertEqual(canceled["activity"], {})

    def test_profile_only_contains_requested_platform_and_llm_stages(self) -> None:
        civitai_stages = {
            stage.id for stage in build_progress_profile(
                2, {"targets": "civitai", "llm_reverse": True}
            ).stages
        }
        pixiv_stages = {
            stage.id for stage in build_progress_profile(
                3, {"targets": "pixiv", "llm_reverse": False}
            ).stages
        }

        self.assertIn("publishing_civitai", civitai_stages)
        self.assertNotIn("opening_pixiv", civitai_stages)
        self.assertNotIn("generating_copy", civitai_stages)
        self.assertIn("opening_pixiv", pixiv_stages)
        self.assertNotIn("publishing_civitai", pixiv_stages)
        self.assertNotIn("generating_copy", pixiv_stages)

class PublishingItemOutcomeTests(unittest.TestCase):
    def test_success_partial_uncertain_cancel_and_archive_failure_outcomes(self) -> None:
        success = {
            "status_by_target": {"pixiv": "success"},
            "pixiv": {"post_url": "https://www.pixiv.net/artworks/1", "error_code": ""},
            "finalization": {"status": "archived"},
        }
        self.assertEqual(
            _task_item_outcome(success, ["pixiv"], source_available=False)["item_status"],
            "succeeded",
        )
        not_archived = _task_item_outcome(
            {key: value for key, value in success.items() if key != "finalization"},
            ["pixiv"],
            source_available=True,
        )
        self.assertEqual(not_archived["item_status"], "failed")
        self.assertEqual(not_archived["reason_code"], "source_archive_failed")

        partial = {
            "status_by_target": {"civitai": "success", "pixiv": "failed"},
            "civitai": {"post_url": "https://civitai.com/images/1"},
            "pixiv": {"error_code": "pixiv_upload_failed"},
        }
        partial_outcome = _task_item_outcome(partial, ["civitai", "pixiv"], source_available=True)
        self.assertEqual(partial_outcome["item_status"], "partial")
        self.assertTrue(partial_outcome["retryable"])
        self.assertEqual(partial_outcome["targets"]["civitai"]["post_url"], "https://civitai.com/images/1")

        uncertain = {
            "status_by_target": {"pixiv": "maybe_posted"},
            "pixiv": {"post_url": "https://www.pixiv.net/artworks/should-not-leak", "error_code": "pixiv_submit_unconfirmed"},
        }
        uncertain_outcome = _task_item_outcome(uncertain, ["pixiv"], source_available=True)
        self.assertEqual(uncertain_outcome["item_status"], "uncertain")
        self.assertFalse(uncertain_outcome["retryable"])
        self.assertEqual(uncertain_outcome["targets"]["pixiv"]["post_url"], "")

        canceled = {
            "status_by_target": {"pixiv": "canceled"},
            "pixiv": {"error_code": "task_canceled"},
        }
        self.assertEqual(
            _task_item_outcome(canceled, ["pixiv"], source_available=True, canceled=True)["item_status"],
            "canceled",
        )

        archive_failed = {
            **success,
            "finalization": {"status": "failed", "error_code": "source_archive_failed"},
        }
        archive_outcome = _task_item_outcome(archive_failed, ["pixiv"], source_available=True)
        self.assertEqual(archive_outcome["item_status"], "failed")
        self.assertEqual(archive_outcome["reason_code"], "source_archive_failed")
        self.assertTrue(archive_outcome["retryable"])

        skipped = {
            "status_by_target": {
                "pixiv": "skipped_already_done",
                "civitai": "skipped_civitai_safety",
            },
            "pixiv": {"post_url": "https://www.pixiv.net/artworks/1"},
            "civitai": {},
            "finalization": {"status": "archived"},
        }
        skipped_outcome = _task_item_outcome(
            skipped,
            ["pixiv", "civitai"],
            source_available=False,
        )
        self.assertEqual(skipped_outcome["item_status"], "succeeded")
        self.assertEqual(skipped_outcome["targets"]["pixiv"]["status"], "skipped_already_done")
        self.assertEqual(skipped_outcome["targets"]["civitai"]["status"], "skipped_civitai_safety")

    def test_missing_file_and_early_stop_reasons_remain_structured(self) -> None:
        for reason_code in ("source_file_missing", "pixiv_risk_detected", "consecutive_failures"):
            with self.subTest(reason_code=reason_code):
                state = TaskProgressState(
                    build_progress_profile(3, {"targets": "pixiv"}),
                    items=["current.png", "later.png"],
                    targets=["pixiv"],
                )
                state.advance("reading_metadata", item_index=1, stage_progress=0.1)
                if reason_code == "source_file_missing":
                    state.advance(
                        "item_complete",
                        item_index=1,
                        item_status="failed",
                        retryable=False,
                        reason_code=reason_code,
                        targets={"pixiv": {"status": "failed", "error_code": reason_code}},
                    )
                closed = state.finish("failed", reason_code=reason_code)
                self.assertEqual(closed["items"][0]["reason_code"], reason_code)
                self.assertEqual(closed["items"][1]["status"], "unprocessed")
                self.assertEqual(closed["items"][1]["reason_code"], reason_code)


class WebTaskProgressTests(unittest.TestCase):
    def tearDown(self) -> None:
        with web.TASKS_LOCK:
            web.TASKS.clear()

    def test_log_index_text_does_not_drive_task_progress(self) -> None:
        task = web._new_task_record(
            "progress-test",
            3,
            {"targets": "pixiv", "files": ["image.png"]},
            title="发布 1 张图片",
            target="Pixiv",
            total=1,
        )
        with web.TASKS_LOCK:
            web.TASKS[task["id"]] = task

        with patch.object(web, "_broadcast_sse"):
            web._push_log_line(task["id"], "INFO", "pixiv", "[1/1] image.png")

        self.assertEqual(task["progress"], 0.0)
        self.assertEqual(task["current"], 0)
        self.assertEqual(task["stage"], "queued")

    def test_worker_consumes_explicit_publish_stage_events(self) -> None:
        params = {"targets": "pixiv", "files": ["image.png"], "llm_reverse": False}
        task = web._new_task_record(
            "worker-progress",
            3,
            params,
            title="发布 1 张图片",
            target="Pixiv",
            total=1,
        )
        task["status"] = "running"
        with web.TASKS_LOCK:
            web.TASKS[task["id"]] = task
        controller = web._TaskProgressController(task["id"], 3, params)

        def fake_upload(args):
            args.progress_callback("initializing", stage_progress=1.0, total=1)
            args.progress_callback(
                "reading_metadata",
                item_index=1,
                item_name="image.png",
                stage_progress=1.0,
            )
            args.progress_callback("opening_pixiv", item_index=1, stage_progress=1.0)
            args.progress_callback("filling_pixiv", item_index=1, stage_progress=0.4)
            return {
                "status": "failed",
                "total": 1,
                "processed": 1,
                "succeeded": 0,
                "failed": 1,
                "canceled": 0,
                "unprocessed": 0,
            }

        with (
            patch("pixiv_uploader.publishing.cmd_upload", side_effect=fake_upload),
            patch.object(web, "_broadcast_sse"),
        ):
            web._run_task_locked(task["id"], 3, params, controller)

        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["stage"], "filling_pixiv")
        self.assertEqual(task["current"], 1)
        self.assertEqual(task["failed"], 1)
        self.assertEqual(task["items"][0]["status"], "failed")
        self.assertEqual(task["items"][0]["reason_code"], "batch_stopped")
        self.assertLess(task["progress"], 1.0)

    def test_failed_controller_keeps_real_stage_and_progress_below_complete(self) -> None:
        task = web._new_task_record(
            "failed-progress",
            3,
            {"targets": "pixiv", "files": ["image.png"]},
            title="发布 1 张图片",
            target="Pixiv",
            total=1,
        )
        task["status"] = "running"
        with web.TASKS_LOCK:
            web.TASKS[task["id"]] = task
        controller = web._TaskProgressController(task["id"], 3, task["params"])

        with patch.object(web, "_broadcast_sse"):
            controller.report("opening_pixiv", item_index=1, stage_progress=1.0)
            controller.report("filling_pixiv", item_index=1, stage_progress=0.48)
            controller.finish("failed")

        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["stage"], "filling_pixiv")
        self.assertLess(task["progress"], 1.0)
        self.assertEqual(task["current"], 0)


if __name__ == "__main__":
    unittest.main()
