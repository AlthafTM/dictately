"""Compact language picker: native input, real local voice, and no mic capture."""
from _support import ROOT
from windows_controls import u,pump,chord
import ctypes as c
from ctypes import wintypes as w
import json,os,subprocess,time,uuid,wave
import numpy as np

def main():
    u.SetProcessDpiAwarenessContext.argtypes=[c.c_void_p];u.SetProcessDpiAwarenessContext(c.c_void_p(-4))
    base=ROOT/'qa'/('compact-picker-'+uuid.uuid4().hex[:8]);base.mkdir(parents=True)
    with wave.open(str(ROOT/'qa/picker-indonesia.wav'),'rb') as sound:
        rate=sound.getframerate();channels=sound.getnchannels();assert sound.getsampwidth()==2
        voice=np.frombuffer(sound.readframes(sound.getnframes()),dtype='<i2').reshape(-1,channels).mean(axis=1)
    voice=np.interp(np.arange(int(len(voice)*16000/rate))*rate/16000,np.arange(len(voice)),voice).astype('<i2')
    pcm=base/'voice.pcm';pcm.write_bytes(bytes(32000*3)+voice.tobytes()+bytes(32000*38))
    (base/'settings.json').write_text(json.dumps({'language':'both'}),encoding='utf-8')
    before=u.GetForegroundWindow()
    window=u.CreateWindowExW(0,'STATIC','Dictately compact language check',0x10CF0000,100,100,600,300,None,None,None,None)
    editor=u.CreateWindowExW(0,'EDIT','',0x50000044,20,20,540,200,window,None,None,None)
    u.keybd_event(18,0,0,0);u.keybd_event(18,0,2,0);u.SetForegroundWindow(window);u.SetFocus(editor)
    u.SendMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];u.GetDpiForWindow.argtypes=[w.HWND];u.GetDpiForWindow.restype=w.UINT
    process=subprocess.Popen([str(ROOT/'native/target/release/dictately.exe'),'--test-pcm',str(pcm),'--test-target',str(window)],env=dict(os.environ,LOCALFLOW_DATA=str(base)),creationflags=subprocess.CREATE_NO_WINDOW)
    def status():
        try:return json.loads((base/'status.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):return {}
    def wait(predicate,seconds=35):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            pump();assert u.GetForegroundWindow()==window,'External focus change interrupted test'
            assert process.poll() is None
            if predicate():return
            time.sleep(.01)
        raise AssertionError(f'Timed out: {status()}')
    hwnd=0
    try:
        wait(lambda:status().get('pid')==process.pid and status().get('engine_ready'));hwnd=status()['hwnd']
        assert status()['language']=='en'
        chord(123);wait(lambda:status()['state']=='recording');chord(123);chord(121)
        wait(lambda:status().get('language_picker'));opened_at=time.monotonic();time.sleep(.5);pump()
        rect=w.RECT();u.GetWindowRect.argtypes=[w.HWND,c.POINTER(w.RECT)];u.GetWindowRect(hwnd,c.byref(rect))
        scale=u.GetDpiForWindow(hwnd)/96
        assert abs((rect.right-rect.left)/scale-156)<1.1 and abs((rect.bottom-rect.top)/scale-44)<1.1,((rect.right-rect.left,rect.bottom-rect.top),scale)
        u.SendMessageW(hwnd,0x20a,120<<16,0);wait(lambda:status()['language']=='id')
        u.SendMessageW(hwnd,0x20a,((-120)&0xffff)<<16,0);wait(lambda:status()['language']=='en')
        point=lambda x,y:(int(y*scale)<<16)|int(x*scale)
        u.SendMessageW(hwnd,0x201,1,point(32,22));u.SendMessageW(hwnd,0x200,1,point(65,22));u.SendMessageW(hwnd,0x202,0,point(65,22))
        wait(lambda:status()['language']=='id')
        def click(x):
            u.SendMessageW(hwnd,0x201,1,point(x,22));u.SendMessageW(hwnd,0x202,0,point(x,22))
        click(100);wait(lambda:status()['language']=='en')
        click(30);wait(lambda:status()['language']=='id')
        click(100);wait(lambda:status()['language']=='en')
        chord(191);time.sleep(.1);pump();text=c.create_unicode_buffer(200);u.GetWindowTextW(editor,text,200)
        assert text.value=='/','Slash must type normally'
        # The synthetic word arrives after the mouse checks, through the real model.
        wait(lambda:status()['language']=='id',20)
        wait(lambda:time.monotonic()-opened_at>=31.2,35)
        assert status()['language_picker'],'Picker must stay open past its former 30-second timeout'
        assert not status()['live_commits'],'Picker speech must not type into the editor'
        chord(121);wait(lambda:not status()['language_picker'])
        chord(121);wait(lambda:status()['language_picker'])
        chord(123);wait(lambda:not status()['language_picker'] and status()['state']=='recording')
        chord(123);wait(lambda:status()['state']=='ready')
        result={'wheel':True,'drag':True,'click':True,'voice':True,'slash_types':True,'two_languages_only':True,'persistent_picker':True,'logical_size':[156,44]}
        (base/'results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
    finally:
        hwnd=hwnd or status().get('hwnd',0)
        if hwnd and process.poll() is None:u.PostMessageW(hwnd,0x8004,0,0)
        process.wait(timeout=15);u.DestroyWindow(window)
        if before:u.SetForegroundWindow(before)

if __name__=='__main__':main()
