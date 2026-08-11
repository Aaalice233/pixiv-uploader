from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class SupportedPlatformTests(unittest.TestCase):
    def test_backend_platform_registry_contains_only_pixiv_copy_schema(self) -> None:
        from pixiv_uploader.pixiv.llm_platforms import PLATFORM_SPECS

        self.assertEqual(set(PLATFORM_SPECS), {"pixiv"})

    def test_upload_target_parser_accepts_only_civitai_and_pixiv(self) -> None:
        import pixiv_uploader.publishing as civitai_splitter

        self.assertEqual(civitai_splitter.parse_targets("civitai,pixiv"), ["civitai", "pixiv"])
        self.assertEqual(civitai_splitter.parse_targets("pixiv,pixiv"), ["pixiv"])
        with self.assertRaisesRegex(ValueError, "不支持的 targets"):
            civitai_splitter.parse_targets("x")
        with self.assertRaisesRegex(ValueError, "不支持的 targets"):
            civitai_splitter.parse_targets("civitai,xhs")

    def test_llm_normalization_drops_unsupported_personas_and_retired_keys(self) -> None:
        from pixiv_uploader.pixiv.llm_reverse import normalize_llm_reverse_config

        normalized = normalize_llm_reverse_config({
            "accounts": [{"id": "old"}],
            "default_account_id": "old",
            "personas": [
                {"id": "unsupported", "platform": "xhs", "label": "old"},
                {"id": "supported", "platform": "pixiv", "label": "Pixiv"},
            ],
            "default_persona_id": "unsupported",
        })

        self.assertEqual([persona["id"] for persona in normalized["personas"]], ["supported"])
        self.assertEqual(normalized["default_persona_id"], "supported")
        self.assertNotIn("accounts", normalized)
        self.assertNotIn("default_account_id", normalized)


class PlatformApiTests(unittest.TestCase):
    def setUp(self) -> None:
        import pixiv_uploader.web as web_server

        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.config_path = self.root / "config.json"
        self.script_patch = patch.object(web_server, "SCRIPT_DIR", self.root)
        self.config_patch = patch.object(web_server, "CONFIG_FILE", self.config_path)
        self.script_patch.start()
        self.config_patch.start()
        self.web_server = web_server
        self.client = web_server.app.test_client()

    def tearDown(self) -> None:
        self.config_patch.stop()
        self.script_patch.stop()
        self.temp_dir.cleanup()

    def test_publish_api_rejects_unknown_targets_before_starting_task(self) -> None:
        response = self.client.post("/api/run/2", json={"targets": "civitai,xhs", "files": []})

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn("不支持的发布平台", payload["error"])
        self.assertEqual(payload["error_code"], "invalid_targets")
        self.assertEqual(payload["error_params"], {})

    def test_api_errors_expose_locale_independent_codes(self) -> None:
        response = self.client.post("/api/run/2", json={"targets": "", "files": []})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error_code"], "target_required")

        response = self.client.post("/api/settings", json=["invalid"])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error_code"], "settings_must_be_object")

        response = self.client.get("/api/not-a-real-endpoint")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error_code"], "not_found")

    def test_task_payload_exposes_structured_image_progress(self) -> None:
        with patch.object(self.web_server.threading.Thread, "start"):
            response = self.client.post("/api/run/2", json={"targets": "pixiv", "files": ["example.png"]})

        self.assertEqual(response.status_code, 200)
        task_id = response.get_json()["task_id"]
        task = next(item for item in self.client.get("/api/tasks").get_json() if item["id"] == task_id)
        self.assertEqual(task["current"], 0)
        self.assertEqual(task["total"], 1)
        self.assertEqual(task["cmd"], 2)

    def test_upload_defaults_are_reduced_to_current_two_platform_schema(self) -> None:
        self.config_path.write_text(json.dumps({
            "upload_defaults": {
                "targets": "civitai,pixiv,xhs",
                "sort": "random",
                "x_template": "old",
                "xhs_template": "old",
                "ai_tags_by_platform": {"pixiv": False, "xhs": True},
            }
        }), encoding="utf-8")

        response = self.client.get("/api/upload-defaults")
        payload = response.get_json()

        self.assertEqual(payload["targets"], "civitai,pixiv")
        self.assertEqual(payload["ai_tags_by_platform"], {"pixiv": False})
        self.assertNotIn("x_template", payload)
        self.assertNotIn("xhs_template", payload)

    def test_legacy_target_list_is_normalized_without_widening_selection(self) -> None:
        self.config_path.write_text(json.dumps({"upload_defaults": {"targets": ["pixiv"]}}), encoding="utf-8")

        response = self.client.get("/api/upload-defaults")

        self.assertEqual(response.get_json()["targets"], "pixiv")

    def test_scheduler_post_rejects_unknown_target(self) -> None:
        response = self.client.post("/api/scheduler", json={"targets": "x"})

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertIn("不支持的发布平台", payload["error"])
        self.assertEqual(payload["error_code"], "invalid_scheduler")


if __name__ == "__main__":
    unittest.main()
