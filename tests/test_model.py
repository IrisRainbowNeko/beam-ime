import hashlib
import tempfile
import unittest
from pathlib import Path
from beamlib.model import install
from beamlib.config import schema_entry
import yaml


class ModelTests(unittest.TestCase):
    def test_failed_import_preserves_model(self):
        with tempfile.TemporaryDirectory() as root:
            source, target = Path(root)/"source", Path(root)/"model.gguf"
            source.write_bytes(b"bad")
            target.write_bytes(b"previous")
            spec = {"size": 4, "sha256": hashlib.sha256(b"good").hexdigest()}
            with self.assertRaises(ValueError): install(spec, target, source)
            self.assertEqual(target.read_bytes(), b"previous")
            self.assertFalse(list(Path(root).glob("*.part")))
            source.write_bytes(b"good")
            install(spec, target, source)
            self.assertEqual(target.read_bytes(), b"good")

    def test_no_implicit_download(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError): install({}, Path(root)/"model")

    def test_schema_roundtrip_preserves_user_fields(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/"default.custom.yaml"
            original = {"patch": {"schema_list": [{"schema": "rime_ice"}], "menu/page_size": 9}}
            path.write_text(yaml.safe_dump(original))
            schema_entry(path, True)
            schema_entry(path, True)
            data = yaml.safe_load(path.read_text())
            self.assertEqual(len(data["patch"]["schema_list"]), 2)
            data["patch"]["new_user_setting"] = True
            path.write_text(yaml.safe_dump(data))
            schema_entry(path, False)
            data = yaml.safe_load(path.read_text())
            self.assertTrue(data["patch"]["new_user_setting"])
            self.assertEqual(data["patch"]["menu/page_size"], 9)
            self.assertEqual(data["patch"]["schema_list"], [{"schema": "rime_ice"}])
