from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pixiv_uploader.pixiv.support as support


class PixivCanonicalLookupTests(unittest.TestCase):
    def test_http_lookup_extracts_pixiv_canonical_tag(self) -> None:
        response = Mock(status_code=200)
        response.json.return_value = {
            "error": False,
            "body": {"pixpedia": {"parentTag": "東方"}},
        }
        with patch.object(support.httpx, "get", return_value=response) as get:
            result = support._fetch_pixiv_tag_canonical_via_http("touhou")

        self.assertEqual(result, "東方")
        self.assertIn("/ajax/search/tags/touhou", get.call_args.args[0])

    def test_live_alias_lookup_no_longer_requires_browser_page(self) -> None:
        cache = {}
        with patch.object(
            support,
            "_fetch_pixiv_tag_canonical_via_http",
            return_value="東方",
        ) as fetch:
            result = support.lookup_jp_alias("touhou", cache, page=None, live=True)

        self.assertEqual(result, "東方")
        self.assertEqual(cache["touhou"], "東方")
        fetch.assert_called_once_with("touhou")


class PixivBrowserReuseTests(unittest.TestCase):
    def test_profile_cdp_endpoint_ignores_stale_port_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profile = Path(temp_dir)
            (profile / "DevToolsActivePort").write_text("40123\n/devtools/browser/test", encoding="utf-8")
            with patch.object(support.httpx, "get", side_effect=support.httpx.ConnectError("closed")):
                self.assertIsNone(support._profile_cdp_endpoint(profile))

    def test_open_browser_attaches_to_login_window(self) -> None:
        page = Mock()
        page.url = "https://www.pixiv.net/"
        context = Mock()
        context.pages = [page]
        browser = SimpleNamespace(contexts=[context])
        chromium = Mock()
        chromium.connect_over_cdp.return_value = browser
        pw = SimpleNamespace(chromium=chromium)

        with patch.object(support, "_profile_cdp_endpoint", return_value="http://127.0.0.1:40123"):
            opened_context, opened_page = support.open_pixiv_browser(pw)

        self.assertIs(opened_context, context)
        self.assertIs(opened_page, page)
        chromium.connect_over_cdp.assert_called_once_with("http://127.0.0.1:40123")
        chromium.launch_persistent_context.assert_not_called()

    def test_profile_lock_error_is_actionable(self) -> None:
        chromium = Mock()
        chromium.launch_persistent_context.side_effect = RuntimeError("Target closed")
        pw = SimpleNamespace(chromium=chromium)

        with patch.object(support, "_profile_cdp_endpoint", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "关闭占用该账号配置的 Chrome 窗口"):
                support.open_pixiv_browser(pw)

    def test_attached_login_browser_is_not_closed_after_task(self) -> None:
        context = Mock()
        context._pixiv_uploader_attached = True

        support.close_pixiv_browser(context)

        context.close.assert_not_called()

    def test_owned_browser_is_closed_after_task(self) -> None:
        context = Mock(spec=["close"])

        support.close_pixiv_browser(context)

        context.close.assert_called_once_with()


class PixivUploadFailureTests(unittest.TestCase):
    def test_closed_browser_returns_one_actionable_failure_step(self) -> None:
        page = Mock()
        page.url = "https://www.pixiv.net/upload.php"
        page.is_closed.return_value = True
        page.locator.side_effect = RuntimeError(
            "Target page, context or browser has been closed"
        )

        progress_events = []
        with patch.object(support.random, "uniform", return_value=0):
            url, steps = support.create_pixiv_post(
                page,
                {},
                Path("image.png"),
                delay=0,
                progress_callback=lambda stage, **details: progress_events.append((stage, details)),
            )

        self.assertIsNone(url)
        self.assertEqual(progress_events, [("filling_pixiv", {"stage_progress": 0.0})])
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].name, "browser_session")
        self.assertEqual(steps[0].reason, "browser_closed")


