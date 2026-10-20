from _support import ROOT
"""Exercise asynchronous desktop capture/finalization with synthetic audio only."""
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch



def main():
    from PySide6.QtCore import QTimer
    import desktop_app
    results=[];failures=[]
    class Stream:
        def __init__(self,**kwargs):self.callback=kwargs['callback']
        def start(self):
            time.sleep(.18)  # A slow audio driver must not freeze the popup.
            self.callback(b'\x10\x04'*48000,48000,None,False)
        def stop(self):
            time.sleep(.08)
            self.callback(b'\x10\x04'*1600,1600,None,False)
        def close(self):pass
    class Input:
        def destination(self):return 'synthetic field'
        def paste(self,target,text):results.append(text)
    class Speech:
        def __init__(self,*args,**kwargs):pass
        def recognize(self,token,request):
            time.sleep(.04)
            return {'type':'result','cleaned':'Synthetic phrase' if not request['final'] else 'tail', 'consumed':len(request['samples'])}
    def probe(controller,app):
        controller.input=Input()
        controller.toggle()
        def check_open():
            try:
                assert controller.starting,'capture should still be opening'
                assert controller.window.progress>0,'animation did not advance during device opening'
            except Exception as error:failures.append(error)
        def stop():
            try:
                assert controller.recording
                assert results==['Synthetic phrase'],results
                controller.toggle()
            except Exception as error:failures.append(error);app.quit()
        def finish():
            try:
                assert not controller.finishing,'finalization did not complete'
                assert results==['Synthetic phrase',' tail'],results
                assert controller.history[0]['text']=='Synthetic phrase tail',controller.history
                print('Asynchronous open, animation, live paste, microphone tail and final history passed')
            except Exception as error:failures.append(error)
            finally:controller.quit()
        QTimer.singleShot(80,check_open)
        QTimer.singleShot(650,stop)
        QTimer.singleShot(1200,finish)
        QTimer.singleShot(10000,app.quit)
    with tempfile.TemporaryDirectory() as temporary,patch.dict(os.environ,{'LOCALFLOW_DATA':temporary,'QT_QPA_PLATFORM':'offscreen'}),patch('sounddevice.RawInputStream',Stream),patch.object(desktop_app,'SpeechSession',Speech):
        code=desktop_app.run_desktop(smoke=True,probe=probe)
    assert code==0,code
    if failures:raise failures[0]
    assert results==['Synthetic phrase',' tail'],results


if __name__=='__main__':main()
