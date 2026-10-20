"""Hosted native checks; no personal apps, microphone, or permission changes."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def x11_routing():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QLineEdit
    from desktop_support import DesktopInput
    from desktop_app import start_hotkey
    import threading
    app=QApplication([])
    first=QLineEdit();second=QLineEdit()
    first.setWindowTitle('Dictately synthetic destination A');second.setWindowTitle('Dictately synthetic destination B')
    first.show();second.show()
    backend=DesktopInput();state={};failures=[];toggled=threading.Event()
    hooks=start_hotkey(toggled.set,lambda:None)
    def activate(widget):
        subprocess.run(['xdotool','windowactivate','--sync',str(int(widget.winId()))],check=True,timeout=5)
        widget.setFocus();app.processEvents()
    def step_one():
        try:
            activate(first);state['target']=backend.destination()
            backend.paste(state['target'],'Dictately native typing check')
            subprocess.run(['xdotool','key','F12'],check=True,timeout=5)
            QTimer.singleShot(600,step_two)
        except Exception as error:failures.append(error);app.quit()
    def step_two():
        try:
            assert first.text()=='Dictately native typing check',repr(first.text())
            assert toggled.is_set(),'F12 was not received'
            activate(second)
            try:backend.paste(state['target'],'must never appear')
            except RuntimeError:pass
            else:raise AssertionError('Changed-window paste was not blocked')
            assert second.text()==''
            print('X11 native paste, focus rejection and F12 passed',flush=True)
        except Exception as error:failures.append(error)
        finally:app.quit()
    QTimer.singleShot(500,step_one);QTimer.singleShot(15000,app.quit);app.exec()
    for hook in hooks:
        if hasattr(hook,'stop'):hook.stop()
    if failures:raise failures[0]
    assert toggled.is_set()


def main():
    if '--x11' in sys.argv:
        x11_routing();return
    if sys.platform=='darwin':
        import AppKit
        import ApplicationServices as ax
        import Quartz
        assert callable(ax.AXUIElementIsAttributeSettable)
        assert callable(Quartz.CGPreflightListenEventAccess)
        assert AppKit.NSWorkspace.sharedWorkspace() is not None
    with tempfile.TemporaryDirectory() as temporary:
        image=ROOT/'qa/desktop-waveform.png';image.parent.mkdir(exist_ok=True)
        env=dict(os.environ,LOCALFLOW_DATA=temporary,QT_QPA_PLATFORM='offscreen',DICTATELY_SMOKE_IMAGE=str(image))
        subprocess.run([sys.executable,str(ROOT/'desktop_app.py'),'--smoke-test'],env=env,check=True,timeout=30)
        assert image.is_file()
        subprocess.run([sys.executable,str(ROOT/'tests/check_desktop_lifecycle.py')],env=env,check=True,timeout=30)
        if sys.platform=='linux':
            env.pop('QT_QPA_PLATFORM',None);env.pop('WAYLAND_DISPLAY',None);env['XDG_SESSION_TYPE']='x11'
            subprocess.run(['xvfb-run','-a','sh','-c','openbox >/dev/null 2>&1 & exec "$@"','--',sys.executable,__file__,'--x11'],env=env,check=True,timeout=30)
        from desktop_support import install_launchers
        launcher=install_launchers(ROOT,sys.executable,startup=True,home=Path(temporary))
        assert launcher.exists()
        print('Native launcher generation and opening/closing waveform smoke passed')
        # A real multilingual model load and synthetic English decode on each CPU.
        # This is not a real-microphone accuracy or dictation-permission test.
        os.environ['LOCALFLOW_DATA']=temporary
        from desktop_app import install_model
        install_model('small')
        speech=Path(temporary)/'sample.wav'
        phrase='Please send the report tomorrow morning'
        if sys.platform=='darwin':
            subprocess.run(['say','-o',str(speech),'--file-format=WAVE','--data-format=LEI16@16000',phrase],check=True)
        else:
            subprocess.run(['espeak','-w',str(speech),phrase],check=True)
        from faster_whisper.audio import decode_audio
        import numpy as np
        from live_worker import Worker
        worker=Worker(model_root=Path(temporary))
        samples=(np.clip(decode_audio(str(speech),sampling_rate=16000),-1,1)*32767).astype(np.int16)
        result=worker.process({'command':'recognize','id':1,'samples':samples,'language':'en','final':True})
        worker.engine.close()
        print('CPU recognition:',result['cleaned'])
        words=result['cleaned'].lower()
        assert 'report' in words and 'tomorrow' in words,result
        assert result['language']=='en'
        result=worker.process({'command':'recognize','id':2,'samples':samples,'language':'both','final':True})
        assert result['language'] in ('en','id')
        assert 'report' in result['cleaned'].lower() and 'tomorrow' in result['cleaned'].lower(),result
        print('Real local CPU model load and synthetic speech decode passed')


if __name__=='__main__':main()