class PixivUploadStartupTests(unittest.TestCase):
    def test_pixiv_browser_opens_after_manifest_preparation(self) -> None:
        import pixiv_uploader.publishing as splitter

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            upload_dir = root / "upload"
            upload_dir.mkdir()
            source = upload_dir / "source.png"
            source.write_bytes(b"not-read-by-this-test")
            files = {
                key: root / f"{key}.json"
                for key in (
                    "aliases", "popularity", "age_rules", "jp_aliases", "general_jp",
                    "danbooru_jp", "civitai_safety", "manifests", "validation",
                )
            }
            files["manifests"] = root / "manifests"
            files["manifests"].mkdir()
            events = []
            progress_events = []
            page = object()
            context = Mock()
            playwright = Mock()
            manager = Mock()
            manager.start.return_value = playwright

            manifest = {
                "status_by_target": {"pixiv": "pending"},
                "errors": [],
                "pixiv": {
                    "clean_copy_path": str(source),
                    "title_ja": "title",
                    "title_zh": "",
                    "caption_ja": "caption",
                    "caption_zh": "",
                    "final_tags": ["AIイラスト"],
                    "age_restriction": "all_ages",
                    "privacy": "public",
                    "allow_tag_edits": False,
                    "domain": "original",
                    "post_url": "",
                    "tagger": {"status": "disabled"},
                },
            }

            def prepare(**kwargs):
                self.assertIsNone(kwargs["pixiv_page"])
                events.append("prepare")
                return manifest, True

            def open_browser(_playwright):
                events.append("open")
                return context, page

            def publish(received_page, *_args, **kwargs):
                self.assertIs(received_page, page)
                events.append("publish")
                kwargs["progress_callback"]("filling_pixiv", stage_progress=0.48)
                kwargs["progress_callback"]("submitting_pixiv", stage_progress=1.0)
                kwargs["progress_callback"]("verifying_pixiv", stage_progress=1.0)
                return "https://www.pixiv.net/artworks/123", []

            args = SimpleNamespace(
                targets="pixiv", count=1, files=[source.name], sort="random",
                delay=0, dry_run=False, pixiv_privacy="public",
                pixiv_allow_tag_edits="false", pixiv_max_retries=0,
                abort_after_failures=3, llm_reverse=False,
                llm_persona="", llm_content_mode="",
                llm_personas_by_platform={}, llm_content_modes_by_platform={},
                ai_tags_by_platform={"pixiv": True}, cancel_event=None,
                progress_callback=lambda stage, **details: progress_events.append((stage, details)),
            )

            with ExitStack() as stack:
                stack.enter_context(patch.object(splitter, "SCRIPT_DIR", root))
                stack.enter_context(patch.object(splitter, "UPLOAD_DIR", upload_dir))
                stack.enter_context(patch.object(splitter, "DONE_DIR", root / "done"))
                stack.enter_context(patch.object(splitter, "TMP_DIR", root / ".tmp"))
                stack.enter_context(patch.object(splitter, "LOG_DIR", root / "logs"))
                stack.enter_context(patch.object(splitter, "ensure_runtime_files", return_value=files))
                stack.enter_context(patch.object(splitter, "load_json", return_value={}))
                stack.enter_context(patch.object(splitter, "_make_bridges", return_value=(Mock(), None)))
                stack.enter_context(patch.object(splitter, "_load_watermark_for_targets", return_value=(None, None)))
                stack.enter_context(patch.object(splitter, "load_llm_reverse_config", return_value={"enabled": False}))
                stack.enter_context(patch.object(splitter, "sync_playwright", return_value=manager))
                stack.enter_context(patch.object(splitter, "create_upload_manifest", side_effect=prepare))
                stack.enter_context(patch.object(splitter, "open_pixiv_browser", side_effect=open_browser))
                stack.enter_context(patch.object(splitter, "create_pixiv_post", side_effect=publish))
                stack.enter_context(patch.object(splitter, "find_target_successes", return_value={}))
                stack.enter_context(patch.object(splitter, "write_manifest"))
                stack.enter_context(patch.object(splitter, "save_json"))
                stack.enter_context(patch.object(splitter, "move_to_done", return_value=root / "done" / source.name))
                summary = splitter.cmd_upload(args)

            self.assertEqual(events, ["prepare", "open", "publish"])
            progress_stages = [stage for stage, _details in progress_events]
            self.assertLess(progress_stages.index("opening_pixiv"), progress_stages.index("filling_pixiv"))
            self.assertLess(progress_stages.index("filling_pixiv"), progress_stages.index("submitting_pixiv"))
            self.assertEqual(progress_stages[-1], "item_complete")
            self.assertEqual(progress_events[-1][1]["current"], 1)
            self.assertEqual(summary["status"], "success")
            self.assertEqual(summary["succeeded"], 1)
            context.close.assert_called_once_with()
            playwright.stop.assert_called_once_with()


class LoginBrowserLaunchTests(unittest.TestCase):
    def test_login_browser_exposes_local_cdp_endpoint(self) -> None:
        import pixiv_uploader.web as web_server

        with tempfile.TemporaryDirectory() as temp_dir, \
             patch.object(web_server, "_find_chrome_executable", return_value="chrome"), \
             patch.object(web_server.subprocess, "Popen") as popen:
            web_server._open_login_browser(Path(temp_dir) / "profile", "https://www.pixiv.net/")

        command = popen.call_args.args[0]
        self.assertIn("--remote-debugging-address=127.0.0.1", command)
        self.assertIn("--remote-debugging-port=0", command)


if __name__ == "__main__":
    unittest.main()
