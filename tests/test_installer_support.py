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
            os.environ.pop("DICTATELY_ROOT", None)
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(root / "runtime/worker/DictatelyWorker.exe")):
                self.assertEqual(runtime_paths.app_root(), root)
                os.environ["DICTATELY_ROOT"] = str(root / "override")
                self.assertEqual(runtime_paths.app_root(), root / "override")

    def test_rename_migrates_history_without_overwriting_new_preferences(self):
        with TemporaryDirectory() as temporary, patch.dict(os.environ, {"LOCALAPPDATA":temporary},clear=True), patch('sys.platform','win32'):
            parent=Path(temporary);old=parent/'Transcribr';old.mkdir()
            (old/'settings.json').write_text('{"language":"id"}')
            (old/'history.json').write_text('[{"raw":"keep this"}]')
            new=parent/'Dictately';new.mkdir()
            (new/'settings.json').write_text('{"language":"both"}')
            self.assertEqual(runtime_paths.user_data_dir(),new)
            self.assertEqual(json.loads((new/'settings.json').read_text())['language'],'both')
            self.assertEqual((new/'history.json').read_bytes(),(old/'history.json').read_bytes())
            self.assertTrue((old/'settings.json').exists())

    def test_legacy_default_changes_to_f10_without_stealing_a_custom_key(self):
        for dictation,language,expected in [(123,122,121),(121,122,122),(123,888,888)]:
            with self.subTest(dictation=dictation,language=language), TemporaryDirectory() as temporary, patch.dict(os.environ,{"LOCALAPPDATA":temporary},clear=True), patch('sys.platform','win32'):
                old=Path(temporary)/'Transcribr';old.mkdir()
                (old/'settings.json').write_text(json.dumps({'dictation_hotkey':dictation,'language_hotkey':language}))
                new=runtime_paths.user_data_dir()
                self.assertEqual(json.loads((new/'settings.json').read_text())['language_hotkey'],expected)
                self.assertEqual(json.loads((old/'settings.json').read_text())['language_hotkey'],language)

    def test_explicit_data_directory_never_imports_personal_history(self):
        with TemporaryDirectory() as temporary, patch.dict(os.environ,clear=True), patch('sys.platform','win32'):
            parent=Path(temporary);old=parent/'Transcribr';old.mkdir()
            (old/'history.json').write_text('[{"raw":"private"}]')
            os.environ.update(LOCALAPPDATA=temporary,DICTATELY_DATA=str(parent/'isolated'))
            self.assertEqual(runtime_paths.user_data_dir(),parent/'isolated')
            self.assertFalse((parent/'isolated/history.json').exists())

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
