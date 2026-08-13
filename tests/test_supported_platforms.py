from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class SupportedPlatformTests(unittest.TestCase):
    def test_backend_platform_registry_contains_only_pixiv_copy_schema(self) -> None:
        from pixiv_uploader.pixiv.llm_platforms import (
            PLATFORM_SPECS,
            SUPPORTED_OUTPUT_CONSUMERS,
            required_field_keys,
            validate_platform_specs,
        )

        self.assertEqual(set(PLATFORM_SPECS), {"pixiv"})
        self.assertEqual(validate_platform_specs(), [])
        fields = PLATFORM_SPECS["pixiv"]["fields"] + PLATFORM_SPECS["pixiv"]["extra_fields"]
        self.assertEqual(len({field["key"] for field in fields}), len(fields))
        self.assertTrue(all(field.get("consumer") in SUPPORTED_OUTPUT_CONSUMERS for field in fields))
        self.assertEqual(
            required_field_keys("pixiv"),
            ["title_ja", "title_zh", "caption_ja", "caption_zh", "keywords"],
        )
        self.assertNotIn("description", {field["key"] for field in fields})

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
            response = self.client.post(
                "/api/run/2",
                json={"targets": "pixiv", "files": [r"C:\private\example.png"]},
            )

        self.assertEqual(response.status_code, 200)
        task_id = response.get_json()["task_id"]
        task = next(item for item in self.client.get("/api/tasks").get_json() if item["id"] == task_id)
        self.assertEqual(task["current"], 0)
        self.assertEqual(task["total"], 1)
        self.assertEqual(task["cmd"], 2)
        self.assertEqual(task["category"], "workflow")
        self.assertEqual(task["progress_version"], 4)
        self.assertEqual(task["progress"], 0.0)
        self.assertEqual(task["stage"], "queued")
        self.assertEqual(task["stage_label"], "等待执行")
        self.assertGreater(task["stage_count"], 2)
        self.assertEqual(task["item_index"], 0)
        self.assertEqual(task["activity"], {})
        self.assertEqual(task["succeeded"], 0)
        self.assertEqual(task["failed"], 0)
        self.assertEqual(task["canceled"], 0)
        self.assertEqual(len(task["items"]), 1)
        self.assertEqual(task["params"]["files"], ["example.png"])
        self.assertEqual(task["items"][0]["name"], "example.png")
        self.assertEqual(task["items"][0]["status"], "queued")
        self.assertNotIn("source_path", task["items"][0])
        self.assertNotIn("private", str(task["items"][0]))

    def test_maintenance_commands_are_classified_outside_workflow_tasks(self) -> None:
        with patch.object(self.web_server.threading.Thread, "start"):
            for command in (4, 5):
                with self.subTest(command=command):
                    response = self.client.post(f"/api/run/{command}", json={})
                    self.assertEqual(response.status_code, 200)
                    task_id = response.get_json()["task_id"]
                    task = next(item for item in self.client.get("/api/tasks").get_json() if item["id"] == task_id)
                    self.assertEqual(task["cmd"], command)
                    self.assertEqual(task["category"], "maintenance")

    def test_llm_platform_api_exposes_sample_editor_and_output_consumers(self) -> None:
        response = self.client.get("/api/llm-reverse-platforms")

        self.assertEqual(response.status_code, 200)
        pixiv = response.get_json()["pixiv"]
        fields = pixiv["fields"] + pixiv["extra_fields"]
        self.assertEqual({field["consumer"] for field in fields}, {"payload", "tag_candidates"})
        self.assertTrue(all(field.get("label_key") for field in fields))
        self.assertTrue(all(field.get("required") for field in fields))
        keywords = next(field for field in fields if field["key"] == "keywords")
        self.assertTrue(keywords["required"])
        self.assertEqual(keywords["min_count"], 6)
        self.assertIn("オリジナル", keywords["forbidden_values"])
        self.assertIn("#", keywords["forbidden_prefixes"])

    def test_llm_retry_policy_is_persisted_as_a_nested_partial_update(self) -> None:
        initial = self.client.post("/api/llm-reverse-config", json={
            "enabled": True,
            "provider": "openai_compatible",
            "base_url": "https://example.invalid/v1",
            "api_key": "secret-value",
            "model": "primary-model",
            "retry_policy": {
                "request_attempts": 4,
                "repair_attempts": 2,
                "fallback_models": ["fallback-a", "fallback-b"],
            },
        })
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.get_json()["retry_policy"]["request_attempts"], 4)
        self.assertNotIn("secret-value", initial.get_data(as_text=True))

        updated = self.client.post("/api/llm-reverse-config", json={
            "api_key": "",
            "retry_policy": {"total_timeout_seconds": 240},
        })
        self.assertEqual(updated.status_code, 200)
        policy = updated.get_json()["retry_policy"]
        self.assertEqual(policy["request_attempts"], 4)
        self.assertEqual(policy["repair_attempts"], 2)
        self.assertEqual(policy["total_timeout_seconds"], 240.0)
        self.assertEqual(policy["fallback_models"], ["fallback-a", "fallback-b"])

        saved = json.loads(self.config_path.read_text(encoding="utf-8"))["llm_reverse"]
        self.assertEqual(saved["api_key"], "secret-value")

    def test_llm_config_rejects_non_object_payload(self) -> None:
        response = self.client.post("/api/llm-reverse-config", json=["invalid"])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error_code"], "settings_must_be_object")

    def test_task_log_handler_uses_command_specific_source(self) -> None:
        handler = self.web_server._SseLogHandler(
            "task-id",
            self.web_server.CMD_LOG_SOURCES[3],
        )
        record = logging.LogRecord("pixiv_uploader", logging.INFO, "", 0, "message", (), None)

        with patch.object(self.web_server, "_push_log_line") as push:
            handler.emit(record)

        push.assert_called_once_with("task-id", "INFO", "pixiv", "message")

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
