from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pixiv_uploader.pixiv.support as support
from pixiv_uploader.pixiv.standalone import StandaloneTaggerBridge
from pixiv_uploader.pixiv.tagger_settings import (
    load_haintag_settings,
    pixai_model_ready,
    resolve_cl_model_dir,
    resolve_pixai_model_dir,
    save_haintag_settings,
    scan_cl_model_dir,
)


class TaggerSettingsTests(unittest.TestCase):
    def test_resolver_ignores_stale_absolute_path_and_finds_project_models(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cl_dir = root / "models" / "cl_tagger"
            cl_dir.mkdir(parents=True)
            (cl_dir / "model.onnx").write_bytes(b"model")
            (cl_dir / "selected_tags.csv").write_text("name,category\n", encoding="utf-8")
            pixai_dir = root / "models" / "pixai_tagger"
            pixai_dir.mkdir(parents=True)
            (pixai_dir / "model.onnx").write_bytes(b"model")
            (pixai_dir / "selected_tags.csv").write_text("name,category\n", encoding="utf-8")
            settings = {
                "tagger_model_dir": "E:/deleted-project/models/cl_tagger",
                "pixai_tagger_model_dir": "E:/deleted-project/models/pixai_tagger",
            }

            self.assertEqual(resolve_cl_model_dir(settings, root), cl_dir.resolve())
            self.assertEqual(resolve_pixai_model_dir(settings, root), pixai_dir.resolve())

    def test_incomplete_or_missing_model_directories_are_not_usable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "model.onnx").write_bytes(b"model")

            self.assertEqual(scan_cl_model_dir(root), (root / "model.onnx", None))
            self.assertFalse(pixai_model_ready(root))
            with patch(
                "pixiv_uploader.pixiv.tagger_settings.haintag_settings_path",
                return_value=root / "HainTag" / "settings.json",
            ):
                self.assertIsNone(resolve_cl_model_dir({"tagger_model_dir": root}, root / "project"))
            result = StandaloneTaggerBridge(root).predict_tags(root / "image.png")
            self.assertEqual(result["status"], "model_dir_not_configured")

    def test_nested_haintag_settings_are_preserved_when_updated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "settings.json"
            path.write_text(
                json.dumps({"settings": {"theme": "dark", "tagger_model_dir": "old"}}),
                encoding="utf-8",
            )

            save_haintag_settings({"tagger_model_dir": "new"}, path)

            self.assertEqual(
                load_haintag_settings(path),
                {"theme": "dark", "tagger_model_dir": "new"},
            )

    def test_haintag_bridge_uses_resolved_model_and_data_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model_dir = root / "models" / "cl_tagger"
            model_dir.mkdir(parents=True)
            model_path = model_dir / "model.onnx"
            mapping_path = model_dir / "selected_tags.csv"
            model_path.write_bytes(b"model")
            mapping_path.write_text("name,category\n", encoding="utf-8")
            calls = {}

            class Engine:
                def __init__(self, model_dir=None):
                    calls["constructor_model_dir"] = model_dir
                    self.is_ready = False

                def find_model(self, custom_dir=None, appdata_dir=None):
                    calls["find_model"] = (custom_dir, appdata_dir)
                    return str(model_path), str(mapping_path)

                def load(self, received_model, received_mapping, external_python=None):
                    calls["load"] = (received_model, received_mapping, external_python)
                    self.is_ready = True

            bridge = support.HainTagTaggerBridge(root)
            settings_path = root / "HainTag" / "settings.json"
            with patch.object(support, "resolve_cl_model_dir", return_value=model_dir), \
                 patch.object(support, "resolve_tagger_python", return_value=None), \
                 patch.object(support, "haintag_settings_path", return_value=settings_path), \
                 patch.object(
                     support.importlib,
                     "import_module",
                     return_value=SimpleNamespace(TaggerEngine=Engine),
                 ):
                engine = bridge._ensure_engine()

            self.assertIsInstance(engine, Engine)
            self.assertEqual(calls["constructor_model_dir"], str(model_dir))
            self.assertEqual(calls["find_model"], (str(model_dir), str(settings_path.parent)))
            self.assertEqual(calls["load"], (str(model_path), str(mapping_path), None))


if __name__ == "__main__":
    unittest.main()
