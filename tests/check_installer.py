"""Install into an isolated folder, exercise frozen runtime, and uninstall.

This manages the current user's Transcribr shortcut/registry entries temporarily;
they are backed up and restored. Run with no dictation session in progress.
"""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import winreg

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / "qa"
INSTALL = QA / ("installer smoke " + uuid.uuid4().hex[:8])
INSTALLER = ROOT / "dist/Transcribr-0.6.0-Setup.exe"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Transcribr"
SHORTCUT = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Transcribr.lnk"


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
    prior_run = (values(RUN_KEY) or {}).get("Transcribr")
    prior_app = values(APP_KEY)
    prior_shortcut = SHORTCUT.read_bytes() if SHORTCUT.exists() else None
    environment = dict(os.environ, TRANSCRIBR_ROOT=str(INSTALL), LOCALFLOW_DATA=str(QA / "installed-data"))
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
        results["installed_without_system_python"] = True
        worker = INSTALL / "runtime/worker/TranscribrWorker.exe"
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
        settings = subprocess.Popen([str(INSTALL / "runtime/settings/TranscribrSettings.exe")],
                                    env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(3)
        assert settings.poll() is None, "Bundled settings exited unexpectedly"
        close_settings(settings)
        settings.wait(timeout=10)
        assert settings.returncode == 0
        results["bundled_settings"] = True
        run([sys.executable, ROOT / "test_lifecycle.py", "--app-dir", INSTALL], cwd=ROOT)
        result = run([sys.executable, ROOT / "test_native.py", "--quick", "--app-dir", INSTALL], cwd=ROOT)
        desktop = json.loads(result.stdout)
        assert desktop["sessions"][0]["reference_word_recall"] == 1.0
        results["installed_native_dictation"] = True
        (INSTALL / "keep-me.txt").write_text("User-added file; preserve on uninstall")
        print("Checking uninstall and user-file preservation", flush=True)
        run([INSTALL / "Uninstall Transcribr.exe", "--silent"])
        deadline = time.monotonic() + 30
        while (INSTALL / "install-manifest.txt").exists():
            assert time.monotonic() < deadline, "Uninstaller cleanup did not finish"
            time.sleep(.1)
        assert not (INSTALL / "Transcribr.exe").exists()
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
        run([ROOT / "native/target/release/transcribr.exe", "--quit-running"])
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if prior_run:
                winreg.SetValueEx(key, "Transcribr", 0, prior_run[1], prior_run[0])
            else:
                try: winreg.DeleteValue(key, "Transcribr")
                except FileNotFoundError: pass
        if prior_app is not None:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, APP_KEY) as key:
                for name, (value, kind) in prior_app.items():
                    winreg.SetValueEx(key, name, 0, kind, value)
        if prior_shortcut is not None:
            SHORTCUT.write_bytes(prior_shortcut)
        elif SHORTCUT.exists():
            SHORTCUT.unlink()


if __name__ == "__main__":
    main()
