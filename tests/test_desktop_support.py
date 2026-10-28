from _support import ROOT
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import desktop_support
from desktop_app import PhraseBuffer, SpeechSession
from runtime_paths import user_data_dir


class DesktopTests(unittest.TestCase):
    def test_new_recording_resets_agreement_without_reloading_model(self):
        calls=[];created=[]
        class Worker:
            def __init__(self,**kwargs):created.append(kwargs)
            def process(self,request):calls.append(request['command']);return {'type':'result'}
        speech=SpeechSession(Path('model-storage'),factory=Worker)
        for token in (1,1,2):speech.recognize(token,{'command':'recognize'})
        self.assertEqual(len(created),1)
        self.assertEqual(calls,['prepare','recognize','recognize','prepare','recognize'])
    def test_decode_consumption_keeps_new_microphone_audio(self):
        buffer=PhraseBuffer();buffer.append(b'\x01\x00'*32000)
        self.assertEqual(len(buffer.snapshot()),64000)
        buffer.append(b'\x02\x00'*1600)
        buffer.consume(32000)
        self.assertEqual(buffer.samples,b'\x02\x00'*1600)
        with self.assertRaises(ValueError):buffer.consume(32001)

    def test_linux_refuses_wayland_even_with_xwayland_display(self):
        with patch('sys.platform','linux'),patch.dict(os.environ,{'DISPLAY':':0','WAYLAND_DISPLAY':'wayland-0'}):
            with self.assertRaisesRegex(RuntimeError,'Wayland'):desktop_support.check_platform()

    def test_platform_storage_is_not_a_windows_environment_default(self):
        with TemporaryDirectory() as directory, patch.object(Path,'home',return_value=Path(directory)):
            home=Path(directory)
            with patch.dict(os.environ,clear=True),patch('sys.platform','darwin'):
                self.assertEqual(user_data_dir(),home/'Library/Application Support/Dictately')
            with patch.dict(os.environ,{'XDG_DATA_HOME':str(home/'xdg')},clear=True),patch('sys.platform','linux'):
                self.assertEqual(user_data_dir(),home/'xdg/dictately')

    def test_launcher_quotes_paths_and_handles_desktop_field_codes(self):
        with TemporaryDirectory(prefix='Dictately space ') as temporary,patch('sys.platform','linux'):
            root=Path(temporary)
            launcher=desktop_support.install_launchers(root,root/'venv/bin/python',True,home=root/'home')
            text=launcher.read_text()
            self.assertIn('Terminal=false',text)
            self.assertIn('Exec="',text)
            self.assertIn('desktop_app.py"',text)
            self.assertEqual(text,(root/'home/.config/autostart/dictately.desktop').read_text())
        self.assertIn('%%',desktop_support.desktop_entry(['/tmp/100%/python'], '/tmp/icon.png'))

    def test_input_refuses_focus_changes_before_touching_clipboard(self):
        backend=object.__new__(desktop_support.DesktopInput)
        with patch.object(backend,'matches',return_value=False),patch('pyperclip.copy') as copy:
            with self.assertRaisesRegex(RuntimeError,'focus changed'):backend.paste(None,'private text')
            copy.assert_not_called()


if __name__=='__main__':unittest.main()
