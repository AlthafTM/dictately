"""Install into an isolated folder, exercise frozen runtime, and uninstall.

This manages the current user's Dictately shortcut/registry entries temporarily;
they are backed up and restored. Run with no dictation session in progress.
"""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import base64
import sys
import time
import tomllib
import uuid
import winreg

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / "qa"
INSTALL = QA / ("installer smoke " + uuid.uuid4().hex[:8])
VERSION=tomllib.loads((ROOT/'native/Cargo.toml').read_text(encoding='utf-8'))['package']['version']
INSTALLER = ROOT / f"dist/Dictately-{VERSION}-Setup.exe"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Dictately"
SHORTCUT = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Dictately.lnk"
SETTINGS_SHORTCUT=SHORTCUT.with_name('Dictately Settings.lnk')
LEGACY_KEY=APP_KEY.rsplit('\\',1)[0]+r'\Transcribr'
LEGACY_SHORTCUTS=[SHORTCUT.with_name(name) for name in ('Transcribr.lnk','Transcribr Settings.lnk')]


def values(path):
    result = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            for index in range(winreg.QueryInfoKey(key)[1]):
                name, value, kind = winreg.EnumValue(key, index)
                result[name] = (value, kind)
        return result
    except FileNotFoundError:
        return None


def run(command, **options):
    result = subprocess.run([str(part) for part in command], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=600,
                            creationflags=subprocess.CREATE_NO_WINDOW, **options)
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} exited {result.returncode}: {result.stderr[-1500:]}")
    return result


def close_settings(process):
    user = ctypes.WinDLL("user32")
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    @callback_type
    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == process.pid:
            user.PostMessageW(hwnd, 0x10, 0, 0)
        return True
    user.EnumWindows(visit, 0)


