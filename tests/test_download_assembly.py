"""Offline model-assembly and resumable range-download checks."""
from _support import ROOT
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import download_turbo


class DownloadAssemblyTests(unittest.TestCase):
    def test_streaming_assembly_verifies_before_replacing_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);first=root/'first';second=root/'second';target=root/'model.bin'
            first.write_bytes(b'abcd');second.write_bytes(b'efgh');target.write_bytes(b'keep existing')
            with self.assertRaisesRegex(ValueError,'checksum'):
                download_turbo.assemble_files([first,second],target,8,'incorrect')
            self.assertEqual(target.read_bytes(),b'keep existing')
            with self.assertRaisesRegex(ValueError,'Incomplete'):
                download_turbo.assemble_files([first],target,8)
            with self.assertRaises(FileNotFoundError):
                download_turbo.assemble_files([first,root/'missing'],target,8)
            self.assertEqual(target.read_bytes(),b'keep existing')
            expected=hashlib.sha256(b'abcdefgh').hexdigest()
            self.assertEqual(download_turbo.assemble_files([first,second],target,8,expected),expected)
            self.assertEqual(target.read_bytes(),b'abcdefgh')
            self.assertFalse(list(root.glob('*.assembling')))

    def test_range_download_sessions_assembly_manifest_and_cached_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'assets').mkdir();destination=root/'models/whisper-turbo'
            data=b'abcdefgh';expected=hashlib.sha256(data).hexdigest()
            (root/'assets/speech-model.json').write_text(json.dumps([{'file':'model.bin','bytes':8,'revision':'pinned-test','sha256':expected}]),encoding='utf-8')
            response=Mock(status_code=206,headers={'Content-Range':'bytes 0-7/8'},content=data)
            response.raw.read.return_value=b'badbytes'
            session=Mock();session.get.return_value=response
            with patch.object(download_turbo,'ROOT',root),patch.object(download_turbo,'DEST',destination),patch.object(download_turbo.requests,'Session',return_value=session) as factory:
                with self.assertRaisesRegex(ValueError,'checksum'):download_turbo.main()
                self.assertFalse((destination/'model.bin').exists())
                self.assertFalse((destination/'download-parts').exists())
                response.raw.read.return_value=data
                download_turbo.main()
                self.assertEqual(factory.call_count,2);self.assertEqual(session.close.call_count,2)
                self.assertEqual(session.get.call_args.kwargs['headers'],{'Range':'bytes=0-7'})
                self.assertTrue(session.get.call_args.kwargs['stream'])
                response.raw.read.assert_called_with(9,decode_content=True)
                self.assertEqual((destination/'model.bin').read_bytes(),data)
                self.assertEqual(json.loads((destination/'manifest.json').read_text(encoding='utf-8'))[0]['sha256'],expected)
                self.assertFalse((destination/'download-parts').exists())
                download_turbo.main()
                self.assertEqual(factory.call_count,2,'A verified cached model must not be downloaded again')
