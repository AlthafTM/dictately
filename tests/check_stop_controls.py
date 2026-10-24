"""Rapid controller controls with synthetic silence, no keyboard injection or mic."""
from _support import ROOT
from windows_controls import u,pump
import json,os,subprocess,time,uuid

def main():
    base=ROOT/'qa'/('stop-controls-'+uuid.uuid4().hex[:8]);base.mkdir(parents=True)
    pcm=base/'silence.pcm';pcm.write_bytes(bytes(32000*15))
    process=subprocess.Popen([str(ROOT/'native/target/release/dictately.exe'),'--test-pcm',str(pcm),'--test-target','0','--test-no-hooks','--test-no-warmup'],env=dict(os.environ,LOCALFLOW_DATA=str(base)),creationflags=subprocess.CREATE_NO_WINDOW)
    def status():
        try:return json.loads((base/'status.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):return {}
    def wait(predicate,timeout=35):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            pump();assert process.poll() is None,'Controller exited unexpectedly'
            if predicate():return
            time.sleep(.005)
        raise AssertionError('Timed out: '+str(status()))
    hwnd=0;events=0
    def press(key):
        nonlocal events
        events+=1;u.PostMessageW(hwnd,0x8000+key,0,0)
    def burst(keys):
        for key in keys:press(key);time.sleep(.003)
    try:
        wait(lambda:status().get('pid')==process.pid);hwnd=status()['hwnd']
        # Last intent is idle: no microphone or model request should start.
        burst([1,5]*20+[1,1]);time.sleep(.2)
        assert not u.IsWindowVisible(hwnd) and not status().get('worker_loaded'),status()
        times=[]
        for _ in range(3):
            burst([1,5]*20);started=time.monotonic()
            wait(lambda:status().get('language_picker') and u.IsWindowVisible(hwnd),2)
            times.append(round((time.monotonic()-started)*1000,1))
            assert not status().get('worker_loaded'),'Silent picker loaded model'
            press(5);wait(lambda:not u.IsWindowVisible(hwnd),1.2)
            assert status()['state']=='ready'
        # Switching away while a cold model prepares must retain the new request.
        press(1);wait(lambda:status().get('state')=='recording',2)
        time.sleep(.35);press(5)
        wait(lambda:u.IsWindowVisible(hwnd),1)
        wait(lambda:status().get('language_picker'),40)
        press(1);wait(lambda:status().get('state')=='recording' and not status().get('language_picker'),2)
        # Repeated stop/start while already finishing must honor the final picker.
        time.sleep(.35);burst([1,1,5,1,5])
        wait(lambda:status().get('language_picker'),35)
        press(5);wait(lambda:not u.IsWindowVisible(hwnd),1.2)
        assert not status().get('microphone_starting') and not status().get('microphone_stopping')
        assert 'Microphone ready after' not in (base/'native.log').read_text(encoding='utf-8')
        result={'control_events':events,'picker_after_burst_ms':times,'switch_during_finalization':True,'real_microphone_opened':False,'global_keyboard_input':False}
        (base/'results.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))
    finally:
        hwnd=hwnd or status().get('hwnd',0)
        if hwnd:u.PostMessageW(hwnd,0x8004,0,0)
        process.wait(timeout=15)

if __name__=='__main__':main()
