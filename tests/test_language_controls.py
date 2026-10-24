from _support import ROOT
import unittest
from types import SimpleNamespace
from language_commands import language_command
from hotkeys import valid_shortcut, shortcut_name, LANGUAGE_DEFAULT
from live_worker import Worker, locked_language
from language_commands import shared_encoder
import numpy as np


class LanguageControlTests(unittest.TestCase):
    def test_silence_does_not_load_a_speech_model(self):
        worker=Worker()
        worker.prepare=lambda:self.fail('Silent requests must not load model weights')
        for command in ('recognize','select_language'):
            result=worker.process({'command':command,'id':1,'samples':[0]*32000,'final':True})
            self.assertEqual(result.get('cleaned',''),'')
            self.assertIsNone(result.get('language') if command=='select_language' else None)

    def test_encoder_reuse_is_exact_bounded_and_scoped_to_one_request(self):
        class Model:
            def __init__(self):self.calls=0
            def encode(self,features):self.calls+=1;return object()
        model=Model();features=np.ones((4,5),dtype=np.float32)
        with shared_encoder(model):
            first=model.encode(features)
            self.assertIs(model.encode(features.copy()),first)
            self.assertEqual(model.calls,1)
            model.encode(features+1)
            model.encode(np.ones((5,4),dtype=np.float32))
            model.encode(np.ones((5,4),dtype=np.float32))
            self.assertEqual(model.calls,4)  # Only two distinct inputs retained.
        self.assertNotIn('encode',vars(model))
        self.assertIsNot(model.encode(features),first)
        with self.assertRaisesRegex(RuntimeError,'test failure'):
            with shared_encoder(model):raise RuntimeError('test failure')
        self.assertNotIn('encode',vars(model))

    def test_picker_accepts_only_short_explicit_language_commands(self):
        for phrase,expected in [('English','en'),('bahasa Inggris','en'),('Indonesia.','id'),('Indonesian','id'),('both',None),('keduanya',None),('Indonesia dan English',None),('choose both English and Indonesian',None)]:
            self.assertEqual(language_command(phrase),expected)
        for phrase in ['Please send my English report','English is important','hello','', 'I live in Indonesia']:
            self.assertIsNone(language_command(phrase))

    def test_legacy_both_falls_back_to_locked_english(self):
        calls=[]
        def decode(audio,**options):calls.append(options);return iter([]),SimpleNamespace(language='id')
        worker=Worker();worker.prepare=lambda:None;worker.engine.whisper=SimpleNamespace(transcribe=decode)
        result=worker.process({'command':'recognize','id':1,'samples':[1500]*32000,'language':'both','final':True})
        self.assertNotIn('multilingual',calls[0]);self.assertEqual(calls[0]['language'],'en')
        self.assertEqual(result['language'],'en');self.assertEqual(locked_language('both'),'en')

    def test_picker_results_are_control_messages_without_typable_text(self):
        worker=Worker();worker.prepare=lambda:None
        worker.engine.whisper=SimpleNamespace(transcribe=lambda *args,**kwargs:(iter([SimpleNamespace(text='Indonesia')]),None))
        result=worker.process({'command':'select_language','id':4,'samples':[1500]*32000})
        self.assertEqual(result['type'],'language_selected');self.assertEqual(result['language'],'id')
        self.assertNotIn('cleaned',result);self.assertNotIn('raw',result)

    def test_clear_voice_selection_uses_one_decode_and_unclear_selection_retries(self):
        calls=[]
        def decode(audio,**options):
            calls.append(options['language'])
            return iter([SimpleNamespace(text='Indonesia' if options['language']=='id' else 'not a command')]),None
        worker=Worker();worker.prepare=lambda:None;worker.engine.whisper=SimpleNamespace(transcribe=decode)
        result=worker.process({'command':'select_language','id':1,'language':'id','samples':[1500]*32000})
        self.assertEqual(result['language'],'id');self.assertEqual(calls,['id'])
        calls.clear()
        result=worker.process({'command':'select_language','id':2,'language':'en','samples':[1500]*32000})
        self.assertEqual(result['language'],'id');self.assertEqual(calls,['en','id'])

    def test_shortcuts_support_modifiers_but_reject_bare_modifiers(self):
        self.assertTrue(valid_shortcut(123));self.assertTrue(valid_shortcut(0x341))
        self.assertEqual(shortcut_name(0x341),'Ctrl+Shift+A')
        self.assertEqual(shortcut_name(122),'F11')
        self.assertEqual(shortcut_name(LANGUAGE_DEFAULT),'F10')
        for key in [0,27,16,162,4096,None,'F12']:
            self.assertFalse(valid_shortcut(key),key)


if __name__=='__main__':unittest.main()
