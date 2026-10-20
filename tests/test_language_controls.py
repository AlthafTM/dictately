from _support import ROOT
import unittest
from types import SimpleNamespace
from language_commands import language_command, BilingualBackend
from hotkeys import valid_shortcut, shortcut_name, LANGUAGE_DEFAULT
from live_worker import Worker, locked_language


class LanguageControlTests(unittest.TestCase):
    def test_picker_accepts_only_short_explicit_language_commands(self):
        for phrase,expected in [('English','en'),('bahasa Inggris','en'),('Indonesia.','id'),('Indonesian','id'),('both','both'),('keduanya','both'),('Indonesia dan English','both'),('choose both English and Indonesian','both')]:
            self.assertEqual(language_command(phrase),expected)
        for phrase in ['Please send my English report','English is important','hello','', 'I live in Indonesia']:
            self.assertIsNone(language_command(phrase))

    def test_per_segment_detector_cannot_select_a_third_language(self):
        backend=SimpleNamespace(detect_language=lambda *args,**kwargs:[
            [('<|fr|>',.95),('<|id|>',.04),('<|en|>',.01)],
            [('<|ja|>',.8),('<|en|>',.2),('<|id|>',0)]])
        result=BilingualBackend(backend).detect_language(None)
        self.assertEqual(result[0][0][0],'<|id|>')
        for row in result:
            self.assertEqual({token for token,_ in row},{'<|en|>','<|id|>'})
            self.assertAlmostEqual(sum(probability for _,probability in row),1.)

    def test_both_enables_filtered_multilingual_recognition(self):
        calls=[]
        def decode(audio,**options):calls.append(options);return iter([]),SimpleNamespace(language='id')
        worker=Worker();worker.prepare=lambda:None;worker.engine.whisper=SimpleNamespace(transcribe=decode)
        result=worker.process({'command':'recognize','id':1,'samples':[1500]*32000,'language':'both','final':True})
        self.assertTrue(calls[0]['multilingual']);self.assertIsNone(calls[0]['language'])
        self.assertEqual(result['language'],'id');self.assertEqual(locked_language('both'),'both')

    def test_picker_results_are_control_messages_without_typable_text(self):
        worker=Worker();worker.prepare=lambda:None
        worker.engine.whisper=SimpleNamespace(transcribe=lambda *args,**kwargs:(iter([SimpleNamespace(text='Indonesia')]),None))
        result=worker.process({'command':'select_language','id':4,'samples':[1500]*32000})
        self.assertEqual(result['type'],'language_selected');self.assertEqual(result['language'],'id')
        self.assertNotIn('cleaned',result);self.assertNotIn('raw',result)

    def test_shortcuts_support_modifiers_but_reject_bare_modifiers(self):
        self.assertTrue(valid_shortcut(123));self.assertTrue(valid_shortcut(0x341))
        self.assertEqual(shortcut_name(0x341),'Ctrl+Shift+A')
        self.assertEqual(shortcut_name(122),'F11')
        self.assertEqual(shortcut_name(LANGUAGE_DEFAULT),'F10')
        for key in [0,27,16,162,4096,None,'F12']:
            self.assertFalse(valid_shortcut(key),key)


if __name__=='__main__':unittest.main()
