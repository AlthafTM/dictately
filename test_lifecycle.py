"""Native overlay/microphone lifecycle smoke test; no keystrokes are inserted."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import time

root = Path(__file__).resolve().parent
data = root / "qa/lifecycle"
data.mkdir(exist_ok=True)
user = ctypes.WinDLL("user32")
user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user.IsWindowVisible.argtypes = [wintypes.HWND]
process = subprocess.Popen([str(root / "Transcribr.exe"), "--test-target", "0"],
    env=dict(os.environ, LOCALFLOW_DATA=str(data), LOCALFLOW_PREVIEW_DIR=str(root / "qa")), creationflags=subprocess.CREATE_NO_WINDOW)
def status():
    try:
        return json.loads((data / "status.json").read_text())
    except (ValueError, OSError):
        return {}
try:
    deadline = time.monotonic() + 15
    while status().get("pid") != process.pid:
        assert time.monotonic() < deadline, "Controller did not start"
        time.sleep(.1)
    hwnd = status()["hwnd"]
    assert not user.IsWindowVisible(hwnd), "Overlay visible while idle"
    user.PostMessageW(hwnd, 0x8001, 0, 0)
    deadline = time.monotonic()+10
    while status().get("state") != "recording" and time.monotonic() < deadline:
        time.sleep(.05)
    assert status()["state"] == "recording", status()
    assert user.IsWindowVisible(hwnd), "Overlay missing while recording"
    time.sleep(.35)
    from test_native import screenshot
    screenshot(hwnd, root / "qa/start-overlay.png")
    time.sleep(1.0)
    screenshot(hwnd, root / "qa/steady-overlay.png")
    user.PostMessageW(hwnd, 0x8002, 0, 0)
    deadline = time.monotonic()+5
    while status().get("state") != "ready" and time.monotonic()<deadline:
        time.sleep(.01)
    assert status()["state"] == "ready", status()
    assert user.IsWindowVisible(hwnd), "Stop confirmation missing"
    screenshot(hwnd, root / "qa/stop-overlay.png")
    deadline = time.monotonic()+2
    while user.IsWindowVisible(hwnd) and time.monotonic()<deadline:
        time.sleep(.02)
    assert not user.IsWindowVisible(hwnd), "Overlay/background remains after fade"
    result = {"idle_hidden": True, "recording_visible": True, "cancel_ready": True,
        "hidden_after_animation": True, "audio_saved": False, "typing_target": 0}
    (root / "qa/lifecycle-verification.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)
finally:
    if status().get("pid") == process.pid:
        user.PostMessageW(status()["hwnd"], 0x8004, 0, 0)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
