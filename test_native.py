"""Real native F12/live-insertion QA, isolated from the user's history and audio."""
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import time
import wave
import sys
import re
from collections import Counter

ROOT = Path(__file__).resolve().parent
QA = ROOT / "qa"
QA.mkdir(exist_ok=True)
u, g, k = c.WinDLL("user32", use_last_error=True), c.WinDLL("gdi32"), c.WinDLL("kernel32")
u.CreateWindowExW.restype = w.HWND
u.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, c.c_int, c.c_int, c.c_int, c.c_int, w.HWND, w.HMENU, w.HINSTANCE, c.c_void_p]
u.SetForegroundWindow.argtypes = [w.HWND]
u.SetFocus.argtypes = [w.HWND]
u.SetFocus.restype = w.HWND
u.GetForegroundWindow.restype = w.HWND
u.GetWindowTextLengthW.argtypes = [w.HWND]
u.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
u.SetWindowTextW.argtypes = [w.HWND, w.LPCWSTR]
u.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
u.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
u.DestroyWindow.argtypes = [w.HWND]
u.keybd_event.argtypes = [w.BYTE, w.BYTE, w.DWORD, c.c_size_t]

def read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

def text(hwnd):
    length = u.GetWindowTextLengthW(hwnd)
    value = c.create_unicode_buffer(length + 1)
    u.GetWindowTextW(hwnd, value, length + 1)
    return value.value

def pump():
    class MSG(c.Structure):
        _fields_ = [("hwnd", w.HWND), ("message", w.UINT), ("wp", w.WPARAM), ("lp", w.LPARAM), ("time", w.DWORD), ("point", w.POINT), ("private", w.DWORD)]
    message = MSG()
    while u.PeekMessageW(c.byref(message), None, 0, 0, 1):
        u.TranslateMessage(c.byref(message))
        u.DispatchMessageW(c.byref(message))

def f12():
    scan = u.MapVirtualKeyW(0x7B, 0)
    u.keybd_event(0x7B, scan, 0, 0)
    u.keybd_event(0x7B, scan, 2, 0)

