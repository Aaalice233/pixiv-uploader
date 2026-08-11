from __future__ import annotations

import base64
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from PIL import Image

from pixiv_uploader.pixiv import llm_reverse


def _retry_config(**policy_overrides):
    config = llm_reverse.default_llm_reverse_config()
    config.update({
        "enabled": True,
        "base_url": "https://example.invalid/v1",
        "api_key": "secret-key",
        "model": "primary-model",
        "timeout_seconds": 10,
    })
    config["retry_policy"].update({
        "request_attempts": 3,
        "repair_attempts": 1,
        "base_delay_seconds": 0.1,
        "max_delay_seconds": 0.2,
        "total_timeout_seconds": 30,
        **policy_overrides,
    })
    return config


def _response(status: int, payload, headers=None) -> httpx.Response:
    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    return httpx.Response(status, json=payload, headers=headers, request=request)


def _success_fields(title: str = "夜の光") -> dict:
    return {
        "title_ja": title,
        "title_zh": "夜之光",
        "caption_ja": "静かな夜のイラストです。",
        "caption_zh": "描绘宁静夜晚的插画。",
        "keywords": ["女の子", "夜", "星空", "白髪", "青い目", "ドレス"],
    }


def _success_response(title: str = "夜の光") -> httpx.Response:
    content = json.dumps(_success_fields(title), ensure_ascii=False)
    return _response(200, {"choices": [{"message": {"content": content}}]})


def _client_with(*side_effects):
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    client.post.side_effect = list(side_effects)
    return client


class LlmImagePreviewTests(unittest.TestCase):
    def test_image_is_resized_and_encoded_as_jpeg_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "large-transparent.png"
            Image.new("RGBA", (3072, 1024), (10, 20, 30, 128)).save(source)

            data_url = llm_reverse._image_to_data_url(source)

        prefix, encoded = data_url.split(",", 1)
        self.assertEqual(prefix, "data:image/jpeg;base64")
        preview_bytes = base64.b64decode(encoded)
        self.assertLess(len(preview_bytes), 2 * 1024 * 1024)
        with Image.open(BytesIO(preview_bytes)) as preview:
            self.assertEqual(preview.format, "JPEG")
            self.assertEqual(preview.mode, "RGB")
            self.assertEqual(preview.size, (1536, 512))

    def test_file_extension_does_not_control_preview_mime_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "misnamed.png"
            Image.new("RGB", (64, 64), "white").save(source, format="JPEG")

            data_url = llm_reverse._image_to_data_url(source)

        self.assertTrue(data_url.startswith("data:image/jpeg;base64,"))


