from _support import ROOT
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, MagicMock
from speech_models import selected_model
import select_model


class ModelSelectionTests(unittest.TestCase):
    def test_optional_download_is_atomic_and_records_revision(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            selection = root / "models/selected-model.json"
            selection.parent.mkdir()
            selection.write_text('{"model":"turbo"}')
            hub = MagicMock()
            hub.HfApi.return_value.model_info.return_value.sha = "specific-revision"
            hub.snapshot_download.side_effect = RuntimeError("Download interrupted")
            with patch.object(select_model, "ROOT", root), patch("sys.argv", ["app/select_model.py", "large-v3"]), patch.dict("sys.modules", {"huggingface_hub": hub}):
                with self.assertRaises(RuntimeError):
                    select_model.main()
                self.assertEqual(json.loads(selection.read_text())["model"], "turbo")

                def download(**options):
                    self.assertEqual(options["revision"], "specific-revision")
                    options["local_dir"].mkdir()
                    self.assertIn('vocabulary.txt',options['allow_patterns'])
                    for filename in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
                        (options["local_dir"] / filename).touch()
                hub.snapshot_download.side_effect = download
                select_model.main()
                self.assertEqual(selected_model(root), "whisper-large-v3")
                source = json.loads((root / "models/whisper-large-v3/source.json").read_text())
                self.assertEqual(source["revision"], "specific-revision")

    def test_default_and_explicit_selection(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(selected_model(root), "whisper-small")
            model = root / "models/whisper-turbo/model.bin"
            model.parent.mkdir(parents=True)
            model.touch()
            self.assertEqual(selected_model(root), "whisper-turbo")
            selection = root / "models/selected-model.json"
            selection.write_text(json.dumps({"model": "large-v3"}))
            with self.assertRaises(FileNotFoundError):
                selected_model(root)
            large = root / "models/whisper-large-v3/model.bin"
            large.parent.mkdir()
            large.touch()
            self.assertEqual(selected_model(root), "whisper-large-v3")
            selection.write_text(json.dumps({"model": "../outside"}))
            with self.assertRaises(ValueError):
                selected_model(root)

    def test_indonesian_subfolder_model_and_per_language_routing(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            turbo = root / "models/whisper-turbo/model.bin"
            turbo.parent.mkdir(parents=True)
            turbo.touch()
            selection = root / "models/selected-model.json"
            selection.write_text(json.dumps({"model": "turbo"}))
            hub = MagicMock()
            hub.HfApi.return_value.model_info.return_value.sha = "maleo-rev"

            def download(**options):
                self.assertEqual(options["repo_id"], "maleo-ai/whisper-large-v3-turbo-id")
                self.assertIn("ct2-int8/model.bin", options["allow_patterns"])
                nested = options["local_dir"] / "ct2-int8"
                nested.mkdir(parents=True)
                for filename in ("model.bin", "config.json", "tokenizer.json", "vocabulary.json"):
                    (nested / filename).touch()

            hub.snapshot_download.side_effect = download
            with patch.object(select_model, "ROOT", root), patch("sys.argv", ["app/select_model.py", "turbo-id", "--for-indonesian"]), patch.dict("sys.modules", {"huggingface_hub": hub}):
                select_model.main()

            self.assertEqual(selected_model(root, language="en"), "whisper-turbo")
            self.assertEqual(selected_model(root, language="id"), "whisper-turbo-id")
            source = json.loads((root / "models/whisper-turbo-id/source.json").read_text())
            self.assertEqual(source["subfolder"], "ct2-int8")
            self.assertEqual(source["revision"], "maleo-rev")

            with patch.object(select_model, "ROOT", root), patch("sys.argv", ["app/select_model.py", "none", "--for-indonesian"]):
                select_model.main()
            self.assertEqual(selected_model(root, language="id"), "whisper-turbo")


if __name__ == "__main__":
    unittest.main()
