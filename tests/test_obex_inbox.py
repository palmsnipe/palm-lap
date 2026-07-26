#!/usr/bin/env python3
"""Pure-function tests for the Bluetooth Object Push inbox."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

try:
    import dbus  # noqa: F401
except ModuleNotFoundError as exc:
    raise unittest.SkipTest("dbus-python is not installed in this Python environment") from exc


REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "palm_obex_inbox", REPO_ROOT / "src" / "palm-obex-inbox.py"
)
inbox_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inbox_module)


class InboxTests(unittest.TestCase):
    def test_safe_filename_removes_paths_and_control_characters(self):
        self.assertEqual(
            inbox_module.safe_filename("../Palm\x00Backup.prc"),
            "Palm_Backup.prc",
        )
        self.assertEqual(inbox_module.safe_filename(".."), "received-file")

    def test_unique_destination_does_not_overwrite_existing_or_reserved(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory)
            (inbox / "Memo.pdb").write_bytes(b"existing")

            destination = inbox_module.unique_destination(
                inbox, "Memo.pdb", [inbox / "Memo-2.pdb"]
            )

            self.assertEqual(destination.name, "Memo-3.pdb")

    def test_recovery_discards_incomplete_payload_and_records_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory)
            payload = inbox / "partial.prc"
            payload.write_bytes(b"partial")
            record = {
                "id": "abc123",
                "status": "receiving",
                "stored_name": payload.name,
            }
            inbox_module.write_metadata(inbox, record)

            inbox_module.recover_interrupted(inbox)

            self.assertFalse(payload.exists())
            recovered = json.loads(
                inbox_module.metadata_path(inbox, "abc123").read_text(encoding="utf-8")
            )
            self.assertEqual(recovered["status"], "interrupted")
            self.assertIn("finished_at", recovered)


if __name__ == "__main__":
    unittest.main()
