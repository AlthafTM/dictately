import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import download_turbo
import runtime_paths


class InstallerSupportTests(unittest.TestCase):
    def test_relocated_frozen_root_and_explicit_override(self):
        with TemporaryDirectory() as temporary, patch.dict(os.environ):
            root = Path(temporary).resolve()
            os.environ.pop("TRANSCRIBR_ROOT", None)
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(root / "runtime/worker/TranscribrWorker.exe")):
                self.assertEqual(runtime_paths.app_root(), root)
                os.environ["TRANSCRIBR_ROOT"] = str(root / "override")
                self.assertEqual(runtime_paths.app_root(), root / "override")

    def test_invalid_partial_download_is_repaired_on_retry(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assets").mkdir()
            destination = root / "models/whisper-turbo"
            destination.mkdir(parents=True)
            content = b'{"valid":true}'
            (destination / "config.json").write_bytes(b"partial")
            spec = [{"file":"config.json", "bytes":len(content), "revision":"pinned",
                     "sha256":hashlib.sha256(content).hexdigest()}]
            (root / "assets/speech-model.json").write_text(json.dumps(spec))
            response = SimpleNamespace(content=content, raise_for_status=lambda:None)
            with patch.object(download_turbo, "ROOT", root), patch.object(download_turbo, "DEST", destination), patch.object(download_turbo.requests, "get", return_value=response) as get:
                download_turbo.main()
                self.assertIn("/resolve/pinned/", get.call_args.args[0])
                self.assertEqual((destination / "config.json").read_bytes(), content)
                get.reset_mock()
                download_turbo.main()
                get.assert_not_called()

    def test_cleanup_removes_only_owned_numeric_fragments(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            chunks = root / "download-parts/chunks"
            chunks.mkdir(parents=True)
            (chunks / "0").write_text("chunk")
            (chunks / "notes.txt").write_text("keep")
            (chunks.parent / "0000").write_text("part")
            download_turbo.discard_verified_parts(root)
            self.assertFalse((chunks / "0").exists())
            self.assertFalse((chunks.parent / "0000").exists())
            self.assertEqual((chunks / "notes.txt").read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
