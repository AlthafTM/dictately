"""Exercise Windows preference controls and saving without opening any mic."""
from _support import ROOT
import json
import subprocess
import tempfile
from pathlib import Path
import settings_window as ui


def main():
    result=subprocess.run([str(ROOT/'Dictately.exe'),'--list-microphones'],capture_output=True,text=True,encoding='utf-8',check=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
    devices=json.loads(result.stdout)
    assert isinstance(devices,list)
    with tempfile.TemporaryDirectory() as temporary:
        ui.DATA=Path(temporary)
        ui.write(ui.DATA/'settings.json',{'vocabulary':'keep this setting','language':'id'})
        window=ui.Window()
        try:
            window.root.update()
            assert window.overlay_position.get()=='Bottom center'
            assert window.microphone.get()=='System default'
            window.overlay_position.set('Top right')
            if devices:
                identity=devices[0]['id']
                window.microphone.set(next(name for name,value in window.microphone_choices.items() if value==identity))
            else:
                identity=''
            window.save_preferences()
            saved=ui.read(ui.DATA/'settings.json',{})
            assert saved['overlay_position']=='top_right' and saved['microphone']==identity,saved
            assert saved['vocabulary']=='keep this setting' and saved['language']=='id'
            window.refresh_microphones('missing-fixture-device')
            assert window.microphone.get()=='Saved microphone (unavailable)'
            window.save_preferences()
            assert ui.read(ui.DATA/'settings.json',{})['microphone']=='missing-fixture-device'
            window.refresh_microphones('')
            window.overlay_position.set('Bottom center');window.save_preferences()
            assert ui.read(ui.DATA/'settings.json',{})['microphone']==''
            print('Preference controls, device enumeration, persistence, unavailable device and system-default reset passed; no capture opened')
        finally:
            window.root.destroy()


if __name__=='__main__':main()
