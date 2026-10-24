"""Exercise Windows preference controls and saving without opening any mic."""
from _support import ROOT
import json
import subprocess
import tempfile
from pathlib import Path
import settings_window as ui
from unittest.mock import patch


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
            import tkinter as tk
            dialog=tk.Toplevel(window.root)
            try:
                window.root.update()
                import ctypes as c
                user=c.WinDLL('user32',use_last_error=True)
                user.SendMessageW.argtypes=[c.c_void_p,c.c_uint,c.c_size_t,c.c_ssize_t];user.SendMessageW.restype=c.c_ssize_t
                user.GetClassLongPtrW.argtypes=[c.c_void_p,c.c_int];user.GetClassLongPtrW.restype=c.c_void_p
                def icon(widget):
                    hwnd=int(widget.wm_frame(),0)
                    return user.SendMessageW(hwnd,0x7f,0,0) or user.GetClassLongPtrW(hwnd,-34)
                assert icon(window.root) and icon(dialog)==icon(window.root),'Dialog did not inherit the app icon'
            finally:dialog.destroy()
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
            ui.write(ui.DATA/'history.json',[{'at':'2026-10-20','cleaned':'history update','raw':'history update'}])
            window.poll();assert window.rows[0]['cleaned']=='history update'
            with patch.object(ui,'read',side_effect=AssertionError('Unchanged history was reparsed')):
                window.poll()
            before=ui.read(ui.DATA/'settings.json',{})
            window.language_hotkey=window.dictation_hotkey;window.save_shortcuts()
            assert ui.read(ui.DATA/'settings.json',{})==before,'Conflicting shortcut was saved'
            window.language_hotkey=0x1bf;window.save_shortcuts()
            assert ui.read(ui.DATA/'settings.json',{})['language_hotkey']==0x1bf
            print('Preference controls, device enumeration, persistence, unavailable device and system-default reset passed; no capture opened')
        finally:
            window.root.destroy()


if __name__=='__main__':main()
