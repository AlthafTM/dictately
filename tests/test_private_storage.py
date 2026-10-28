from _support import ROOT
import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from runtime_paths import private_data_dir, user_data_dir, write_private_json


class PrivateStorageTests(unittest.TestCase):
    def test_failed_json_write_preserves_history_and_cleans_temporary(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'history.json'
            write_private_json(path, {'text': 'original'})
            with patch('runtime_paths.json.dump', side_effect=ValueError('invalid data')):
                with self.assertRaises(ValueError):
                    write_private_json(path, {})
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'text': 'original'})
            self.assertEqual(list(path.parent.glob('*.tmp')), [])

    def test_writer_does_not_reuse_predictable_temporary(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'desktop-history.json'
            planted = path.with_suffix('.tmp')
            planted.write_text('leave alone', encoding='utf-8')
            write_private_json(path, {'text': 'bahasa Indonesia'})
            self.assertEqual(planted.read_text(encoding='utf-8'), 'leave alone')
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['text'], 'bahasa Indonesia')

    @unittest.skipIf(os.name == 'nt', 'Requires real POSIX permission semantics')
    def test_new_and_existing_history_are_private_under_permissive_umask(self):
        with TemporaryDirectory() as directory:
            old_umask = os.umask(0o022)
            try:
                data = Path(directory) / 'Dictately'
                data.mkdir(mode=0o755)
                history = data / 'desktop-history.json'
                history.write_text('[]', encoding='utf-8')
                history.chmod(0o644)
                private_data_dir(data)
                self.assertEqual(stat.S_IMODE(data.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(history.stat().st_mode), 0o600)
                write_private_json(history, [{'text': 'private'}])
                self.assertEqual(stat.S_IMODE(history.stat().st_mode), 0o600)
                new_data = private_data_dir(Path(directory) / 'fresh')
                self.assertEqual(stat.S_IMODE(new_data.stat().st_mode), 0o700)
            finally:
                os.umask(old_umask)

    @unittest.skipIf(os.name == 'nt', 'Requires POSIX no-follow opens')
    def test_state_symlinks_do_not_modify_their_targets(self):
        with TemporaryDirectory() as directory:
            outside = Path(directory) / 'outside.json'
            outside.write_text('unchanged', encoding='utf-8')
            data = private_data_dir(Path(directory) / 'Dictately')
            (data / 'desktop-history.json').symlink_to(outside)
            with self.assertRaises(OSError):
                write_private_json(data / 'desktop-history.json', [])
            self.assertEqual(outside.read_text(encoding='utf-8'), 'unchanged')
            linked = Path(directory) / 'linked'
            linked.symlink_to(data, target_is_directory=True)
            with self.assertRaises(OSError):
                private_data_dir(linked)

    @unittest.skipIf(os.name == 'nt', 'Requires real POSIX permission semantics')
    def test_migration_protects_data_before_copying_history(self):
        with TemporaryDirectory() as directory:
            parent = Path(directory)
            old = parent / 'transcribr'
            old.mkdir()
            (old / 'history.json').write_text('[{"text":"keep"}]', encoding='utf-8')
            with patch.dict(os.environ, {'XDG_DATA_HOME': str(parent)}, clear=True), patch('sys.platform', 'linux'):
                data = user_data_dir()
            self.assertEqual(stat.S_IMODE(data.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((data / 'history.json').stat().st_mode), 0o600)
            self.assertTrue((old / 'history.json').exists())
