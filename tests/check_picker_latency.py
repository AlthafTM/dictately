"""Measure real shortcut-to-visible latency and transition frames; synthetic audio only."""
from _support import ROOT
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import uuid

user=c.WinDLL('user32',use_last_error=True)
user.PostMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM]
user.IsWindowVisible.argtypes=[w.HWND]
user.keybd_event.argtypes=[w.BYTE,w.BYTE,w.DWORD,c.c_size_t]
user.SetProcessDpiAwarenessContext.argtypes=[c.c_void_p]
user.GetGuiResources.argtypes=[w.HANDLE,w.DWORD];user.GetGuiResources.restype=w.DWORD
class MemoryCounters(c.Structure):
    _fields_=[('cb',w.DWORD),('page_faults',w.DWORD)]+[(name,c.c_size_t) for name in ('peak_working_set','working_set','peak_paged_pool','paged_pool','peak_nonpaged_pool','nonpaged_pool','pagefile','peak_pagefile','private')]
psapi=c.WinDLL('psapi');psapi.GetProcessMemoryInfo.argtypes=[w.HANDLE,c.POINTER(MemoryCounters),w.DWORD]


def main():
    user.SetProcessDpiAwarenessContext(c.c_void_p(-4))
    baseline='--baseline' in sys.argv
    messages='--messages' in sys.argv
    exe=Path(sys.argv[sys.argv.index('--exe')+1]) if '--exe' in sys.argv else ROOT/'native/target/release/dictately.exe'
    data=ROOT/'qa'/('picker-latency-'+uuid.uuid4().hex[:8]);data.mkdir()
    pcm=data/'silence.pcm';pcm.write_bytes(bytes(16000*2*30))
    frames=data/'frames.json'
    process=subprocess.Popen([str(exe),'--test-pcm',str(pcm),'--test-target','0','--test-no-warmup'],env=dict(os.environ,LOCALFLOW_DATA=str(data),DICTATELY_FRAME_METRICS=str(frames)),creationflags=subprocess.CREATE_NO_WINDOW)
    def status():
        try:return json.loads((data/'status.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):return {}
    def wait(predicate,seconds=12):
        deadline=time.perf_counter()+seconds
        while not predicate():
            assert time.perf_counter()<deadline,'Controller timed out'
            time.sleep(.001)
    def shortcut():
        if messages:
            user.PostMessageW(hwnd,0x8005,0,0);return
        user.keybd_event(121,0,0,0)
        user.keybd_event(121,0,2,0)
    hwnd=0;latencies=[]
    try:
        wait(lambda:status().get('pid')==process.pid)
        hwnd=status()['hwnd'];time.sleep(.1)
        memory=MemoryCounters();memory.cb=c.sizeof(memory)
        assert psapi.GetProcessMemoryInfo(w.HANDLE(int(process._handle)),c.byref(memory),memory.cb)
        idle_private_mb=memory.private/1024**2
        initial_gdi=user.GetGuiResources(w.HANDLE(int(process._handle)),0)
        for cycle in range(5):
            before=time.perf_counter();shortcut()
            wait(lambda:user.IsWindowVisible(hwnd))
            latencies.append((time.perf_counter()-before)*1000)
            time.sleep(.65)
            shortcut();wait(lambda:not user.IsWindowVisible(hwnd))
            time.sleep(.15)
            # Alternate surface types so repeated F10 includes the common path
            # after a dictation, rather than only reopening an unchanged picker.
            user.PostMessageW(hwnd,0x8001,0,0);wait(lambda:user.IsWindowVisible(hwnd));time.sleep(.2)
            user.PostMessageW(hwnd,0x8002,0,0);wait(lambda:not user.IsWindowVisible(hwnd));time.sleep(.15)
        final_gdi=user.GetGuiResources(w.HANDLE(int(process._handle)),0)
        assert final_gdi<=initial_gdi+2,(initial_gdi,final_gdi)
    finally:
        if hwnd:user.PostMessageW(hwnd,0x8004,0,0)
        process.wait(timeout=15)
    assert process.returncode==0
    values=json.loads(frames.read_text(encoding='utf-8'))
    intervals=[row[1] for row in values if 0<row[1]<500]
    render=[row[2] for row in values]
    quantile=lambda items:sorted(items)[int(.95*(len(items)-1))]
    report={'shortcut_visible_ms':latencies,'median_shortcut_visible_ms':statistics.median(latencies),'first_shortcut_visible_ms':latencies[0],
            'all_transition_p95_interval_ms':quantile(intervals),'max_render_ms':max(render),'p95_render_ms':quantile(render),'frames':len(values),
            'idle_private_mb':idle_private_mb,'gdi_objects_before':initial_gdi,'gdi_objects_after':final_gdi,'trigger':'posted message' if messages else 'F10'}
    if not baseline:
        assert max(latencies)<(250 if messages else 100),report
        if not messages:assert report['all_transition_p95_interval_ms']<35,report
    destination=ROOT/'qa'/('picker-latency-before.json' if baseline else 'picker-latency-after.json')
    destination.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
