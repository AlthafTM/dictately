"""Real local speech with an owned editor that rejects and then accepts input."""
from _support import ROOT
from windows_controls import u,pump,chord
import ctypes as c
import json,os,subprocess,time,uuid

def main():
    pcm=ROOT/'qa/delivery-voice.pcm';assert pcm.is_file()
    base=ROOT/'qa'/('delivery-recovery-'+uuid.uuid4().hex[:8]);base.mkdir(parents=True)
    (base/'settings.json').write_text(json.dumps({'language':'en'}),encoding='utf-8')
    before=u.GetForegroundWindow()
    window=u.CreateWindowExW(0,'STATIC','Dictately delivery recovery check',0x10CF0000,100,100,700,520,None,None,None,None)
    editor=u.CreateWindowExW(0,'EDIT','Existing: ',0x503010c4,20,20,640,450,window,None,None,None)
    u.keybd_event(18,0,0,0);u.keybd_event(18,0,2,0);u.SetForegroundWindow(window);u.SetFocus(editor)
    u.SendMessageW.argtypes=[c.c_void_p,c.c_uint,c.c_size_t,c.c_ssize_t]
    u.SendMessageW(editor,0xb1,10,10)
    process=subprocess.Popen([str(ROOT/'native/target/release/dictately.exe'),'--test-pcm',str(pcm),'--test-target',str(window)],env=dict(os.environ,LOCALFLOW_DATA=str(base)),creationflags=subprocess.CREATE_NO_WINDOW)
    def status():
        try:return json.loads((base/'status.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):return {}
    def wait(predicate,seconds=40):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            pump();assert u.GetForegroundWindow()==window,'External focus change interrupted test'
            assert process.poll() is None,'Controller exited'
            if predicate():return
            time.sleep(.02)
        raise AssertionError(f'Timed out: {status()}')
    hwnd=0
    try:
        wait(lambda:status().get('pid')==process.pid and status().get('engine_ready'));hwnd=status()['hwnd']
        chord(123);wait(lambda:status().get('live_commits',0)>0)
        # Keep the same captured editor, but make it reject subsequent delivery.
        u.SendMessageW(editor,0xcf,1,0)
        wait(lambda:status().get('input_blocked'))
        assert status().get('state')=='recording','Typing failure must not stop recording'
        time.sleep(1);pump();assert status().get('state')=='recording'
        chord(123);wait(lambda:status().get('state')=='ready')
        failed=status();history=json.loads((base/'history.json').read_text(encoding='utf-8'))
        assert failed['pipeline_stage']=='rejected' and failed['pending_characters']>0,failed
        assert history[0]['raw'] and history[0]['delivery_failed'],history[0]
        log=(base/'native.log').read_text(encoding='utf-8')
        assert log.count('stopped retries and retained speech in History')==1,log
        u.SendMessageW(editor,0xcf,0,0)
        chord(123);wait(lambda:status().get('state')=='recording' and not status().get('input_blocked'))
        wait(lambda:status().get('live_commits',0)>0)
        chord(123);wait(lambda:status().get('state')=='ready')
        recovered=status();assert not recovered['input_blocked'],recovered
        (base/'results.json').write_text(json.dumps({'failed':failed,'recovered':recovered},indent=2),encoding='utf-8')
        print('Read-only editor: bounded rejection, speech retained, rejected feedback; new session delivers again')
    finally:
        hwnd=hwnd or status().get('hwnd',0)
        if hwnd and process.poll() is None:u.PostMessageW(hwnd,0x8004,0,0)
        process.wait(timeout=15);u.DestroyWindow(window)
        if before:u.SetForegroundWindow(before)

if __name__=='__main__':main()
