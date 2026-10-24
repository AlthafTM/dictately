"""Real local speech recognition and deferred capture, with OS capture simulated."""
from _support import ROOT
from windows_controls import u,pump
import json,os,subprocess,time,uuid,wave
import numpy as np

def main(language='en'):
    base=ROOT/'qa'/('screenshot-voice-'+uuid.uuid4().hex[:8]);base.mkdir(parents=True)
    with wave.open(str(ROOT/'qa/screenshot-command.wav'),'rb') as sound:
        rate=sound.getframerate();channels=sound.getnchannels();assert sound.getsampwidth()==2
        voice=np.frombuffer(sound.readframes(sound.getnframes()),dtype='<i2').reshape(-1,channels).mean(axis=1)
    voice=np.interp(np.arange(int(len(voice)*16000/rate))*rate/16000,np.arange(len(voice)),voice).astype('<i2')
    pcm=base/'voice.pcm';pcm.write_bytes(bytes(32000)+voice.tobytes()+bytes(32000*int(20-len(voice)/16000))+voice.tobytes()+bytes(32000*35))
    (base/'settings.json').write_text(json.dumps({'language':language}),encoding='utf-8')
    event=base/'capture.json'
    process=subprocess.Popen([str(ROOT/'native/target/release/dictately.exe'),'--test-pcm',str(pcm),'--test-target','0','--test-no-hooks'],env=dict(os.environ,LOCALFLOW_DATA=str(base),DICTATELY_TEST_SCREENSHOT=str(event)),creationflags=subprocess.CREATE_NO_WINDOW)
    def status():
        try:return json.loads((base/'status.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):return {}
    def wait(predicate,timeout=45):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            pump();assert process.poll() is None
            if predicate():return
            time.sleep(.01)
        raise AssertionError('Timed out: '+str(status()))
    hwnd=0
    try:
        wait(lambda:status().get('pid')==process.pid and status().get('engine_ready'));hwnd=status()['hwnd']
        started=time.monotonic();u.PostMessageW(hwnd,0x8001,0,0)
        wait(event.exists)
        voice_end=1.+(np.flatnonzero(np.abs(voice.astype(np.int32))>300)[-1]+1)/16000.
        handoff_ms=round((time.monotonic()-started-voice_end)*1000,1)
        captured=json.loads(event.read_text(encoding='utf-8'))
        assert captured=={'overlay_visible':False,'recording':False,'action':'screenshot'},captured
        assert status()['live_commits']==0,'Voice command must not type'
        wait(lambda:status().get('state')=='recording' and not status().get('screenshot_pending'))
        event.unlink()
        wait(event.exists)
        assert json.loads(event.read_text(encoding='utf-8'))==captured
        wait(lambda:status().get('state')=='recording' and not status().get('screenshot_pending'))
        assert not (base/'history.json').exists(),'Command must not become transcript history'
        print(json.dumps({'selected_language':language,'recognized_screenshot_command':True,'overlay_excluded_before_launch':True,'command_typed':False,'estimated_synthetic_word_end_to_handoff_ms':handoff_ms,'automatic_resume':True,'repeated_capture':True,'os_capture':'simulated; no screen image saved'},indent=2))
    finally:
        hwnd=hwnd or status().get('hwnd',0)
        if hwnd:u.PostMessageW(hwnd,0x8004,0,0)
        process.wait(timeout=15)

if __name__=='__main__':
    main('en')
