from _support import ROOT
"""Measure native UI frame submission with synthetic silence and no editor input."""
import ctypes as c
from ctypes import wintypes as w
import json,os
from pathlib import Path
import statistics,subprocess,sys,time,uuid
from ui_preferences import overlay_origin, POSITIONS
ROOT=Path(__file__).resolve().parents[1]
user=c.WinDLL('user32',use_last_error=True)
user.PostMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM]
user.SendMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];user.SendMessageW.restype=c.c_ssize_t
user.GetClassLongPtrW.argtypes=[w.HWND,c.c_int];user.GetClassLongPtrW.restype=c.c_size_t
class MonitorInfo(c.Structure):
    _fields_=[('cbSize',w.DWORD),('monitor',w.RECT),('work',w.RECT),('flags',w.DWORD)]
user.MonitorFromWindow.argtypes=[w.HWND,w.DWORD];user.MonitorFromWindow.restype=w.HANDLE
user.GetMonitorInfoW.argtypes=[w.HANDLE,c.POINTER(MonitorInfo)]
user.GetWindowRect.argtypes=[w.HWND,c.POINTER(w.RECT)]
user.GetDpiForWindow.argtypes=[w.HWND];user.GetDpiForWindow.restype=w.UINT
user.SetProcessDpiAwarenessContext.argtypes=[c.c_void_p]

def main():
    user.SetProcessDpiAwarenessContext(c.c_void_p(-4))
    waveform='--waveform' in sys.argv
    position=sys.argv[sys.argv.index('--position')+1] if '--position' in sys.argv else 'bottom_center'
    assert position in POSITIONS.values()
    data=ROOT/'qa'/('animation-'+uuid.uuid4().hex[:8]);data.mkdir(parents=True)
    (data/'settings.json').write_text(json.dumps({'overlay_position':position}),encoding='utf-8')
    pcm=data/'silence.pcm';pcm.write_bytes(bytes(16000*2*10))
    metrics=data/'frames.json'
    exe=ROOT/'native/target/release/dictately.exe'
    process=subprocess.Popen([str(exe),'--test-pcm',str(pcm),'--test-target','0','--test-no-warmup'],env=dict(os.environ,LOCALFLOW_DATA=str(data),DICTATELY_FRAME_METRICS=str(metrics)),creationflags=subprocess.CREATE_NO_WINDOW)
    def status():
        try:return json.loads((data/'status.json').read_text())
        except (OSError,ValueError):return {}
    hwnd=0
    try:
        deadline=time.monotonic()+10
        while status().get('pid')!=process.pid:
            assert time.monotonic()<deadline,'Controller did not start';time.sleep(.01)
        hwnd=status()['hwnd'];user.PostMessageW(hwnd,0x8001 if waveform else 0x8005,0,0)
        time.sleep(3.5)
        assert status().get('state')=='recording' if waveform else status().get('language_picker')
        if not waveform:assert not status().get('worker_loaded'),'Silent picker loaded speech model'
        assert status().get('overlay_position')==position
        rect=w.RECT();assert user.GetWindowRect(hwnd,c.byref(rect))
        monitor=MonitorInfo();monitor.cbSize=c.sizeof(monitor)
        assert user.GetMonitorInfoW(user.MonitorFromWindow(hwnd,2),c.byref(monitor))
        area=monitor.work
        expected=overlay_origin((area.left,area.top,area.right-area.left,area.bottom-area.top),(rect.right-rect.left,rect.bottom-rect.top),int(22*user.GetDpiForWindow(hwnd)/96+.5),position)
        assert (rect.left,rect.top)==expected,((rect.left,rect.top),expected,position)
        class_cursor=bool(user.GetClassLongPtrW(hwnd,-12))
        cursor_handled=bool(user.SendMessageW(hwnd,0x20,hwnd,(0x200<<16)|1))
        user.PostMessageW(hwnd,0x8002,0,0);time.sleep(.5)
    finally:
        if hwnd:user.PostMessageW(hwnd,0x8004,0,0)
        process.wait(timeout=15)
    assert process.returncode==0
    frames=json.loads(metrics.read_text());active=[f for f in frames if f[3]==0]
    start=active[0][0]
    steady=[f for f in active if 1500<f[0]-start<3300]
    intervals=sorted(f[1] for f in steady)
    assert len(intervals)>20
    result={'steady_frames':len(steady),'median_interval_ms':round(statistics.median(intervals),2),'p95_interval_ms':round(intervals[int(.95*(len(intervals)-1))],2),'median_render_ms':round(statistics.median(f[2] for f in steady),2),'class_cursor_assigned':class_cursor,'cursor_message_handled':cursor_handled,'mode':'waveform' if waveform else 'language_picker','silent_picker_without_model':None if waveform else True}
    result['overlay_position']=position
    if '--baseline' not in sys.argv:
        assert class_cursor and cursor_handled,'Panel cursor was not explicitly assigned'
        assert result['median_interval_ms']<23,result
        assert result['p95_interval_ms']<35,result
    (data/'results.json').write_text(json.dumps(result,indent=2))
    (ROOT/('qa/animation-baseline.json' if '--baseline' in sys.argv else 'qa/waveform-animation-verification.json' if waveform else 'qa/animation-verification.json')).write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
