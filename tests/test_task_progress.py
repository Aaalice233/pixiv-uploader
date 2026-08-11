from __future__ import annotations

import unittest
from unittest.mock import patch

from pixiv_uploader.task_progress import TaskProgressState, build_progress_profile
import pixiv_uploader.web as web


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

    def test_split_can_report_real_work_fraction_across_interleaved_stages(self) -> None:
        state = TaskProgressState(build_progress_profile(1, {}), total=4)
        downloaded = state.advance(
            "split_download",
            stage_progress=1.0,
            overall_progress=0.35,
        )
        published = state.advance(
            "split_publish",
            stage_progress=1.0,
            overall_progress=0.5,
        )

        self.assertGreater(published["progress"], downloaded["progress"])
        self.assertLess(published["progress"], 1.0)


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