class LlmRetryPolicyTests(unittest.TestCase):
    def test_partial_few_shot_sample_is_not_sent_to_the_model(self) -> None:
        spec = llm_reverse.get_merged_spec(["pixiv"])
        persona = {
            "platform": ["pixiv"],
            "samples": [
                {"mode": "sfw", "note": "incomplete", "fields": {"title_ja": "不足例"}},
                {"mode": "sfw", "note": "complete", "fields": _success_fields("完成例")},
            ],
        }

        block = llm_reverse._render_samples_block(persona, "sfw", spec)

        self.assertNotIn("不足例", block)
        self.assertIn("完成例", block)
        self.assertIn("keywords", block)

    def test_pixiv_prompt_requires_actionable_visual_keywords(self) -> None:
        payload = llm_reverse._build_request_payload(
            _retry_config(),
            {"voice": "", "sfw_prompt": "", "extra_prompt": "", "avoid": []},
            "sfw",
            llm_reverse.PLATFORM_SPECS["pixiv"],
            "https://example.invalid/image.jpg",
        )
        prompt = payload["messages"][0]["content"][0]["text"]

        required_line = next(line for line in prompt.splitlines() if line.startswith("Return only"))
        self.assertIn("keywords", required_line.split("Optional keys:")[0])
        self.assertIn("6-16 concise", prompt)
        self.assertIn("do not include hashtags", prompt)

    def test_retry_policy_is_normalized_and_primary_model_is_not_duplicated(self) -> None:
        normalized = llm_reverse.normalize_llm_reverse_config({
            "enabled": "false",
            "model": "primary-model",
            "timeout_seconds": "9999",
            "retry_policy": {
                "request_attempts": 99,
                "repair_attempts": -2,
                "base_delay_seconds": 5,
                "max_delay_seconds": 1,
                "total_timeout_seconds": "bad",
                "adaptive_image": "false",
                "fallback_models": "primary-model, fallback-a\nfallback-a\nfallback-b\nfallback-c\nfallback-d",
            },
        })

        self.assertFalse(normalized["enabled"])
        self.assertEqual(normalized["timeout_seconds"], 300.0)
        self.assertEqual(normalized["retry_policy"]["request_attempts"], 6)
        self.assertEqual(normalized["retry_policy"]["repair_attempts"], 0)
        self.assertEqual(normalized["retry_policy"]["max_delay_seconds"], 5.0)
        self.assertFalse(normalized["retry_policy"]["adaptive_image"])
        self.assertEqual(
            normalized["retry_policy"]["fallback_models"],
            ["fallback-a", "fallback-b", "fallback-c"],
        )

    def test_transient_failures_honor_request_retries_and_recover(self) -> None:
        timeout = httpx.ReadTimeout(
            "read timed out",
            request=httpx.Request("POST", "https://example.invalid/v1/chat/completions"),
        )
        client = _client_with(
            timeout,
            _response(429, {"error": {"message": "busy"}}, {"Retry-After": "0"}),
            _success_response(),
        )
        events = []

        with patch.object(llm_reverse.httpx, "Client", return_value=client), patch.object(
            llm_reverse, "_wait_for_retry"
        ) as wait_for_retry:
            result = llm_reverse.infer_image_copy(
                image_url="https://example.invalid/image.jpg",
                config=_retry_config(request_attempts=3, repair_attempts=0),
                event_callback=lambda event, details: events.append((event, details)),
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["retry"]["attempt_count"], 3)
        self.assertEqual(result["retry"]["retry_count"], 2)
        self.assertTrue(result["retry"]["recovered"])
        self.assertEqual([item["error_code"] for item in result["retry"]["history"][:-1]], ["read_timeout", "rate_limited"])
        self.assertEqual(wait_for_retry.call_count, 2)
        self.assertEqual(events[-1][0], "succeeded")

    def test_authentication_failure_is_not_retried_or_failed_over(self) -> None:
        client = _client_with(_response(401, {"error": {"message": "invalid key"}}))
        config = _retry_config(fallback_models=["fallback-model"])

        with patch.object(llm_reverse.httpx, "Client", return_value=client):
            result = llm_reverse.infer_image_copy(
                image_url="https://example.invalid/image.jpg",
                config=config,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "authentication_failed")
        self.assertEqual(result["retry"]["attempt_count"], 1)
        self.assertEqual(result["retry"]["models_tried"], ["primary-model"])
        self.assertFalse(result["retry"]["fallback_used"])

    def test_required_visual_keywords_must_be_distinct(self) -> None:
        fields = _success_fields()
        fields["keywords"] = ["夜"] * 6

        with self.assertRaisesRegex(ValueError, "keywords"):
            llm_reverse._normalize_output(fields, llm_reverse.PLATFORM_SPECS["pixiv"])

    def test_missing_required_visual_keywords_runs_strict_repair_round(self) -> None:
        incomplete = _response(200, {
            "choices": [{"message": {"content": json.dumps({
                key: value for key, value in _success_fields().items() if key != "keywords"
            }, ensure_ascii=False)}}]
        })
        client = _client_with(incomplete, _success_response("タグ修復済み"))

        with patch.object(llm_reverse.httpx, "Client", return_value=client):
            result = llm_reverse.infer_image_copy(
                image_url="https://example.invalid/image.jpg",
                config=_retry_config(request_attempts=1, repair_attempts=1),
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["fields"]["title_ja"], "タグ修復済み")
        self.assertGreaterEqual(len(result["fields"]["keywords"]), 6)
        self.assertEqual(result["retry"]["repair_count"], 1)
        second_payload = client.post.call_args_list[1].kwargs["json"]
        prompt = second_payload["messages"][0]["content"][0]["text"]
        self.assertIn("Every required field must be non-empty", prompt)

    def test_invalid_model_output_runs_strict_repair_round(self) -> None:
        invalid = _response(200, {"choices": [{"message": {"content": "not-json"}}]})
        client = _client_with(invalid, _success_response("修復済み"))

        with patch.object(llm_reverse.httpx, "Client", return_value=client):
            result = llm_reverse.infer_image_copy(
                image_url="https://example.invalid/image.jpg",
                config=_retry_config(request_attempts=1, repair_attempts=1),
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["fields"]["title_ja"], "修復済み")
        self.assertEqual(result["retry"]["repair_count"], 1)
        second_payload = client.post.call_args_list[1].kwargs["json"]
        prompt = second_payload["messages"][0]["content"][0]["text"]
        self.assertIn("REPAIR:", prompt)
        self.assertIn("invalid_model_json", prompt)

    def test_exhausted_primary_model_uses_configured_fallback(self) -> None:
        client = _client_with(
            _response(404, {"error": {"message": "model not found"}}),
            _success_response("後備成功"),
        )

        with patch.object(llm_reverse.httpx, "Client", return_value=client):
            result = llm_reverse.infer_image_copy(
                image_url="https://example.invalid/image.jpg",
                config=_retry_config(
                    request_attempts=1,
                    repair_attempts=0,
                    fallback_models=["fallback-model"],
                ),
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["model"], "fallback-model")
        self.assertEqual(result["retry"]["models_tried"], ["primary-model", "fallback-model"])
        self.assertTrue(result["retry"]["fallback_used"])
        sent_models = [call.kwargs["json"]["model"] for call in client.post.call_args_list]
        self.assertEqual(sent_models, ["primary-model", "fallback-model"])

    def test_timeout_uses_smaller_preview_on_the_next_attempt(self) -> None:
        timeout = httpx.ReadTimeout(
            "read timed out",
            request=httpx.Request("POST", "https://example.invalid/v1/chat/completions"),
        )
        client = _client_with(timeout, _success_response())
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.png"
            Image.new("RGB", (2048, 2048), "white").save(source)
            with patch.object(llm_reverse.httpx, "Client", return_value=client), patch.object(
                llm_reverse, "_wait_for_retry"
            ), patch.object(
                llm_reverse,
                "_image_to_data_url",
                side_effect=["data:image/jpeg;base64,first", "data:image/jpeg;base64,second"],
            ) as encode:
                result = llm_reverse.infer_image_copy(
                    image_path=source,
                    config=_retry_config(request_attempts=2, repair_attempts=0),
                )

        self.assertEqual(result["status"], "ok")
        self.assertEqual([call.kwargs["max_edge"] for call in encode.call_args_list], [1536, 1280])
        first_image = client.post.call_args_list[0].kwargs["json"]["messages"][0]["content"][1]["image_url"]["url"]
        second_image = client.post.call_args_list[1].kwargs["json"]["messages"][0]["content"][1]["image_url"]["url"]
        self.assertEqual((first_image, second_image), ("data:image/jpeg;base64,first", "data:image/jpeg;base64,second"))

    def test_payload_too_large_is_not_retried_without_an_adaptable_local_preview(self) -> None:
        client = _client_with(_response(413, {"error": {"message": "payload too large"}}))
        with patch.object(llm_reverse.httpx, "Client", return_value=client):
            result = llm_reverse.infer_image_copy(
                image_url="https://example.invalid/image.jpg",
                config=_retry_config(request_attempts=3, repair_attempts=0),
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "payload_too_large")
        self.assertEqual(result["retry"]["attempt_count"], 1)
        self.assertEqual(client.post.call_count, 1)

    def test_cancel_event_interrupts_retry_backoff(self) -> None:
        class CancelOnWait:
            def is_set(self):
                return False

            def wait(self, delay):
                return True

        timeout = httpx.ConnectTimeout(
            "connect timed out",
            request=httpx.Request("POST", "https://example.invalid/v1/chat/completions"),
        )
        client = _client_with(timeout)
        with patch.object(llm_reverse.httpx, "Client", return_value=client):
            with self.assertRaises(InterruptedError):
                llm_reverse.infer_image_copy(
                    image_url="https://example.invalid/image.jpg",
                    config=_retry_config(request_attempts=2, repair_attempts=0),
                    cancel_event=CancelOnWait(),
                )
        self.assertEqual(client.post.call_count, 1)

    def test_retry_engine_preserves_native_provider_request_shapes(self) -> None:
        content = json.dumps(_success_fields("ネイティブ成功"), ensure_ascii=False)
        cases = [
            (
                "anthropic",
                _response(200, {"content": [{"type": "text", "text": content}]}),
                "https://api.anthropic.com/v1/messages",
                "x-api-key",
            ),
            (
                "google_gemini",
                _response(200, {"candidates": [{"content": {"parts": [{"text": content}]}}]}),
                "https://generativelanguage.googleapis.com/v1beta/models/primary-model:generateContent",
                "x-goog-api-key",
            ),
        ]

        for provider, response, expected_url, api_key_header in cases:
            with self.subTest(provider=provider):
                config = _retry_config(request_attempts=1, repair_attempts=0)
                config.update({"provider": provider, "base_url": ""})
                client = _client_with(response)
                with patch.object(llm_reverse.httpx, "Client", return_value=client):
                    result = llm_reverse.infer_image_copy(
                        image_url="https://example.invalid/image.jpg",
                        config=config,
                    )

                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["fields"]["title_ja"], "ネイティブ成功")
                call = client.post.call_args
                self.assertEqual(call.args[0], expected_url)
                self.assertEqual(call.kwargs["headers"][api_key_header], "secret-key")
                if provider == "anthropic":
                    self.assertEqual(call.kwargs["json"]["model"], "primary-model")
                else:
                    self.assertEqual(call.kwargs["json"]["contents"][0]["role"], "user")

    def test_provider_error_never_exposes_credentials(self) -> None:
        upstream_message = (
            "secret-key failed at https://user:pass@example.invalid/path?api_key=leaked-query "
            "Authorization: Bearer leaked-token"
        )
        client = _client_with(_response(500, {"error": {"message": upstream_message}}))
        with patch.object(llm_reverse.httpx, "Client", return_value=client):
            result = llm_reverse.infer_image_copy(
                image_url="https://example.invalid/image.jpg",
                config=_retry_config(request_attempts=1, repair_attempts=0),
            )

        serialized = json.dumps(result, ensure_ascii=False)
        for secret in ("secret-key", "user:pass", "leaked-query", "leaked-token"):
            self.assertNotIn(secret, serialized)
        self.assertIn("***", result["error"])

    def test_task_activity_payload_is_bounded_and_json_safe(self) -> None:
        activity = llm_reverse.build_llm_retry_activity(
            "retry_scheduled",
            {
                "attempt": "2",
                "max_attempts": 6,
                "delay_seconds": "1.25",
                "model": "model-name\ninjected",
                "error_code": "rate_limited",
                "ignored": {"not": "serializable"},
            },
        )

        self.assertEqual(activity["kind"], "llm_retry")
        self.assertEqual(activity["attempt"], 2)
        self.assertEqual(activity["delay_seconds"], 1.25)
        self.assertEqual(activity["model"], "model-name injected")
        self.assertNotIn("ignored", activity)
        json.dumps(activity)

    def test_retry_after_supports_seconds_milliseconds_and_http_dates(self) -> None:
        seconds = _response(429, {}, {"Retry-After": "2.5"})
        milliseconds = _response(429, {}, {"Retry-After-Ms": "750"})
        future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)
        http_date = _response(429, {}, {"Retry-After": future})

        self.assertEqual(llm_reverse._retry_after_seconds(seconds), 2.5)
        self.assertEqual(llm_reverse._retry_after_seconds(milliseconds), 0.75)
        self.assertGreater(llm_reverse._retry_after_seconds(http_date), 25.0)
        self.assertLessEqual(llm_reverse._retry_after_seconds(http_date), 30.0)


if __name__ == "__main__":
    unittest.main()
