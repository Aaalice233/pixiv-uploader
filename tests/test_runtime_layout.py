from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pixiv_uploader.paths import runtime_paths
from pixiv_uploader.pixiv.storage import append_validation_case, ensure_runtime_files, load_json
from pixiv_uploader.runtime import ensure_runtime_layout


class RuntimeLayoutTests(unittest.TestCase):
    def test_fresh_layout_seeds_local_config_and_reads_compressed_dictionary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            files = ensure_runtime_files(root)
            paths = runtime_paths(root)

            self.assertEqual(files["manifests"], paths.manifests)
            self.assertEqual(files["censor_config"], paths.pixiv / "censor.json")
            self.assertTrue(files["aliases"].is_file())
            self.assertTrue(files["civitai_safety"].is_file())
            self.assertEqual(load_json(files["validation"], {}), {"cases": []})
            self.assertEqual(len(load_json(files["popularity"], {}).get("groups", {})), 1953)
            self.assertEqual(len(load_json(files["danbooru_jp"], {})), 151262)
            files["age_rules"].write_text('{"custom": true}', encoding="utf-8")
            ensure_runtime_files(root)
            self.assertEqual(load_json(files["age_rules"], {}), {"custom": True})
            self.assertFalse((root / "pixiv").exists())

    def test_legacy_runtime_data_moves_without_overwriting_newer_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "manifests").mkdir()
            (root / "manifests" / "old.json").write_text("{}", encoding="utf-8")
            (root / "watermark_fonts").mkdir()
            (root / "watermark_fonts" / "font.ttf").write_bytes(b"font")
            (root / "watermark.json").write_text('{"enabled": false}', encoding="utf-8")
            legacy_samples = root / "pixiv" / "rule_fit" / "samples"
            legacy_samples.mkdir(parents=True)
            (legacy_samples / "old.json").write_text("{}", encoding="utf-8")
            current_samples = root / "runtime" / "pixiv" / "rule_fit" / "samples"
            current_samples.mkdir(parents=True)
            (current_samples / "current.json").write_text("{}", encoding="utf-8")

            paths = ensure_runtime_layout(root)

            self.assertTrue((paths.manifests / "old.json").is_file())
            self.assertTrue((paths.watermark / "fonts" / "font.ttf").is_file())
            self.assertTrue((paths.watermark / "config.json").is_file())
            self.assertTrue((paths.pixiv_rule_fit / "samples" / "old.json").is_file())
            self.assertTrue((paths.pixiv_rule_fit / "samples" / "current.json").is_file())
            self.assertFalse((root / "manifests").exists())
            self.assertFalse((root / "watermark_fonts").exists())
            self.assertFalse((root / "watermark.json").exists())
            self.assertFalse((root / "pixiv").exists())

    def test_migration_keeps_conflicting_legacy_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy = root / "pixiv" / "censor.json"
            current = root / "runtime" / "pixiv" / "censor.json"
            legacy.parent.mkdir(parents=True)
            current.parent.mkdir(parents=True)
            legacy.write_text('{"preset": "strict"}', encoding="utf-8")
            current.write_text('{"preset": "off"}', encoding="utf-8")

            with self.assertLogs("pixiv_uploader", level="WARNING") as captured:
                ensure_runtime_layout(root)

            self.assertIn("未覆盖", "\n".join(captured.output))
            self.assertEqual(json.loads(current.read_text(encoding="utf-8"))["preset"], "off")
            self.assertEqual(json.loads(legacy.read_text(encoding="utf-8"))["preset"], "strict")

    def test_validation_cases_replace_exact_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            validation_path = root / "validation.json"
            source = root / "same.png"
            manifest = {
                "pixiv": {
                    "domain": "original",
                    "raw_candidates": ["blue_hair"],
                    "final_tags": ["青髪"],
                    "title_ja": "title",
                    "title_zh": "标题",
                }
            }

            append_validation_case(source, validation_path, manifest)
            append_validation_case(source, validation_path, manifest)

            cases = json.loads(validation_path.read_text(encoding="utf-8"))["cases"]
            self.assertEqual(len(cases), 1)


if __name__ == "__main__":
    unittest.main()
