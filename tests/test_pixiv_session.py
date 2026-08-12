from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, PropertyMock, patch

import pixiv_uploader.pixiv.support as support
from pixiv_uploader.pixiv.session import (
    PixivProfileInUseError,
    PixivRateController,
    PixivSessionStore,
)


class MutableClock:
    def __init__(self, value: float = 1_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class PixivSessionStoreTests(unittest.TestCase):
    def test_existing_profile_without_verification_is_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile = root / "profile"
            profile.mkdir()
            store = PixivSessionStore(profile_dir=profile, state_path=root / "session.json")

            snapshot = store.snapshot()

        self.assertEqual(snapshot["state"], "unverified")
        self.assertFalse(snapshot["state"] == "authenticated")

    def test_missing_profile_never_reuses_stale_authenticated_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile = root / "profile"
            profile.mkdir()
            store = PixivSessionStore(profile_dir=profile, state_path=root / "session.json")
            store.update_verified("authenticated")
            for child in profile.iterdir():
                child.unlink()
            profile.rmdir()

            snapshot = store.snapshot()
            profile.mkdir()
            recreated = store.snapshot()

        self.assertEqual(snapshot["state"], "missing")
        self.assertEqual(snapshot["verified_state"], "")
        self.assertEqual(recreated["state"], "unverified")

    def test_legacy_authenticated_state_without_profile_identity_becomes_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile = root / "profile"
            profile.mkdir()
            state_path = root / "session.json"
            state_path.write_text(
                '{"state":"authenticated","last_verified_at":"2026-01-01T00:00:00+00:00"}',
                encoding="utf-8",
            )
            store = PixivSessionStore(profile_dir=profile, state_path=state_path)

            snapshot = store.snapshot()

        self.assertEqual(snapshot["state"], "unverified")
        self.assertEqual(snapshot["verified_state"], "")

    def test_profile_lease_is_exclusive_and_reports_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = PixivSessionStore(profile_dir=root / "profile", state_path=root / "session.json")
            lease = store.acquire("publishing")
            with self.assertRaises(support.PixivProfileInUseError) as caught:
                store.acquire("login:web")
            self.assertEqual(caught.exception.owner, "publishing")
            self.assertEqual(store.snapshot()["state"], "in_use")
            store.release(lease)

        self.assertEqual(store.snapshot()["state"], "missing")

    def test_profile_lease_is_exclusive_across_store_instances(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile = root / "profile"
            first = PixivSessionStore(profile_dir=profile, state_path=root / "first.json")
            second = PixivSessionStore(profile_dir=profile, state_path=root / "second.json")
            lease = first.acquire("publishing")
            try:
                with self.assertRaises(support.PixivProfileInUseError) as caught:
                    second.acquire("logout:web")
                self.assertEqual(caught.exception.owner, "publishing")
                self.assertEqual(second.snapshot()["in_use_by"], "publishing")
            finally:
                first.release(lease)

    def test_login_flow_closes_owned_context_and_publishes_authenticated_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile = root / "profile"
            profile.mkdir()
            store = PixivSessionStore(profile_dir=profile, state_path=root / "session.json")
            states: list[str] = []
            store.add_listener(lambda snapshot: states.append(snapshot["state"]))
            context = Mock()
            page = Mock()

            def authenticate(*_args, **_kwargs) -> None:
                store.update_verified("authenticated")

            with patch.object(support, "PIXIV_SESSION", store), patch.object(
                support, "open_pixiv_browser", return_value=(context, page)
            ), patch.object(
                support, "ensure_on_pixiv_upload_page", side_effect=authenticate
            ):
                support.run_pixiv_login_flow(SimpleNamespace(), owner="login:test")

        context.close.assert_called_once_with()
        self.assertEqual(store.snapshot()["state"], "authenticated")
        self.assertIn("checking", states)
        self.assertEqual(states[-1], "authenticated")

    def test_closing_login_window_before_verification_requires_login(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile = root / "profile"
            profile.mkdir()
            store = PixivSessionStore(profile_dir=profile, state_path=root / "session.json")
            context = Mock()
            with patch.object(support, "PIXIV_SESSION", store), patch.object(
                support, "open_pixiv_browser", return_value=(context, Mock())
            ), patch.object(
                support,
                "ensure_on_pixiv_upload_page",
                side_effect=support.PixivBrowserClosedError(),
            ):
                with self.assertRaises(support.PixivFlowError) as caught:
                    support.run_pixiv_login_flow(SimpleNamespace(), owner="login:test")

        self.assertEqual(caught.exception.code, "pixiv_login_browser_closed")
        self.assertEqual(store.snapshot()["state"], "login_required")
        context.close.assert_called_once_with()


class PixivAuthenticationRecoveryTests(unittest.TestCase):
    def test_upload_page_redirect_to_login_waits_and_resumes_automatically(self) -> None:
        page = Mock()
        wait = Mock()

        with patch.object(support, "safe_goto"), patch.object(
            support, "_page_url", side_effect=["https://www.pixiv.net/", "https://accounts.pixiv.net/login"]
        ), patch.object(
            support.PIXIV_SESSION, "update_verified"
        ) as update, patch.object(
            support, "wait_for_pixiv_authentication", wait
        ):
            support.ensure_on_pixiv_upload_page(
                page,
                cancel_event=threading.Event(),
                interaction_callback=Mock(),
            )

        update.assert_called_once_with("login_required")
        wait.assert_called_once()


class PixivCaptchaStateMachineTests(unittest.TestCase):
    def test_pre_submit_captcha_waits_then_allows_one_submit(self) -> None:
        page = Mock()
        publish = Mock()
        publish.is_enabled.side_effect = [False, True]
        activities: list[dict | None] = []
        captcha_active = {"present": True, "active": True, "provider": "recaptcha", "token_present": False}
        captcha_done = {"present": False, "active": False, "provider": "", "token_present": True}

        with patch.object(support, "_detect_pixiv_captcha", side_effect=[captcha_active, captcha_done]), patch.object(
            support, "_sleep_with_cancel"
        ), patch.object(support, "_notify_user_once") as notify:
            result = support._wait_for_pre_submit_ready(
                page,
                publish,
                interaction_callback=activities.append,
            )

        self.assertEqual(result, (True, "recaptcha"))
        notify.assert_called_once_with(page)
        self.assertEqual(activities[-1], None)
        self.assertTrue(any(activity and activity["interaction_type"] == "pixiv_captcha_before_submit" for activity in activities))

    def _run_post(
        self,
        *,
        pre_submit_captcha: bool,
        post_submit_captcha: bool,
        post_submit_url: str = "https://www.pixiv.net/upload.php",
    ):
        page = Mock()
        file_input = Mock()
        publish = Mock()
        page_urls = (
            [post_submit_url, "https://www.pixiv.net/artworks/123"]
            if post_submit_captcha
            else ["https://www.pixiv.net/artworks/123"]
        )
        steps: list[support.PixivStep] = []
        activities: list[dict | None] = []
        monitor = Mock()
        monitor.consume.return_value = None
        successful_step = lambda name, *_args, **_kwargs: support.PixivStep(name, True)
        locator_results = [file_input, None, publish]
        captcha_active = {"present": True, "active": True, "provider": "hcaptcha", "token_present": False}
        captcha_done = {"present": False, "active": False, "provider": "", "token_present": True}
        payload = {
            "title_ja": "title",
            "caption_ja": "",
            "caption_zh": "",
            "final_tags": ["AIイラスト"],
            "age_restriction": "all_ages",
            "privacy": "public",
            "allow_tag_edits": False,
            "domain": "original",
        }
        with patch.object(type(page), "url", new_callable=PropertyMock, create=True) as page_url, patch.object(
            support, "ensure_on_pixiv_upload_page"
        ), patch.object(
            support, "_first_visible_locator", side_effect=locator_results
        ), patch.object(support, "_fill_if_found", side_effect=successful_step), patch.object(
            support, "_fill_tag_input", side_effect=successful_step
        ), patch.object(support, "_set_radio_by_attr", side_effect=successful_step), patch.object(
            support, "_set_checkbox_by_attr", side_effect=successful_step
        ), patch.object(support, "_accept_safety_check", return_value=support.PixivStep("safety_check", True)), patch.object(
            support,
            "_wait_for_pre_submit_ready",
            return_value=(pre_submit_captcha, "recaptcha" if pre_submit_captcha else ""),
        ), patch.object(support, "_human_move_and_click") as click, patch.object(
            support, "_sleep_with_cancel"
        ), patch.object(support.time, "sleep"), patch.object(
            support, "_resolve_posted_artwork_url", return_value=None
        ), patch.object(
            support,
            "_detect_pixiv_captcha",
            side_effect=(
                [captcha_active, captcha_done]
                if post_submit_captcha
                else [{"present": False, "active": False, "provider": "", "token_present": False}]
            ),
        ):
            page_url.side_effect = page_urls
            result = support._create_pixiv_post(
                page,
                payload,
                Path("image.png"),
                0,
                None,
                None,
                steps,
                interaction_callback=activities.append,
                http_monitor=monitor,
            )
        return result, click, activities

    def test_captcha_before_submit_still_clicks_exactly_once(self) -> None:
        result, click, _activities = self._run_post(pre_submit_captcha=True, post_submit_captcha=False)

        click.assert_called_once()
        self.assertEqual(result.url, "https://www.pixiv.net/artworks/123")
        self.assertEqual(result.risk_signal, "captcha")

    def test_captcha_after_submit_never_triggers_a_second_automatic_click(self) -> None:
        result, click, activities = self._run_post(pre_submit_captcha=False, post_submit_captcha=True)

        click.assert_called_once()
        self.assertEqual(result.url, "https://www.pixiv.net/artworks/123")
        self.assertEqual(result.risk_signal, "captcha")
        self.assertTrue(any(activity and activity["interaction_type"] == "pixiv_captcha_after_submit" for activity in activities))

    def test_post_submit_captcha_is_detected_on_pixiv_management_landing(self) -> None:
        result, click, activities = self._run_post(
            pre_submit_captcha=False,
            post_submit_captcha=True,
            post_submit_url="https://www.pixiv.net/manage/illusts",
        )

        click.assert_called_once()
        self.assertEqual(result.url, "https://www.pixiv.net/artworks/123")
        self.assertEqual(result.risk_signal, "captcha")
        self.assertTrue(any(
            activity and activity["interaction_type"] == "pixiv_captcha_after_submit"
            for activity in activities
        ))

    def test_cancel_interrupts_captcha_wait(self) -> None:
        cancel_event = threading.Event()
        cancel_event.set()
        with self.assertRaises(InterruptedError):
            support._wait_for_pre_submit_ready(
                Mock(),
                Mock(),
                cancel_event=cancel_event,
            )


class PixivRateControllerTests(unittest.TestCase):
    def test_three_risk_levels_persist_deduplicate_and_decay(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "risk.json"
            clock = MutableClock()
            controller = PixivRateController(
                state_path=state_path,
                clock=clock,
                sleeper=clock.sleep,
                random_uniform=lambda low, _high: low,
            )
            level_one = controller.record_risk("captcha", work_key="image-a")
            unrelated_429 = controller.record_risk("http_429", work_key="image-a")
            duplicate = controller.record_risk("captcha", work_key="image-a")
            level_two = controller.record_risk("captcha", work_key="image-b")
            level_three = controller.record_risk("http_429", work_key="image-c")
            reloaded = PixivRateController(state_path=state_path, clock=clock)

            self.assertEqual(level_one["risk_level"], 1)
            self.assertEqual(unrelated_429["risk_level"], 2)
            self.assertEqual(duplicate["risk_level"], 2)
            self.assertEqual(level_two["risk_level"], 3)
            self.assertEqual(level_three["risk_level"], 3)
            self.assertEqual(reloaded.snapshot()["risk_level"], 3)

            clock.value += level_three["cooldown_remaining_seconds"] + 1
            controller.wait(poll_seconds=10_000)
            controller.record_success(risk_signal=True)
            controller.record_success()
            controller.record_success()
            self.assertEqual(controller.record_success()["risk_level"], 2)
            clock.value += 2 * 24 * 60 * 60 + 1
            self.assertEqual(controller.snapshot()["risk_level"], 0)

    def test_normal_delay_uses_point_eight_to_one_point_four_jitter(self) -> None:
        ranges: list[tuple[float, float]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = PixivRateController(
                state_path=Path(temp_dir) / "risk.json",
                random_uniform=lambda low, high: ranges.append((low, high)) or high,
            )
            snapshot = controller.schedule_baseline(10)

        self.assertEqual(ranges, [(8.0, 14.0)])
        self.assertGreaterEqual(snapshot["cooldown_remaining_seconds"], 13)

    def test_baseline_does_not_replace_an_active_risk_cooldown_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "risk.json"
            clock = MutableClock()
            controller = PixivRateController(
                state_path=state_path,
                clock=clock,
                random_uniform=lambda low, _high: low,
            )
            risk = controller.record_risk("captcha", work_key="image")
            baseline = controller.schedule_baseline(10)

        self.assertEqual(baseline["cooldown_reason"], "captcha")
        self.assertEqual(baseline["cooldown_until"], risk["cooldown_until"])

    def test_http_429_uses_its_current_risk_level_unless_retry_after_is_longer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "risk.json"
            clock = MutableClock()
            controller = PixivRateController(
                state_path=state_path,
                clock=clock,
                random_uniform=lambda low, _high: low,
            )

            snapshot = controller.record_risk("http_429", work_key="image", retry_after=60)

        self.assertEqual(snapshot["risk_level"], 1)
        self.assertEqual(snapshot["cooldown_remaining_seconds"], 120)

    def test_retry_after_supports_seconds_and_http_dates(self) -> None:
        self.assertEqual(PixivRateController.parse_retry_after("120", now=1_000), 120)
        self.assertEqual(
            PixivRateController.parse_retry_after("Thu, 01 Jan 1970 00:20:00 GMT", now=1_000),
            200,
        )

    def test_cooldown_wait_is_cancelable(self) -> None:
        cancel_event = threading.Event()
        cancel_event.set()
        with tempfile.TemporaryDirectory() as temp_dir:
            controller = PixivRateController(
                state_path=Path(temp_dir) / "risk.json",
                random_uniform=lambda low, _high: low,
            )
            controller.record_risk("captcha", work_key="image")
            with self.assertRaises(InterruptedError):
                controller.wait(cancel_event=cancel_event)


class PixivProfileQueueTests(unittest.TestCase):
    def test_publish_fails_explicitly_when_standalone_login_owns_profile(self) -> None:
        import pixiv_uploader.publishing as publishing

        in_use = PixivProfileInUseError("login:web")
        session = Mock()
        session.acquire.side_effect = in_use
        activities: list[dict | None] = []
        with patch.object(publishing, "PIXIV_SESSION", session):
            with self.assertRaises(PixivProfileInUseError) as caught:
                publishing._acquire_pixiv_profile_for_task(
                    interaction_callback=activities.append,
                )

        self.assertEqual(caught.exception.owner, "login:web")
        self.assertEqual(activities, [None])

    def test_publish_profile_acquire_is_cancelable(self) -> None:
        import pixiv_uploader.publishing as publishing

        cancel_event = threading.Event()
        cancel_event.set()
        with self.assertRaises(InterruptedError):
            publishing._acquire_pixiv_profile_for_task(cancel_event=cancel_event)


class PixivWebContractTests(unittest.TestCase):
    def test_status_api_exposes_structured_session_and_compatible_boolean(self) -> None:
        import pixiv_uploader.web as web

        session = {
            "state": "authenticated",
            "last_verified_at": "2026-01-01T00:00:00+00:00",
            "risk_level": 2,
            "cooldown_until": "2026-01-01T00:05:00+00:00",
        }
        with patch.object(web, "_pixiv_session_payload", return_value=session):
            response = web.app.test_client().get("/api/status")

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["pixiv_session"], session)
        self.assertTrue(payload["pixiv_logged_in"])

    def test_status_sse_broadcast_contains_session_and_compatible_boolean(self) -> None:
        import pixiv_uploader.web as web

        with patch.object(web, "_pixiv_session_payload", return_value={"state": "login_required"}), patch.object(
            web, "_broadcast_sse"
        ) as broadcast:
            web._broadcast_pixiv_session({"state": "login_required"})

        event, payload = broadcast.call_args.args
        self.assertEqual(event, "status_update")
        self.assertEqual(payload["pixiv_session"]["state"], "login_required")
        self.assertFalse(payload["pixiv_logged_in"])

    def test_interaction_callback_preserves_progress_and_restores_running(self) -> None:
        import pixiv_uploader.web as web

        task_id = "pixiv-interaction-test"
        params = {"targets": "pixiv", "count": 1}
        task = web._new_task_record(
            task_id,
            3,
            params,
            title="Pixiv",
            target="Pixiv",
            total=1,
        )
        task["status"] = "running"
        task["progress"] = 0.42
        with web.TASKS_LOCK:
            web.TASKS[task_id] = task
        controller = web._TaskProgressController(task_id, 3, params)
        try:
            with patch.object(web, "_broadcast_sse"):
                controller.interaction({
                    "kind": "pixiv_interaction",
                    "interaction_type": "pixiv_login",
                    "remaining_seconds": 900,
                })
                with web.TASKS_LOCK:
                    waiting = web._task_snapshot(web.TASKS[task_id])
                controller.interaction(None)
                with web.TASKS_LOCK:
                    resumed = web._task_snapshot(web.TASKS[task_id])
        finally:
            with web.TASKS_LOCK:
                web.TASKS.pop(task_id, None)

        self.assertEqual(waiting["status"], "waiting_input")
        self.assertEqual(waiting["progress"], 0.42)
        self.assertEqual(waiting["activity"]["interaction_type"], "pixiv_login")
        self.assertEqual(resumed["status"], "running")
        self.assertEqual(resumed["progress"], 0.42)
        self.assertEqual(resumed["activity"], {})

    def test_login_and_logout_return_409_while_pixiv_task_is_active(self) -> None:
        import pixiv_uploader.web as web

        with patch.object(web, "_has_active_pixiv_task", return_value=True):
            client = web.app.test_client()
            login = client.post("/api/pixiv-open-login")
            logout = client.post("/api/pixiv-logout")

        self.assertEqual(login.status_code, 409)
        self.assertEqual(login.get_json()["error_code"], "pixiv_profile_in_use")
        self.assertEqual(logout.status_code, 409)
        self.assertEqual(logout.get_json()["error_code"], "pixiv_profile_in_use")

    def test_publish_task_returns_409_while_standalone_login_owns_profile(self) -> None:
        import pixiv_uploader.web as web

        with patch.object(web.PIXIV_SESSION, "snapshot", return_value={"in_use_by": "login:web"}), patch.object(
            web.threading.Thread, "start"
        ) as start:
            response = web.app.test_client().post(
                "/api/run/3",
                json={"targets": "pixiv", "files": ["image.png"]},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error_code"], "pixiv_profile_in_use")
        start.assert_not_called()

    def test_second_pixiv_task_is_rejected_after_first_is_atomically_queued(self) -> None:
        import pixiv_uploader.web as web

        with patch.dict(web.TASKS, {}, clear=True), patch.object(
            web.PIXIV_SESSION, "snapshot", return_value={"in_use_by": None}
        ), patch.object(web.threading.Thread, "start") as start:
            client = web.app.test_client()
            first = client.post(
                "/api/run/3",
                json={"targets": "pixiv", "files": ["first.png"]},
            )
            second = client.post(
                "/api/run/3",
                json={"targets": "pixiv", "files": ["second.png"]},
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.get_json()["error_code"], "pixiv_profile_in_use")
        start.assert_called_once()


class PixivRetrySafetyTests(unittest.TestCase):
    def test_maybe_posted_result_can_never_enter_retry(self) -> None:
        import pixiv_uploader.publishing as publishing

        result = support.PixivPostResult(
            None,
            [],
            error_code="pixiv_submit_unconfirmed",
            maybe_posted=True,
        )

        decision = publishing._pixiv_retry_decision(result, [], attempt=0, max_retries=5)

        self.assertEqual(decision, "stop_uncertain")

    def test_429_before_click_can_retry_but_after_click_cannot(self) -> None:
        import pixiv_uploader.publishing as publishing

        before = support.PixivPostResult(None, [], error_code="pixiv_rate_limited", batch_fatal=True)
        clicked = [support.PixivStep("publish_click", True)]
        after = support.PixivPostResult(None, clicked, error_code="pixiv_rate_limited_after_submit", maybe_posted=True)

        self.assertEqual(
            publishing._pixiv_retry_decision(before, [], attempt=0, max_retries=1),
            "retry_after_cooldown",
        )
        self.assertEqual(
            publishing._pixiv_retry_decision(before, [], attempt=1, max_retries=1),
            "stop_batch",
        )
        self.assertEqual(
            publishing._pixiv_retry_decision(after, clicked, attempt=0, max_retries=5),
            "stop_uncertain",
        )


if __name__ == "__main__":
    unittest.main()
