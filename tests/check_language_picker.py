from _support import ROOT
"""Owned-window Windows tests for picker routing, live rebinding and voice commands.

Uses synthesized audio files, no microphone and no insertion into personal apps.
Run tests/make_audio_fixtures.ps1 first, with Dictately stopped.
"""
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import wave

ROOT=Path(__file__).resolve().parents[1]
from hotkeys import CAPTURE_EVENT

user=c.WinDLL('user32',use_last_error=True);kernel=c.WinDLL('kernel32',use_last_error=True)
user.CreateWindowExW.restype=w.HWND
user.CreateWindowExW.argtypes=[w.DWORD,w.LPCWSTR,w.LPCWSTR,w.DWORD,c.c_int,c.c_int,c.c_int,c.c_int,w.HWND,w.HMENU,w.HINSTANCE,c.c_void_p]
user.PostMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM]
user.SetForegroundWindow.argtypes=[w.HWND]
user.SetFocus.argtypes=[w.HWND];user.DestroyWindow.argtypes=[w.HWND]
user.GetClientRect.argtypes=[w.HWND,c.POINTER(w.RECT)]
user.keybd_event.argtypes=[w.BYTE,w.BYTE,w.DWORD,c.c_size_t]
user.GetForegroundWindow.restype=w.HWND
kernel.CreateEventW.argtypes=[c.c_void_p,w.BOOL,w.BOOL,w.LPCWSTR];kernel.CreateEventW.restype=w.HANDLE
kernel.CloseHandle.argtypes=[w.HANDLE]
passthrough_f10=0


def read(path):
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError,OSError):return {}


def wait(predicate,seconds=30):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        pump()
        value=predicate()
        if value:return value
        time.sleep(.02)
    raise AssertionError('Timed out waiting for language-picker state')


def pump():
    global passthrough_f10
    message=w.MSG()
    while user.PeekMessageW(c.byref(message),None,0,0,1):
        # During shortcut capture F10 intentionally reaches this fixture. A
        # stock top-level EDIT would enter Windows' modal system-menu loop,
        # preventing the Python wait deadline from running. Count delivery
        # without dispatching that menu action; real hotkeys are still tested.
        if message.message in (0x100,0x101,0x104,0x105) and message.wParam==121:
            passthrough_f10+=1;continue
        if message.message==0x112 and message.wParam&0xfff0==0xf100:continue
        user.TranslateMessage(c.byref(message));user.DispatchMessageW(c.byref(message))


def chord(value):
    modifiers=[key for flag,key in [(1,17),(2,16),(4,18),(8,91)] if (value>>8)&flag]
    for key in modifiers+[value&255]:user.keybd_event(key,user.MapVirtualKeyW(key,0),0,0)
    for key in [value&255]+list(reversed(modifiers)):user.keybd_event(key,user.MapVirtualKeyW(key,0),2,0)


def main():
    base=ROOT/'qa'/('language-controls-'+uuid.uuid4().hex[:8]);base.mkdir(parents=True)
    app_dir=Path(sys.argv[sys.argv.index('--app-dir')+1]) if '--app-dir' in sys.argv else ROOT/'native/target/release'
    executable=app_dir/('Dictately.exe' if '--app-dir' in sys.argv else 'dictately.exe')
    before=user.GetForegroundWindow()
    fixture=user.CreateWindowExW(0,'EDIT','Dictately synthetic shortcut fixture',0x10CF0000,100,100,550,250,None,None,None,None)
    assert fixture
    # Only synthetic fixtures receive hotkey events; no text is injected.
    user.keybd_event(18,0,0,0);user.keybd_event(18,0,2,0);user.SetForegroundWindow(fixture);user.SetFocus(fixture)
    results={}
    try:
        for index,(name,expected) in enumerate([('english','en'),('indonesia','id'),('both','both')]):
            data=base/name;data.mkdir()
            with wave.open(str(ROOT/f'qa/language-{name}.wav'),'rb') as source:
                assert (source.getframerate(),source.getsampwidth(),source.getnchannels())==(16000,2,1)
                pcm=source.readframes(source.getnframes())+bytes(32000)
            if index==0:pcm=bytes(128000)+pcm  # Four silent seconds before voice.
            sample=data/'voice.pcm';sample.write_bytes(pcm)
            environment=dict(os.environ,LOCALFLOW_DATA=str(data))
            args=[str(executable),'--test-pcm',str(sample),'--test-target','0']
            if index==0:args.append('--test-no-warmup')
            process=subprocess.Popen(args,env=environment,creationflags=subprocess.CREATE_NO_WINDOW)
            status=lambda:read(data/'status.json')
            try:
                wait(lambda:status().get('pid')==process.pid and (index==0 or status().get('engine_ready')))
                hwnd=status()['hwnd']
                if index==0:
                    # While a shortcut-capture dialog owns this event, old
                    # shortcuts must reach the dialog instead of toggling the app.
                    capture=kernel.CreateEventW(None,True,True,CAPTURE_EVENT)
                    try:
                        delivered=passthrough_f10
                        chord(121);time.sleep(.15);pump();assert not status().get('language_picker')
                        assert passthrough_f10>delivered,'Shortcut capture did not receive F10'
                        print('F10 shortcut-capture passthrough passed',flush=True)
                    finally:kernel.CloseHandle(capture)
                    chord(121);wait(lambda:status().get('language_picker'))
                    # Hit targets move with the entry animation; click only once
                    # the canonical resting coordinates are visible.
                    time.sleep(1.2);pump()
                    assert not status().get('worker_loaded'),'Silence loaded the AI model'
                    bounds=w.RECT();user.GetClientRect(hwnd,c.byref(bounds))
                    # Mouse-select Both; no voice text should have been delivered.
                    x=int(304*bounds.right/380);y=int(74*bounds.bottom/112)
                    user.PostMessageW(hwnd,0x202,0,(y<<16)|x)
                    wait(lambda:not status().get('language_picker') and status().get('language')=='both')
                    results['mouse_selection']=True
                    assert not status().get('worker_loaded'),'Mouse selection loaded the AI model'
                    results['cold_mouse_without_model']=True
                    time.sleep(.5)
                    # Save a modifier combination and confirm live reload.
                    custom=(3<<8)|121  # Ctrl+Shift+F10
                    subprocess.run([str(executable),'--set-hotkeys','123',str(custom)],env=environment,check=True,timeout=10)
                    time.sleep(1.2);chord(custom);wait(lambda:status().get('language_picker'))
                    user.PostMessageW(hwnd,0x8002,0,0);wait(lambda:not status().get('language_picker'))
                    results['custom_shortcut_and_cancel']=True
                    time.sleep(.5)
                # Direct owned-window request also covers the tray menu path.
                user.PostMessageW(hwnd,0x8005,0,0)
                wait(lambda:status().get('language_picker'))
                wait(lambda:not status().get('language_picker') and status().get('language')==expected,40)
                assert not (data/'history.json').exists(),'Language command was saved as dictation'
                assert status().get('pending_characters')==0
                results['voice_'+name]=True
                print('Language selection passed:',name,flush=True)
            finally:
                if process.poll() is None:
                    user.PostMessageW(status().get('hwnd',0),0x8004,0,0)
                    process.wait(timeout=15)
        (base/'results.json').write_text(json.dumps(results,indent=2))
        print(json.dumps(results,indent=2))
    finally:
        user.DestroyWindow(fixture)
        if before:user.SetForegroundWindow(before)


if __name__=='__main__':main()