def main():
    assert INSTALL.resolve().is_relative_to(QA.resolve())
    prior_run = (values(RUN_KEY) or {}).get("Dictately")
    prior_app = values(APP_KEY)
    prior_shortcut = SHORTCUT.read_bytes() if SHORTCUT.exists() else None
    prior_settings_shortcut=SETTINGS_SHORTCUT.read_bytes() if SETTINGS_SHORTCUT.exists() else None
    prior_legacy_app=values(LEGACY_KEY)
    prior_legacy_run=(values(RUN_KEY) or {}).get('Transcribr')
    prior_legacy_shortcuts={path:path.read_bytes() if path.exists() else None for path in LEGACY_SHORTCUTS}
    environment = dict(os.environ, DICTATELY_ROOT=str(INSTALL), LOCALFLOW_DATA=str(QA / "installed-data"))
    environment["PATH"] = os.pathsep.join([str(Path(os.environ["WINDIR"]) / "System32"), os.environ["WINDIR"]])
    results = {}
    settings = None
    try:
        print("Installing into a disposable folder using the verified model cache", flush=True)
        run([INSTALLER, "--silent", "--dir", INSTALL, "--no-launch", "--no-startup",
             "--model-cache", ROOT / "models/whisper-turbo"])
        assert (INSTALL / "install-manifest.txt").is_file()
        assert not (INSTALL / ".venv").exists()
        assert (values(APP_KEY) or {})["InstallLocation"][0] == str(INSTALL)
        assert SETTINGS_SHORTCUT.exists()
        results["installed_without_system_python"] = True
        print('Checking upgrade from the previous app identity',flush=True)
        marker=INSTALL/'install-manifest.txt'
        lines=marker.read_text().splitlines();lines[0]='Transcribr:{5BFCCF8A-BAA9-48DF-917C-D1DFE4ABCF88}'
        shutil.copyfile(INSTALL/'Dictately.exe',INSTALL/'Transcribr.exe');lines.append('Transcribr.exe')
        marker.write_text('\n'.join(lines)+'\n')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,LEGACY_KEY) as key:
            for name,(value,kind) in values(APP_KEY).items():winreg.SetValueEx(key,name,0,kind,value)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER,APP_KEY)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,RUN_KEY) as key:
            winreg.SetValueEx(key,'Transcribr',0,winreg.REG_SZ,'"'+str(INSTALL/'Transcribr.exe')+'"')
        script='''$shell=New-Object -ComObject WScript.Shell
foreach ($name in @('Transcribr.lnk','Transcribr Settings.lnk')) {
 $link=$shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Programs')) $name))
 $link.TargetPath=Join-Path $env:DICTATELY_TEST_INSTALL 'Transcribr.exe'
 $link.WorkingDirectory=$env:DICTATELY_TEST_INSTALL
 $link.Save()
}'''
        run(['powershell','-NoProfile','-EncodedCommand',base64.b64encode(script.encode('utf-16-le')).decode()],env=dict(os.environ,DICTATELY_TEST_INSTALL=str(INSTALL)))
        profile=INSTALL/'upgrade-profile';old=profile/'Transcribr';old.mkdir(parents=True)
        (old/'settings.json').write_text(json.dumps({'language':'id','dictation_hotkey':123,'language_hotkey':122,'vocabulary':'keep my terms'}))
        (old/'history.json').write_text('[{"raw":"keep my local history"}]')
        migration_env=dict(os.environ,LOCALAPPDATA=str(profile))
        migration_env.pop('LOCALFLOW_DATA',None);migration_env.pop('DICTATELY_DATA',None)
        run([INSTALL/'Dictately.exe','--enable-startup'],env=migration_env)
        migrated=json.loads((profile/'Dictately/settings.json').read_text())
        assert migrated['language']=='id' and migrated['language_hotkey']==121 and migrated['vocabulary']=='keep my terms'
        assert (profile/'Dictately/history.json').read_bytes()==(old/'history.json').read_bytes()
        assert (old/'settings.json').exists()
        selection=INSTALL/'models/selected-model.json';selection.write_text('{"model":"turbo"}')
        run([INSTALLER,'--silent','--no-launch','--model-cache',ROOT/'models/whisper-turbo'])
        assert (values(APP_KEY) or {})['InstallLocation'][0]==str(INSTALL)
        assert values(LEGACY_KEY) is None
        assert not any(path.exists() for path in LEGACY_SHORTCUTS)
        assert 'Transcribr' not in (values(RUN_KEY) or {})
        assert (values(RUN_KEY) or {})['Dictately'][0]=='"'+str(INSTALL/'Dictately.exe')+'"'
        assert json.loads(selection.read_text())['model']=='turbo'
        assert json.loads((profile/'Dictately/settings.json').read_text())==migrated
        results['legacy_upgrade_preserves_models_settings_startup']=True
        worker = INSTALL / "runtime/worker/DictatelyWorker.exe"
        result = run([worker, "--self-check"], env=environment)
        ready = json.loads(result.stdout.strip().splitlines()[-1])
        assert ready["ready"]
        results["frozen_backend"] = ready["device"]
        # Exercise frozen HTTPS/model repair with a small file, keeping weights cached.
        config = INSTALL / "models/whisper-turbo/config.json"
        digest = hashlib.sha256(config.read_bytes()).digest()
        assert config.resolve().is_relative_to(INSTALL.resolve())
        config.unlink()
        run([worker, "--install-model"], env=environment)
        assert hashlib.sha256(config.read_bytes()).digest() == digest
        results["frozen_https_model_repair"] = True
        print("Checking installed settings and native dictation", flush=True)
        settings = subprocess.Popen([str(INSTALL / "runtime/settings/DictatelySettings.exe")],
                                    env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(3)
        assert settings.poll() is None, "Bundled settings exited unexpectedly"
        close_settings(settings)
        settings.wait(timeout=10)
        assert settings.returncode == 0
        results["bundled_settings"] = True
        run([INSTALL/'Dictately.exe','--set-hotkeys','123','889'],env=environment)
        keys=json.loads((QA/'installed-data/settings.json').read_text())
        assert keys['dictation_hotkey']==123 and keys['language_hotkey']==889
        results['installed_shortcut_configuration']=True
        run([sys.executable, ROOT / "test_lifecycle.py", "--app-dir", INSTALL], cwd=ROOT)
        result = run([sys.executable, ROOT / "test_native.py", "--quick", "--app-dir", INSTALL], cwd=ROOT)
        desktop = json.loads(result.stdout)
        assert desktop["sessions"][0]["reference_word_recall"] == 1.0
        results["installed_native_dictation"] = True
        print('Checking packaged F10 picker, mouse, voice and custom shortcuts',flush=True)
        run([sys.executable,ROOT/'tests/check_language_picker.py','--app-dir',INSTALL],cwd=ROOT)
        results['installed_language_picker']=True
        (INSTALL / "keep-me.txt").write_text("User-added file; preserve on uninstall")
        print("Checking uninstall and user-file preservation", flush=True)
        run([INSTALL / "Uninstall Dictately.exe", "--silent"])
        deadline = time.monotonic() + 30
        while (INSTALL / "install-manifest.txt").exists():
            assert time.monotonic() < deadline, "Uninstaller cleanup did not finish"
            time.sleep(.1)
        assert not (INSTALL / "Dictately.exe").exists()
        assert not (INSTALL / "models").exists()
        assert (INSTALL / "keep-me.txt").read_text() == "User-added file; preserve on uninstall"
        assert values(APP_KEY) is None
        results["uninstall_preserves_user_files"] = True
        (QA / "installer-verification.json").write_text(json.dumps(results, indent=2))
        print(json.dumps(results, indent=2), flush=True)
    finally:
        if settings and settings.poll() is None:
            settings.terminate(); settings.wait(timeout=10)
        # Stop only our own app via its class-checked native quit helper.
        run([ROOT / "native/target/release/dictately.exe", "--quit-running"])
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if prior_run:
                winreg.SetValueEx(key, "Dictately", 0, prior_run[1], prior_run[0])
            else:
                try: winreg.DeleteValue(key, "Dictately")
                except FileNotFoundError: pass
            if prior_legacy_run:winreg.SetValueEx(key,'Transcribr',0,prior_legacy_run[1],prior_legacy_run[0])
            else:
                try:winreg.DeleteValue(key,'Transcribr')
                except FileNotFoundError:pass
        if prior_legacy_app is not None:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER,LEGACY_KEY) as key:
                for name,(value,kind) in prior_legacy_app.items():winreg.SetValueEx(key,name,0,kind,value)
        elif values(LEGACY_KEY) is not None:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER,LEGACY_KEY)
        for path,contents in prior_legacy_shortcuts.items():
            if contents is not None:path.write_bytes(contents)
            elif path.exists():path.unlink()
        if prior_app is not None:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, APP_KEY) as key:
                for name, (value, kind) in prior_app.items():
                    winreg.SetValueEx(key, name, 0, kind, value)
        if prior_shortcut is not None:
            SHORTCUT.write_bytes(prior_shortcut)
        elif SHORTCUT.exists():
            SHORTCUT.unlink()
        if prior_settings_shortcut is not None:SETTINGS_SHORTCUT.write_bytes(prior_settings_shortcut)
        elif SETTINGS_SHORTCUT.exists():SETTINGS_SHORTCUT.unlink()


if __name__ == "__main__":
    main()
