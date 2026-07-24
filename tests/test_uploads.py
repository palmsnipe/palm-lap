#!/usr/bin/env python3
"""Upload-route regression tests using an isolated temporary file store."""

import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

try:
    import flask  # noqa: F401
except ModuleNotFoundError as exc:
    raise unittest.SkipTest("Flask is not installed in this Python environment") from exc


REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("palm_web", REPO_ROOT / "src" / "palm_web.py")
palm_web = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(palm_web)


def palm_database(name="Test", db_type=b"appl", creator=b"TEST"):
    content = bytearray(78)
    encoded_name = name.encode("latin-1")[:31]
    content[:len(encoded_name)] = encoded_name
    content[60:64] = db_type
    content[64:68] = creator
    return bytes(content)


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        palm_web.STATE_DIR = root / "state"
        palm_web.STORE = palm_web.STATE_DIR / "files"
        palm_web.JOBS_FILE = palm_web.STATE_DIR / "jobs.json"
        palm_web.CONFIG_PATH = root / "web.json"
        palm_web.CONFIG_PATH.write_text(json.dumps({
            "admin_networks": ["127.0.0.0/8"],
            "gateway_ip": "10.77.0.1",
            "gateway_name": "Test LAP",
            "max_upload_bytes": 1024 * 1024,
            "max_upload_files": 2,
        }), encoding="utf-8")
        self.app = palm_web.create_app()
        self.app.testing = True
        self.client = self.app.test_client()

    def tearDown(self):
        self.temporary.cleanup()

    def post(self, data):
        data["csrf"] = palm_web.csrf_token
        return self.client.post("/admin/upload", data=data, content_type="multipart/form-data")

    def test_upload_input_does_not_filter_unknown_ios_file_types(self):
        source = (REPO_ROOT / "src" / "palm_web.py").read_text(encoding="utf-8")

        self.assertIn('id="upload-files" name="files" multiple required', source)
        self.assertNotIn('accept=".prc,.pdb"', source)

    def test_uploads_multiple_files(self):
        response = self.post({"files": [
            (io.BytesIO(palm_database("One")), "One.prc"),
            (io.BytesIO(palm_database("Two", db_type=b"DATA")), "Two.pdb"),
        ]})

        self.assertEqual(response.status_code, 302)
        self.assertTrue((palm_web.STORE / "One.prc").is_file())
        self.assertTrue((palm_web.STORE / "Two.pdb").is_file())
        self.assertIn("Uploaded+2+files", response.headers["Location"])

    def test_accepts_legacy_single_file_field(self):
        response = self.post({
            "file": (io.BytesIO(palm_database("Legacy")), "Legacy.prc"),
        })

        self.assertEqual(response.status_code, 302)
        self.assertTrue((palm_web.STORE / "Legacy.prc").is_file())

    def test_rejects_duplicate_normalized_names_without_publishing(self):
        response = self.post({"files": [
            (io.BytesIO(palm_database("One")), "same?.prc"),
            (io.BytesIO(palm_database("Two")), "same*.prc"),
        ]})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(palm_web.STORE.iterdir()), [])

    def test_invalid_file_prevents_entire_batch_from_publishing(self):
        response = self.post({"files": [
            (io.BytesIO(palm_database("Valid")), "valid.prc"),
            (io.BytesIO(b"not a Palm database"), "invalid.pdb"),
        ]})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(palm_web.STORE.iterdir()), [])

    def test_rejects_more_than_configured_batch_limit(self):
        response = self.post({"files": [
            (io.BytesIO(palm_database(str(number))), f"{number}.prc")
            for number in range(3)
        ]})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(palm_web.STORE.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