def screenshot(hwnd, path):
    from PIL import Image
    frame = QA / "glass-frame.bgra"
    dimensions = read(frame.with_suffix(".json"))
    width, height = dimensions.get("width", 160), dimensions.get("height", 44)
    if frame.exists() and frame.stat().st_size == width * height * 4:
        import numpy as np
        data = np.frombuffer(frame.read_bytes(), dtype=np.uint8).reshape(height, width, 4).astype(np.float32)
        alpha = data[:, :, 3:4]
        rgb = np.clip(data[:, :, 2::-1] * 255 / np.maximum(alpha, 1), 0, 255)
        rgba = np.concatenate([rgb, alpha], axis=2).astype(np.uint8)
        Image.fromarray(rgba, "RGBA").save(path)
        return
    u.GetWindowDC.argtypes = [w.HWND]
    u.GetWindowDC.restype = w.HDC
    u.ReleaseDC.argtypes = [w.HWND, w.HDC]
    u.GetWindowRect.argtypes = [w.HWND, c.POINTER(w.RECT)]
    g.CreateCompatibleDC.argtypes = [w.HDC]
    g.CreateCompatibleDC.restype = w.HDC
    g.CreateCompatibleBitmap.argtypes = [w.HDC, c.c_int, c.c_int]
    g.CreateCompatibleBitmap.restype = w.HBITMAP
    g.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
    g.SelectObject.restype = w.HGDIOBJ
    g.BitBlt.argtypes = [w.HDC, c.c_int, c.c_int, c.c_int, c.c_int, w.HDC, c.c_int, c.c_int, w.DWORD]
    g.GetDIBits.argtypes = [w.HDC, w.HBITMAP, w.UINT, w.UINT, c.c_void_p, c.c_void_p, w.UINT]
    g.DeleteObject.argtypes = [w.HGDIOBJ]
    g.DeleteDC.argtypes = [w.HDC]
    rect = w.RECT()
    u.GetWindowRect(hwnd, c.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    dc = u.GetWindowDC(hwnd)
    memory = g.CreateCompatibleDC(dc)
    bitmap = g.CreateCompatibleBitmap(dc, width, height)
    old = g.SelectObject(memory, bitmap)
    g.BitBlt(memory, 0, 0, width, height, dc, 0, 0, 0x00CC0020)
    class HEADER(c.Structure):
        _fields_ = [("size", w.DWORD), ("width", w.LONG), ("height", w.LONG), ("planes", w.WORD), ("bits", w.WORD),
            ("compression", w.DWORD), ("image_size", w.DWORD), ("x", w.LONG), ("y", w.LONG), ("used", w.DWORD), ("important", w.DWORD)]
    info = HEADER(size=40, width=width, height=-height, planes=1, bits=32)
    buffer = c.create_string_buffer(width * height * 4)
    g.SelectObject(memory, old)
    g.GetDIBits(memory, bitmap, 0, height, buffer, c.byref(info), 0)
    Image.frombuffer("RGB", (width, height), buffer.raw, "raw", "BGRX", 0, 1).save(path)
    g.DeleteObject(bitmap); g.DeleteDC(memory); u.ReleaseDC(hwnd, dc)

def main():
    quick = "--quick" in sys.argv
    correction = "--correction" in sys.argv
    with wave.open(str(ROOT / ("test-speech.wav" if quick else "test-live.wav")), "rb") as wav:
        assert wav.getframerate() == 16000
        pcm = wav.readframes(wav.getnframes())
        duration = wav.getnframes() / 16000
    if correction:
        pcm = (QA / "correction.pcm").read_bytes()
        duration = len(pcm)/32000
    (QA / "speech.pcm").write_bytes(pcm)
    (QA / "settings.json").write_text(json.dumps({"language": "en", "cleanup": True}))
    environment = dict(os.environ, LOCALFLOW_DATA=str(QA), LOCALFLOW_PREVIEW_DIR=str(QA))
    executable = ROOT / "native/target/release/transcribr.exe"
    before = u.GetForegroundWindow()
    parent = u.CreateWindowExW(0, "STATIC", "Transcribr disposable native editor", 0x00CF0000, 100, 100, 750, 450, None, None, None, None)
    editor = u.CreateWindowExW(0x200, "EDIT", "", 0x503010C4, 20, 20, 690, 160, parent, None, None, None)
    editor2 = u.CreateWindowExW(0x200, "EDIT", "", 0x503010C4, 20, 210, 690, 160, parent, None, None, None)
    assert parent and editor and editor2
    process = subprocess.Popen([str(executable), "--test-pcm", str(QA / "speech.pcm"), "--test-target", str(parent)], env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
    results = {"audio_seconds": duration, "sessions": []}
    try:
        for _ in range(600):
            pump()
            status = read(QA / "status.json")
            if status.get("pid") == process.pid and status.get("state") == "ready" and status.get("engine_ready"):
                break
            time.sleep(.05)
        else:
            raise RuntimeError("Native controller did not become ready")
        u.ShowWindow(parent, 5)
        for index in range(1 if quick or correction else 2):
            if u.GetForegroundWindow() != parent:
                u.keybd_event(0x12, 0x38, 0, 0)
                u.keybd_event(0x12, 0x38, 2, 0)
                assert u.SetForegroundWindow(parent)
            u.SetWindowTextW(editor, "Existing text: ")
            u.SetWindowTextW(editor2, "Second field: ")
            u.SetFocus(editor)
            u.SendMessageW(editor, 0xB1, len("Existing text: "), len("Existing text: "))
            active_editor = editor
            switched = False
            f12()
            started = time.monotonic()
            captured = False
            session = {}
            stop_at = None
            start_picture = False
            stop_picture = False
            while time.monotonic() - started < duration + 60:
                pump()
                if u.GetForegroundWindow() != parent:
                    raise RuntimeError("Outside focus change interrupted the owned-editor test")
                status = read(QA / "status.json")
                content = text(editor)
                elapsed = time.monotonic() - started
                if elapsed > .35 and not start_picture:
                    screenshot(status["hwnd"], QA / "start-overlay.png")
                    start_picture = True
                if elapsed > duration - .25 and stop_at is None and status.get("state") == "recording":
                    f12()
                    stop_at = time.monotonic()
                if stop_at and status.get("state") != "recording" and "microphone_stop_seconds" not in session:
                    session["microphone_stop_seconds"] = round(time.monotonic()-stop_at, 3)
                if stop_at and time.monotonic()-stop_at > .25 and not stop_picture:
                    screenshot(status["hwnd"], QA / "stop-overlay.png")
                    stop_picture = True
                if len(content) > len("Existing text: ") and "first_text_seconds" not in session:
                    session["first_text_seconds"] = round(elapsed, 2)
                    session["text_arrived_while_recording"] = status.get("state") == "recording"
                if index == 1 and elapsed > 9 and len(content) > len("Existing text: ") and not switched:
                    active_editor = editor2
                    u.SetFocus(editor2)
                    u.SendMessageW(editor2, 0xB1, len("Second field: "), len("Second field: "))
                    switched = True
                if switched and active_editor == editor2 and elapsed > 13:
                    assert text(editor2) == "Second field: ", "Session redirected into another field"
                    active_editor = editor
                    u.SetFocus(editor)
                    u.SendMessageW(editor, 0xB1, len(text(editor)), len(text(editor)))
                if elapsed > (2 if quick else 5) and not captured and status.get("state") == "recording":
                    screenshot(status["hwnd"], QA / "live-overlay.png")
                    captured = True
                if elapsed > 1 and status.get("state") == "error":
                    raise RuntimeError((QA / "native.log").read_text())
                if elapsed > duration and status.get("state") == "ready":
                    session.update(text=content, second_field=text(editor2), commits=status.get("live_commits"), total_seconds=round(elapsed, 2),
                        overlay_kept_focus=u.GetForegroundWindow() == parent)
                    break
                time.sleep(.03)
            results["sessions"].append(session)
            if not quick:
                assert session.get("text_arrived_while_recording"), session
            assert session.get("microphone_stop_seconds", 99) < 1, session
            assert session.get("commits", 0) >= (1 if quick else 2), session
            assert "report" in session.get("text", "").lower(), session
            assert session.get("overlay_kept_focus"), session
            assert session.get("text", "").startswith("Existing text: "), session
            assert "[email]" not in session["text"].lower(), session
            assert "thank you for watching" not in (session["text"] + session["second_field"]).lower(), session
            if correction:
                assert session["text"].count("I would like") == 1 and "Monday" in session["text"] and "Friday" not in session["text"], session
            if index == 1:
                assert session["second_field"] == "Second field: ", session
            reference = "I would like to send the report on Monday." if correction else "I would like to send the report tomorrow." if quick else "I would like to send the report tomorrow. Please include the latest numbers and send a copy to the whole team. We should check the final details before the meeting on Thursday. This is a test of live dictation on my laptop. The text should appear while I am still speaking."
            expected = Counter(re.findall(r"\w+", reference.lower()))
            written = Counter(re.findall(r"\w+", (session["text"] + session["second_field"]).lower()))
            session["reference_word_recall"] = round(sum((expected & written).values()) / sum(expected.values()), 3)
            assert session["reference_word_recall"] >= .93, session
        (QA / "native-verification.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(json.dumps(results, indent=2))
    finally:
        status = read(QA / "status.json")
        if status.get("pid") == process.pid:
            u.PostMessageW(status["hwnd"], 0x8004, 0, 0)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        u.DestroyWindow(parent)
        u.SetForegroundWindow(before)

if __name__ == "__main__":
    main()
