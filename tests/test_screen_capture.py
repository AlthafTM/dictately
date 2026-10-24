from _support import ROOT
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from screen_capture import screenshot_command,screenshot_candidate,capture_command
from live_worker import Worker

class ScreenshotTests(unittest.TestCase):
    def test_commands_are_whole_phrases_in_both_languages(self):
        for text in ('Screenshot!', 'screen shot', 'SS', 'S. S.'):
            self.assertTrue(screenshot_command(text),text)
        for text in ('send me a screenshot', 'how do I take a screenshot', 'screenshot of the problem', 'say screenshot', '', 'shoot the page later', 'please screenshot', 'tolong screenshot', 'ambil screenshot', 'screenshot ss', 'shot this page', 'take a screenshot', 'screenshot sekarang'):
            self.assertFalse(screenshot_command(text),text)
        self.assertTrue(screenshot_candidate('screen'))
        self.assertFalse(screenshot_candidate('take a walk'))
    def test_command_prefix_never_types_and_ordinary_continuation_is_kept(self):
        outputs=iter(('screen','screenshot','screenshot of the problem'))
        def decode(audio,**options):
            text=next(outputs)
            return iter([SimpleNamespace(text=text,words=[])]),SimpleNamespace(language='en')
        worker=Worker();worker.prepare=lambda:None;worker.engine.whisper=SimpleNamespace(transcribe=decode)
        base={'command':'recognize','id':1,'samples':[1000]*32000,'language':'en','cleanup':False}
        first=worker.process(base)
        self.assertEqual(first['raw'],'');self.assertEqual(first['consumed'],0)
        second=worker.process(base)
        self.assertEqual(second['raw'],'');self.assertNotIn('action',second)
        final=worker.process({**base,'final':True})
        self.assertNotIn('action',final);self.assertEqual(final['cleaned'],'screenshot of the problem')
    def test_stopped_command_routes_without_dictation_and_consumes_audio(self):
        worker=Worker();worker.prepare=lambda:None
        worker.engine.whisper=SimpleNamespace(transcribe=lambda *a,**k:(iter([SimpleNamespace(text='Screenshot.',words=[])]),None))
        result=worker.process({'command':'recognize','id':7,'samples':[1000]*32000,'language':'en','final':True})
        self.assertEqual(result['action'],'screenshot');self.assertEqual(result['raw'],'');self.assertEqual(result['cleaned'],'')
        self.assertEqual(result['consumed'],32000);self.assertTrue(result['flushed'])
        self.assertEqual(worker.committed_words,[]);self.assertEqual(worker.previous_words,[])
    def test_extra_words_never_capture_in_either_selected_language(self):
        for language in ('en','id'):
            for text in ('take a screenshot','please screenshot','screenshot now','send screenshot','screenshot ini','tolong screenshot','screenshot halaman ini','shot this page'):
                with self.subTest(language=language,text=text):
                    worker=Worker();worker.prepare=lambda:None
                    worker.engine.whisper=SimpleNamespace(transcribe=lambda *a,**k:(iter([SimpleNamespace(text=text,words=[])]),None))
                    result=worker.process({'command':'recognize','id':1,'samples':[1000]*32000,'language':language,'final':True,'cleanup':False})
                    self.assertNotIn('action',result);self.assertEqual(result['cleaned'],text)
    def test_short_pause_does_not_capture_and_following_words_are_dictated(self):
        for language in ('en','id'):
            for trailing in ('of the problem','halaman ini'):
                outputs=iter(('Screenshot.', 'Screenshot '+trailing))
                worker=Worker();worker.prepare=lambda:None
                worker.engine.whisper=SimpleNamespace(transcribe=lambda *a,**k:(iter([SimpleNamespace(text=next(outputs),words=[])]),None))
                request={'command':'recognize','id':1,'samples':[1000]*32000,'language':language,'cleanup':False}
                held=worker.process({**request,'flush':True,'at_pause':True,'pause_seconds':0.2})
                self.assertNotIn('action',held);self.assertEqual(held['consumed'],0);self.assertEqual(held['raw'],'')
                result=worker.process({**request,'final':True})
                self.assertNotIn('action',result);self.assertEqual(result['cleaned'],'Screenshot '+trailing)
    def test_quarter_second_pause_captures_without_stopping_and_repeats(self):
        for language in ('en','id'):
            worker=Worker();worker.prepare=lambda:None
            worker.engine.whisper=SimpleNamespace(transcribe=lambda *a,**k:(iter([SimpleNamespace(text='screenshot',words=[])]),None))
            request={'command':'recognize','id':1,'samples':[1000]*32000,'language':language}
            for _ in range(2):
                held=worker.process({**request,'pause_seconds':0.249})
                self.assertNotIn('action',held);self.assertEqual(held['raw'],'')
                result=worker.process({**request,'pause_seconds':0.25})
                self.assertEqual(result.get('action'),'screenshot');self.assertEqual(result['consumed'],32000)
                self.assertFalse(worker.session_dictated)
    def test_recognized_word_end_overrides_noise_based_pause(self):
        for language in ('en','id'):
            worker=Worker();worker.prepare=lambda:None
            end=[1.76]
            def decode(*args,**kwargs):
                return iter([SimpleNamespace(text='screenshot',words=[SimpleNamespace(word=' screenshot',start=1.,end=end[0],probability=.95)])]),None
            worker.engine.whisper=SimpleNamespace(transcribe=decode)
            request={'command':'recognize','id':1,'samples':[1000]*32000,'language':language,'pause_seconds':0.0}
            self.assertNotIn('action',worker.process(request),'Do not capture 240 ms after the recognized word')
            end[0]=1.75
            self.assertEqual(worker.process(request).get('action'),'screenshot','Ongoing audio energy must not reset a recognized word pause')
            worker=Worker();worker.prepare=lambda:None;worker.engine.whisper=SimpleNamespace(transcribe=decode)
            end[0]=1.9
            self.assertNotIn('action',worker.process({**request,'pause_seconds':5.0}),'Word timing must also prevent an acoustic-pause false positive')
    def test_command_is_language_independent_but_not_after_prior_dictation(self):
        for language in ('en','id'):
            for text in ('screenshot','screen shot','SS','S. S.'):
                worker=Worker();worker.prepare=lambda:None
                worker.engine.whisper=SimpleNamespace(transcribe=lambda *a,**k:(iter([SimpleNamespace(text=text,words=[])]),None))
                request={'command':'recognize','id':1,'samples':[1000]*32000,'language':language,'final':True}
                self.assertEqual(worker.process(request).get('action'),'screenshot')
            outputs=iter(('A normal sentence','screenshot'))
            worker=Worker();worker.prepare=lambda:None
            worker.engine.whisper=SimpleNamespace(transcribe=lambda *a,**k:(iter([SimpleNamespace(text=next(outputs),words=[])]),None))
            request={'command':'recognize','id':1,'samples':[1000]*32000,'language':language,'flush':True,'cleanup':False}
            self.assertEqual(worker.process(request)['raw'],'A normal sentence')
            self.assertNotIn('action',worker.process({**request,'final':True}))
    def test_native_previous_text_guard_covers_restarted_worker(self):
        worker=Worker();worker.prepare=lambda:None
        worker.engine.whisper=SimpleNamespace(transcribe=lambda *a,**k:(iter([SimpleNamespace(text='screenshot',words=[])]),None))
        result=worker.process({'command':'recognize','id':1,'samples':[1000]*32000,'language':'id','final':True,'previous_text':'Text already dictated'})
        self.assertNotIn('action',result);self.assertEqual(result['raw'],'screenshot')
    def test_platform_launchers_use_native_region_interfaces(self):
        self.assertEqual(capture_command('win32')[-1],'ms-screenclip:')
        with TemporaryDirectory() as folder:
            command=capture_command('darwin',home=folder)
            self.assertEqual(command[:3],['/usr/sbin/screencapture','-i','-x'])
            self.assertEqual(Path(command[3]).parent,Path(folder)/'Pictures/Dictately')
        available=lambda name:'/usr/bin/'+name if name in ('spectacle','gnome-screenshot') else None
        self.assertEqual(capture_command('linux',which=available,desktop='KDE'),['/usr/bin/spectacle','--region'])
        self.assertEqual(capture_command('linux',which=available,desktop='GNOME'),['/usr/bin/gnome-screenshot','--area','--interactive'])
        with self.assertRaisesRegex(RuntimeError,'No region screenshot tool'):
            capture_command('linux',which=lambda name:None)

if __name__=='__main__':unittest.main()
