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


class PixivPublishVerificationTests(unittest.TestCase):
    def test_only_known_submit_destinations_can_confirm_a_redirect(self) -> None:
        self.assertTrue(support._is_pixiv_submit_destination("https://www.pixiv.net/users/74968612"))
        self.assertTrue(support._is_pixiv_submit_destination("https://www.pixiv.net/users/74968612/artworks"))
        self.assertTrue(support._is_pixiv_submit_destination("https://www.pixiv.net/manage/illusts/"))
        self.assertFalse(support._is_pixiv_submit_destination("https://www.pixiv.net/"))
        self.assertFalse(support._is_pixiv_submit_destination("https://accounts.pixiv.net/login"))
        self.assertFalse(support._is_pixiv_submit_destination("https://example.invalid/users/74968612"))

    def test_profile_redirect_resolves_matching_artwork_url(self) -> None:
        page = Mock()
        page.evaluate.return_value = "123456789"

        result = support._resolve_posted_artwork_url(page, "午後の羽音")

        self.assertEqual(result, "https://www.pixiv.net/artworks/123456789")
        page.wait_for_load_state.assert_called_once_with("domcontentloaded", timeout=10000)
        self.assertEqual(page.evaluate.call_args.args[1]["expectedTitle"], "午後の羽音")


class PixivLlmTagPipelineTests(unittest.TestCase):
    def test_llm_keywords_are_sanitized_deduplicated_and_drop_reserved_tags(self) -> None:
        import pixiv_uploader.publishing as publishing

        result = {
            "status": "ok",
            "fields": {
                "keywords": [
                    "#女の子",
                    "女の子",
                    "白髪、小鳥",
                    "オリジナル",
                    "オリジナルイラスト",
                    "AIイラスト",
                    "https://example.invalid/tag",
                    "  木漏れ日  ",
                ]
            },
        }

        self.assertEqual(
            publishing._extract_llm_visual_keywords(result),
            ["女の子", "白髪", "小鳥", "木漏れ日"],
        )

    def test_llm_keywords_are_reprocessed_through_pixiv_tag_pipeline(self) -> None:
        import pixiv_uploader.publishing as publishing

        def payload(tags: list[str]) -> dict:
            return {
                "raw_candidates": [],
                "metadata_entity_hits": [],
                "popularity_decisions": [],
                "rejected_tags": [],
                "final_tags": tags,
                "final_tag_translations": list(tags),
                "entity_tags": [],
                "entity_tags_zh": [],
                "domain": "original",
                "title_ja": "無題",
                "title_zh": "无题",
                "caption_ja": "",
                "caption_zh": "",
                "age_restriction": "all_ages",
                "ai_generated": True,
            }

        build_payload = Mock(
            side_effect=[
                payload(["オリジナル", "AIイラスト"]),
                payload(["オリジナル", "AIイラスト", "女の子", "白髪", "小鳥"]),
            ]
        )
        llm_result = {
            "enabled": True,
            "status": "ok",
            "content_mode": "sfw",
            "fields": {
                "title_ja": "午後の羽音",
                "title_zh": "午后的羽音",
                "caption_ja": "静かな午後。",
                "caption_zh": "安静的午后。",
                "keywords": ["オリジナル", "女の子", "白髪", "小鳥"],
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.png"
            clean = root / "source_clean.png"
            source.write_bytes(b"source")
            metadata = {"available": False, "status": "unavailable", "detected_types": [], "details": []}
            with patch.object(
                publishing, "sanitize_image_for_pixiv", return_value=SimpleNamespace(output_path=clean)
            ), patch.object(
                publishing, "build_pixiv_payload", build_payload
            ), patch.object(
                publishing, "infer_image_copy", return_value=llm_result
            ), patch.object(
                publishing, "resolve_persona", return_value=({}, "sfw")
            ), patch.object(
                publishing, "append_validation_case"
            ):
                manifest, pixiv_ready = publishing.create_upload_manifest(
                    image_path=source,
                    targets=["pixiv"],
                    files={"validation": root / "validation.json"},
                    hain_bridge=SimpleNamespace(read_metadata=lambda _: metadata),
                    alias_data={},
                    popularity_data={},
                    age_rules={},
                    civitai_dir=root,
                    pixiv_dir=root,
                    pixiv_privacy="public",
                    pixiv_allow_tag_edits=False,
                    llm_reverse_config={"enabled": True},
                    ai_tags_by_platform={"pixiv": True},
                )

        self.assertTrue(pixiv_ready)
        self.assertEqual(build_payload.call_count, 2)
        general_tags = build_payload.call_args_list[1].kwargs["extra_groups"]["general"]
        self.assertEqual(general_tags, [("女の子", 1.0), ("白髪", 1.0), ("小鳥", 1.0)])
        self.assertEqual(
            manifest["pixiv"]["final_tags"],
            ["オリジナル", "AIイラスト", "女の子", "白髪", "小鳥"],
        )
        self.assertEqual(manifest["pixiv"]["title_ja"], "午後の羽音")
        self.assertEqual(manifest["pixiv"]["title_zh"], "午后的羽音")
        self.assertEqual(manifest["pixiv"]["caption_ja"], "静かな午後。")
        self.assertEqual(manifest["pixiv"]["caption_zh"], "安静的午后。")
        tagging = manifest["pixiv"]["llm_reverse"]["tagging"]
        self.assertEqual(tagging["status"], "applied")
        self.assertEqual(tagging["candidate_count"], 3)
        self.assertEqual(tagging["added_tags"], ["女の子", "白髪", "小鳥"])

    def test_original_tag_reserves_one_of_pixivs_ten_slots(self) -> None:
        alias_data = {
            "mappings": {},
            "semantics": {
                "ai_art": {
                    "candidates": ["AIイラスト"],
                    "default": "AIイラスト",
                    "zh": "AI插画",
                    "class": "meta",
                    "domain": "both",
                }
            },
        }
        payload = support.build_pixiv_payload(
            image_path=Path("source.png"),
            metadata_info={},
            alias_data=alias_data,
            popularity_data={},
            age_rules={},
            extra_groups={"general": [(f"視覚タグ{i}", 1.0) for i in range(12)]},
            general_jp_data={"force_original": True},
            live_lookup=False,
            live_jp_lookup=False,
        )

        self.assertEqual(len(payload["final_tags"]), 10)
        self.assertEqual(payload["final_tags"][0], "オリジナル")
        self.assertIn("AIイラスト", payload["final_tags"])


class PixivBrowserReuseTests(unittest.TestCase):
    def test_open_browser_always_owns_a_persistent_context_without_remote_debugging(self) -> None:
        page = Mock()
        page.is_closed.return_value = False
        context = Mock()
        context.pages = [page]
        chromium = Mock()
        chromium.launch_persistent_context.return_value = context
        pw = SimpleNamespace(chromium=chromium)

        with patch.object(support.PIXIV_SESSION, "ensure_profile_identity"):
            opened_context, opened_page = support.open_pixiv_browser(pw)

        self.assertIs(opened_context, context)
        self.assertIs(opened_page, page)
        call = chromium.launch_persistent_context.call_args
        self.assertEqual(call.args[0], str(support.PIXIV_PROFILE_DIR))
        self.assertTrue(call.kwargs["no_viewport"])
        self.assertNotIn("args", call.kwargs)
        self.assertFalse(hasattr(chromium, "connect_over_cdp") and chromium.connect_over_cdp.called)

    def test_profile_lock_error_is_actionable_and_coded(self) -> None:
        chromium = Mock()
        chromium.launch_persistent_context.side_effect = RuntimeError(
            "Failed to create a ProcessSingleton for your profile directory"
        )
        pw = SimpleNamespace(chromium=chromium)

        with self.assertRaises(support.PixivFlowError) as caught:
            support.open_pixiv_browser(pw)

        self.assertEqual(caught.exception.code, "pixiv_profile_locked_external")
        self.assertIn("外部 Chrome", str(caught.exception))

    def test_owned_browser_is_always_closed_after_flow(self) -> None:
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
        self.assertEqual(steps[0].reason, "pixiv_browser_closed")


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
                stack.enter_context(patch.object(splitter, "_acquire_pixiv_profile_for_task", return_value=object()))
                stack.enter_context(patch.object(splitter, "PixivRateController"))
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


class UploadFinalizationTests(unittest.TestCase):
    def test_confirmed_success_moves_source_out_of_upload(self) -> None:
        import pixiv_uploader.publishing as publishing

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            upload = root / "upload"
            done = root / "done"
            upload.mkdir()
            source = upload / "image.png"
            source.write_bytes(b"confirmed")

            with patch.object(publishing, "DONE_DIR", done):
                destination = publishing.move_to_done(source)

            self.assertFalse(source.exists())
            self.assertTrue(destination.is_file())
            self.assertEqual(destination.parent, done)
            self.assertEqual(destination.read_bytes(), b"confirmed")

    def test_archive_failure_does_not_report_a_fake_destination(self) -> None:
        import pixiv_uploader.publishing as publishing

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing_source = root / "upload" / "missing.png"
            with patch.object(publishing, "DONE_DIR", root / "done"):
                with self.assertRaises(FileNotFoundError):
                    publishing.move_to_done(missing_source)

            self.assertFalse((root / "done").exists())


class LoginBrowserLaunchTests(unittest.TestCase):
    def test_legacy_login_launcher_is_only_used_for_civitai(self) -> None:
        import pixiv_uploader.web as web_server

        with patch.object(web_server, "_open_login_browser") as open_legacy:
            response = web_server.app.test_client().post("/api/civitai-open-login")

        self.assertEqual(response.status_code, 200)
        open_legacy.assert_called_once_with(
            web_server.CIVITAI_PROFILE_DIR,
            "https://civitai.com/login?returnUrl=/",
        )


if __name__ == "__main__":
    unittest.main()
