"""Isolated microphone/overlay responsiveness check; never inserts text."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "qa/startup"
DATA.mkdir(parents=True, exist_ok=True)
user = ctypes.WinDLL("user32", use_last_error=True)
user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user.IsWindowVisible.argtypes = [wintypes.HWND]
user.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                   wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
user.SendMessageTimeoutW.restype = wintypes.LPARAM


def status():
    try:
        return json.loads((DATA / "status.json").read_text())
    except (OSError, ValueError):
        return {}


def main():
    baseline = "--baseline" in sys.argv
    environment = dict(os.environ, LOCALFLOW_DATA=str(DATA), DICTATELY_TEST_MIC_DELAY_MS="600")
    process = subprocess.Popen([str(ROOT / "Dictately.exe"), "--test-target", "0"],
                               env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline = time.monotonic() + 20
        while status().get("pid") != process.pid:
            assert time.monotonic() < deadline, "Controller did not start"
            time.sleep(.02)
        hwnd = status()["hwnd"]
        rows = []
        for cycle in range(2):
            start = time.perf_counter()
            user.PostMessageW(hwnd, 0x8001, 0, 0)
            visible = None
            ready = None
            ping_times = []
            timeouts = 0
            while time.perf_counter() - start < 5:
                time.sleep(.008)
                begin = time.perf_counter()
                result = ctypes.c_size_t()
                ok = user.SendMessageTimeoutW(hwnd, 0, 0, 0, 2, 25, ctypes.byref(result))
                ping_times.append((time.perf_counter() - begin) * 1000)
                timeouts += not bool(ok)
                current = status()
                elapsed = (time.perf_counter() - start) * 1000
                if visible is None and user.IsWindowVisible(hwnd):
                    visible = elapsed
                if current.get("state") == "recording" and current.get("microphone_ready", baseline):
                    ready = elapsed
                    break
            assert visible is not None and ready is not None, current
            row = {"cycle": cycle, "overlay_visible_ms": round(visible, 1),
                   "microphone_ready_ms": round(ready, 1), "ui_ping_max_ms": round(max(ping_times), 1),
                   "ui_ping_timeouts": timeouts, "audio_samples": current.get("audio_samples")}
            rows.append(row)
            if not baseline:
                assert visible < 250, row
                assert timeouts == 0, row
                time.sleep(.35)
            user.PostMessageW(hwnd, 0x8002, 0, 0)
            deadline = time.monotonic() + 4
            while user.IsWindowVisible(hwnd) or status().get("state") != "ready":
                assert time.monotonic() < deadline, "Cancel did not finish"
                time.sleep(.02)
            if not baseline:
                row["audio_samples_after_cancel"] = status().get("audio_samples", 0)
                assert row["audio_samples_after_cancel"] >= 3200, row
        if not baseline:
            # Cancel while the driver is still opening, immediately start again,
            # and check that the old completion cannot bind to the new recording.
            user.PostMessageW(hwnd, 0x8001, 0, 0)
            deadline = time.monotonic() + 2
            while not status().get("microphone_starting"):
                assert time.monotonic() < deadline, "Pending start was not visible"
                time.sleep(.01)
            user.PostMessageW(hwnd, 0x8002, 0, 0)
            while status().get("state") != "ready":
                assert time.monotonic() < deadline, "Pending start could not cancel"
                time.sleep(.01)
            user.PostMessageW(hwnd, 0x8001, 0, 0)
            restarted = time.monotonic()
            deadline = restarted + 8
            while True:
                time.sleep(.01)
                current = status()
                if current.get("state") == "recording" and current.get("microphone_ready"):
                    assert time.monotonic() - restarted >= .5, "Stale microphone attached to new session"
                    break
                assert time.monotonic() < deadline, "Restart did not acquire microphone"
                result = ctypes.c_size_t()
                assert user.SendMessageTimeoutW(hwnd, 0, 0, 0, 2, 25, ctypes.byref(result)), "UI stalled during restart"
            # Exercise regular stop as well as cancel, including asynchronous tail delivery.
            time.sleep(.35)
            user.PostMessageW(hwnd, 0x8001, 0, 0)
            deadline = time.monotonic() + 25
            saw_processing = False
            while True:
                current = status()
                saw_processing |= current.get("state") == "processing"
                if saw_processing and current.get("state") == "ready" and not current.get("microphone_stopping"):
                    break
                assert time.monotonic() < deadline, "Stop did not finalize"
                time.sleep(.02)
            assert current.get("audio_samples", 0) >= 3200, current
            rows.append({"cancel_while_opening": True, "restart_uses_current_session": True,
                         "regular_stop_finalized": True})
        print(json.dumps(rows, indent=2), flush=True)
        (DATA / ("baseline.json" if baseline else "verification.json")).write_text(json.dumps(rows, indent=2))
    finally:
        if status().get("pid") == process.pid:
            user.PostMessageW(status()["hwnd"], 0x8004, 0, 0)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    main()
