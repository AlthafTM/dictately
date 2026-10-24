"""Experimental macOS / Linux X11 frontend sharing Dictately's speech worker.

Run with the project-local desktop environment, not the Windows launcher.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import queue
import sys
import threading
import time

from runtime_paths import app_root, user_data_dir
from ui_preferences import POSITIONS, position_name, overlay_origin, desktop_microphones, resolve_microphone

ROOT = app_root()
DEFAULTS = {"language":"en", "cleanup":True, "accuracy":False, "vocabulary":"", "overlay_position":"bottom_center", "microphone":None}


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class PhraseBuffer:
    """Only one decode is in flight; preserve audio arriving during inference."""
    def __init__(self):
        self.samples = bytearray()
        self.sent = 0

    def append(self, pcm):
        self.samples.extend(pcm)

    def snapshot(self):
        self.sent = len(self.samples) // 2
        return bytes(self.samples)

    def consume(self, samples):
        if type(samples) is not int or not 0 <= samples <= self.sent:
            raise ValueError("Worker returned an invalid consumed sample count")
        del self.samples[:samples * 2]


class SpeechSession:
    """Reuse model weights, but reset phrase agreement after every new F12 session."""
    def __init__(self, model_root, factory=None):
        self.model_root=model_root; self.factory=factory; self.worker=None; self.session=None

    def recognize(self, token, request):
        if self.worker is None:
            if self.factory is None:
                from live_worker import Worker
                self.factory=Worker
            self.worker=self.factory(model_root=self.model_root)
        if token!=self.session:
            self.worker.process({'command':'prepare','id':0})
            self.session=token
        return self.worker.process(request)


def install_model(name):
    """Download to user storage, never inside a read-only launcher bundle."""
    from huggingface_hub import HfApi, snapshot_download
    from speech_models import MODELS
    directory, repo = MODELS[name]
    revision = HfApi().model_info(repo).sha
    root = user_data_dir()
    destination = root / "models" / directory
    snapshot_download(repo_id=repo, revision=revision, local_dir=destination,
                      allow_patterns=["model.bin", "config.json", "tokenizer.json", "vocabulary.json", "vocabulary.txt", "preprocessor_config.json"])
    for required in ("model.bin", "config.json", "tokenizer.json"):
        if not (destination / required).is_file():
            raise RuntimeError(f"Incomplete model download: {required}")
    if not any((destination/name).is_file() for name in ('vocabulary.json','vocabulary.txt')):
        raise RuntimeError('Incomplete model download: vocabulary is missing')
    write_json(destination / "source.json", {"repo":repo, "revision":revision})
    write_json(root / "models/selected-model.json", {"model":name})
    print(f"Selected {name}; dictation works offline after setup")


def start_hotkey(toggle, cancel):
    """Reserve F12 on X11 so it doesn't also open browser developer tools."""
    from pynput import keyboard
    if sys.platform == "darwin":
        from Quartz import kCGEventKeyUp
        def intercept(kind, event):
            from Quartz import CGEventGetIntegerValueField, kCGKeyboardEventKeycode
            if CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode) == 111:  # F12
                if kind == kCGEventKeyUp:
                    toggle()
                return None
            return event
        listener = keyboard.Listener(on_release=lambda key: cancel() if key == keyboard.Key.esc else None,
                                     darwin_intercept=intercept)
        listener.start()
        return [listener]
    from Xlib import X, XK, display
    import select
    ready = threading.Event()
    errors = []
    def x11():
        connection = display.Display()
        root = connection.screen().root
        keycode = connection.keysym_to_keycode(XK.string_to_keysym("F12"))
        for modifiers in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
            root.grab_key(keycode, modifiers, False, X.GrabModeAsync, X.GrabModeAsync,
                          onerror=lambda error, request: errors.append(str(error)))
        connection.sync()
        ready.set()
        if errors:
            connection.close()
            return
        held = False
        try:
            while True:
                select.select([connection], [], [], .05)
                while connection.pending_events():
                    event = connection.next_event()
                    if event.type == X.KeyPress and event.detail == keycode and not held:
                        held = True
                        toggle()
                # Query physical state to reject X11 auto-repeat release/press pairs.
                keys = connection.query_keymap()
                if not keys[keycode // 8] & (1 << (keycode % 8)):
                    held = False
        finally:
            connection.close()
    thread = threading.Thread(target=x11, daemon=True)
    thread.start()
    if not ready.wait(5) or errors:
        raise RuntimeError("Cannot reserve F12; remove the conflicting desktop shortcut and restart")
    listener = keyboard.Listener(on_release=lambda key: cancel() if key == keyboard.Key.esc else None)
    listener.start()
    return [thread, listener]


def run_desktop(smoke=False, probe=None):
    import numpy as np
    import sounddevice as sd
    from PySide6.QtCore import Qt, QTimer, QRectF, QLockFile, QObject, Signal
    from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QLinearGradient, QPainter, QPen
    from PySide6.QtWidgets import QApplication, QWidget, QSystemTrayIcon, QMenu, QMessageBox, QDialog, QVBoxLayout, QTextEdit, QPushButton, QFormLayout, QComboBox, QLabel

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Dictately")
    app.setWindowIcon(QIcon(str(ROOT / "assets/dictately.png")))
    app.setQuitOnLastWindowClosed(False)
    data = user_data_dir(); data.mkdir(parents=True, exist_ok=True)
    instance = QLockFile(str(data / "desktop.lock"))
    if not instance.tryLock(0):
        return 0
    logger = logging.getLogger("dictately.desktop")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(data / "desktop.log", maxBytes=1_000_000, backupCount=2)
    logger.addHandler(handler)

    class Waveform(QWidget):
        def __init__(self):
            super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
            self.setAttribute(Qt.WA_TranslucentBackground)
            self.setAttribute(Qt.WA_ShowWithoutActivating)
            self.resize(140, 42)
            self.progress = 0.; self.goal = 0.; self.level = 0.; self.phase = 0.
            self.last_frame=time.monotonic()
            self.position='bottom_center'
            self.timer = QTimer(self); self.timer.timeout.connect(self.animate)
        def open(self):
            screen = app.screenAt(__import__('PySide6.QtGui', fromlist=['QCursor']).QCursor.pos()) or app.primaryScreen()
            bounds = screen.availableGeometry()
            self.move(*overlay_origin((bounds.x(),bounds.y(),bounds.width(),bounds.height()),(self.width(),self.height()),22,self.position))
            self.goal = 1.; self.last_frame=time.monotonic(); self.show(); self.timer.start(16)
        def close_wave(self):
            self.goal = 0.; self.timer.start(16)
        def animate(self):
            now=time.monotonic();dt=min(now-self.last_frame,.05);self.last_frame=now
            self.progress += (self.goal - self.progress) * (1.-math.exp(-dt/.086))
            self.phase += dt*4.0625
            if self.goal == 0 and self.progress < .005:
                self.hide(); self.timer.stop()
            self.update()
        def paintEvent(self, event):
            painter = QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
            painter.setOpacity(self.progress)
            width = 124 * self.progress
            panel = QRectF((self.width()-width)/2, 5, width, 32)
            fill = QLinearGradient(0, 5, 0, 37)
            fill.setColorAt(0, QColor(58,58,58,240)); fill.setColorAt(1, QColor(14,14,14,235))
            outline = QLinearGradient(0,0,140,42)
            at = (math.sin(self.phase*.5)+1)/2
            outline.setColorAt(0,QColor(80,80,80)); outline.setColorAt(.15+.7*at,QColor(235,235,235)); outline.setColorAt(1,QColor(90,90,90))
            painter.setBrush(fill); painter.setPen(QPen(outline,1.1)); painter.drawRoundedRect(panel,16,16)
            painter.setPen(Qt.NoPen)
            for i in range(5):
                height = (5 + min(17,self.level*230)*(0.5+.5*math.sin(self.phase*2+i*.9))) * self.progress
                painter.setBrush(QColor(235,235,235))
                painter.drawRoundedRect(QRectF(51+i*8,21-height/2,3.5,height),1.75,1.75)

    class ControlQueue(QObject):
        wake=Signal()
        def __init__(self):
            super().__init__();self.queue=queue.Queue()
        def put(self,event):
            self.queue.put(event);self.wake.emit()
        def get_nowait(self):return self.queue.get_nowait()

    class Controller:
        def __init__(self):
            self.settings = {**DEFAULTS, **read_json(data / "desktop-settings.json", {})}
            if self.settings['language'] not in ('en','id'): self.settings['language'] = 'en'
            self.history = read_json(data / "desktop-history.json", [])
            self.events = ControlQueue(); self.audio = queue.Queue(maxsize=400)
            self.events.wake.connect(self.tick,Qt.QueuedConnection)
            self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="speech")
            self.future = None; self.speech = SpeechSession(data); self.stream = None
            self.recording = False; self.finishing = False; self.starting = False
            self.mic_closing = False
            self.screenshot_paused=False;self.screenshot_resume=False;self.screenshot_process=None;self.screenshot_started=0.
            self.session = 0; self.request = 0; self.target = None; self.blocked = False
            self.buffer = PhraseBuffer(); self.transcript = ''; self.started = 0.; self.last_request = 0.
            self.last_toggle = 0.; self.level = 0.; self.last_audio = 0.; self.last_voice = 0.; self.last_injected = ''
            self.window = Waveform(); self.history_dialog = None
            self.tray = QSystemTrayIcon(QIcon(str(ROOT / "assets/dictately-tray.png")), app)
            self.tray.setToolTip("Dictately · F12 to dictate")
            self.menu = QMenu(); self.group = QActionGroup(self.menu); self.group.setExclusive(True)
            for title, language in [('English only','en'),('Indonesian only','id')]:
                action = QAction(title,self.menu); action.setCheckable(True); action.setChecked(self.settings['language']==language)
                action.triggered.connect(lambda checked, code=language:self.language(code))
                self.group.addAction(action); self.menu.addAction(action)
            self.menu.addSeparator()
            self.menu.addAction('History / copy transcript', self.show_history)
            self.menu.addAction('Audio & appearance', self.show_preferences)
            self.menu.addAction('Cancel dictation', self.cancel)
            self.menu.addSeparator(); self.menu.addAction('Quit', self.quit)
            self.tray.setContextMenu(self.menu); self.tray.show()
            self.input = None; self.hooks = []
            if not smoke:
                from desktop_support import DesktopInput
                self.input = DesktopInput()
                from speech_models import selected_model
                directory = selected_model(data)
                if not (data / 'models' / directory / 'model.bin').exists():
                    raise RuntimeError('Speech model is missing. Run launchers/macos/Install Dictately.command or scripts/desktop/install-desktop.sh again')
                self.hooks = start_hotkey(lambda:self.events.put('toggle'),lambda:self.events.put('cancel'))
            self.timer = QTimer(); self.timer.timeout.connect(self.tick); self.timer.start(1000)
        def language(self, code):
            self.settings['language']=code; write_json(data / 'desktop-settings.json',self.settings)
        def notice(self, text):
            logger.warning(text)
            self.tray.showMessage('Dictately', text, QSystemTrayIcon.Warning, 7000)
        def toggle(self):
            if time.monotonic()-self.last_toggle < .4: return
            self.last_toggle=time.monotonic()
            if self.screenshot_paused:
                self.screenshot_resume=False
                if self.stream:
                    stream=self.stream;self.stream=None;self.close_mic(stream)
                return
            if self.finishing: return
            if self.recording or self.starting:
                self.stop(); return
            if self.future is not None or self.mic_closing:
                self.notice('The previous session is finishing; try F12 again in a moment');return
            self.target = self.input.destination()
            self.session += 1; self.request=0; self.buffer=PhraseBuffer(); self.transcript=''; self.last_injected=''
            self.blocked=False; self.session_settings=dict(self.settings)
            self.started=time.monotonic(); self.last_voice=self.started; self.last_request=0.; self.starting=True
            self.timer.setInterval(40)
            self.window.position=self.settings.get('overlay_position','bottom_center')
            self.window.open()
            token=self.session
            # PortAudio device opening and model loading never block the UI thread.
            def open_mic():
                def callback(indata, frames, timing, status):
                    try: self.audio.put_nowait((token, bytes(indata), bool(status)))
                    except queue.Full: self.events.put('overflow')
                preference=self.session_settings.get('microphone')
                device=resolve_microphone(preference,desktop_microphones(sd)) if preference else None
                stream=sd.RawInputStream(device=device,samplerate=16000,channels=1,dtype='int16',blocksize=1600,callback=callback)
                try: stream.start()
                except Exception:
                    stream.close(); raise
                return (token, stream)
            self.future=self.pool.submit(open_mic)
        def stop(self):
            self.recording=False; self.starting=False; self.finishing=True
            self.window.close_wave()
            if self.stream:
                stream=self.stream; self.stream=None
                self.close_mic(stream)
        def close_mic(self, stream):
            self.mic_closing=True
            def close():
                try:
                    try:stream.stop()
                    finally:stream.close()
                except Exception:
                    logger.exception('Microphone shutdown failed')
                finally:
                    self.events.put('mic_stopped')
            threading.Thread(target=close,daemon=True).start()
        def cancel(self):
            if not (self.recording or self.starting or self.finishing):return
            self.stop(); self.session += 1; self.finishing=False; self.buffer=PhraseBuffer()
            if self.transcript:self.save_history()
        def tick(self):
            try:self._tick()
            except Exception as error:
                logger.exception('Desktop action failed');self.notice(str(error));self.cancel()
        def _tick(self):
            for _ in range(40):
                try:event=self.events.get_nowait()
                except queue.Empty:break
                if event=='toggle':self.toggle()
                elif event=='cancel':self.cancel()
                elif event=='mic_stopped':self.mic_closing=False
                elif event=='overflow':raise RuntimeError('Microphone audio queue overflow; try a smaller model')
            for _ in range(100):
                try:token,pcm,dropout=self.audio.get_nowait()
                except queue.Empty:break
                if token!=self.session or self.screenshot_paused:continue
                if dropout:raise RuntimeError('Audio device dropped samples; check the microphone')
                self.buffer.append(pcm)
                samples=np.frombuffer(pcm,dtype='<i2').astype(np.float32)/32768
                self.level=float(np.sqrt(np.mean(samples*samples)))
                self.last_audio=time.monotonic()
                if self.level>.004:self.last_voice=self.last_audio
            self.window.level += ((self.level if time.monotonic()-self.last_audio<.3 else 0)-self.window.level)*.3
            if self.future and self.future.done():
                future=self.future;self.future=None
                token,result=future.result()
                if not getattr(future,'speech',False):
                    stream=result
                    if self.starting and token==self.session:
                        self.stream=stream;self.starting=False;self.recording=True
                    else:
                        self.close_mic(stream)
                elif token==self.session:
                    self.buffer.consume(result.get('consumed',0))
                    if result.get('action')=='screenshot':
                        self.screenshot_resume=self.recording
                        self.screenshot_paused=True;self.screenshot_started=time.monotonic()
                        self.recording=False;self.finishing=False;self.buffer=PhraseBuffer()
                        self.window.timer.stop();self.window.hide();self.window.progress=0.;self.window.goal=0.
                        QTimer.singleShot(0,lambda token=self.session:self.take_screenshot(token))
                    text=result.get('cleaned','').strip()
                    if text:
                        prefix=' ' if self.transcript else ''
                        self.transcript+=prefix+text
                        # Lose focus once: never resume insertion into a different destination.
                        if not self.blocked:
                            try:self.input.paste(self.target,prefix+text)
                            except Exception as error:self.blocked=True;self.notice(str(error))
                    if self.finishing and not self.mic_closing and not self.buffer.samples:
                        self.finishing=False;self.save_history()
            if self.recording and time.monotonic()-self.started>=120:self.stop()
            if (self.recording or self.finishing) and not self.mic_closing and self.future is None:
                count=len(self.buffer.samples)//2
                if self.finishing and not count:
                    self.finishing=False;self.save_history()
                elif count and (self.finishing or count>=(6400 if not self.transcript else 32000) and time.monotonic()-self.last_request>=(.25 if not self.transcript else 1.4)):
                    from numpy import frombuffer
                    self.request+=1;self.last_request=time.monotonic()
                    request={**self.session_settings,'command':'recognize','id':self.request,
                             'samples':frombuffer(self.buffer.snapshot(),dtype='<i2'),'final':self.finishing,
                             'flush':time.monotonic()-self.last_voice>.9,'pause_seconds':time.monotonic()-self.last_voice}
                    token=self.session
                    self.future=self.pool.submit(lambda req=request,session=token:(session,self.speech.recognize(session,req)))
                    self.future.speech=True
            interval=40 if self.recording or self.starting or self.finishing or self.mic_closing or self.future or self.screenshot_paused else 1000
            if self.timer.interval()!=interval:self.timer.setInterval(interval)
        def take_screenshot(self,token):
            if token!=self.session or self.recording:return
            if self.window.isVisible():
                QTimer.singleShot(50,lambda:self.take_screenshot(token));return
            try:
                from screen_capture import launch_screenshot
                self.screenshot_process=launch_screenshot()
                QTimer.singleShot(100,self.finish_screenshot)
            except (OSError,RuntimeError) as error:
                self.notice(str(error));self.finish_screenshot()
        def finish_screenshot(self):
            if self.screenshot_process and self.screenshot_process.poll() is None:
                QTimer.singleShot(100,self.finish_screenshot);return
            self.screenshot_process=None;self.screenshot_paused=False;self.buffer=PhraseBuffer()
            while True:
                try:self.audio.get_nowait()
                except queue.Empty:break
            if self.screenshot_resume and self.stream:
                self.started+=time.monotonic()-self.screenshot_started
                self.recording=True;self.last_voice=time.monotonic();self.level=0.;self.window.level=0.;self.window.open()
            self.screenshot_resume=False
        def save_history(self):
            if self.transcript:
                self.history.insert(0,{'at':datetime.now().isoformat(timespec='seconds'),'language':self.session_settings['language'],'text':self.transcript})
                self.history=self.history[:50];write_json(data / 'desktop-history.json',self.history)
                self.transcript=''
        def show_history(self):
            if self.recording or self.starting or self.finishing:
                self.notice('Finish dictation before opening History');return
            dialog=QDialog();dialog.setWindowTitle('Dictately history');dialog.resize(600,400)
            layout=QVBoxLayout(dialog);text=QTextEdit();text.setReadOnly(True)
            text.setPlainText('\n\n'.join(f"{x['at']} [{x['language']}]\n{x['text']}" for x in self.history))
            layout.addWidget(text)
            button=QPushButton('Copy latest transcript');button.clicked.connect(lambda:app.clipboard().setText(self.history[0]['text'] if self.history else ''))
            layout.addWidget(button);clear=QPushButton('Clear history')
            def clear_history():
                self.history=[];write_json(data / 'desktop-history.json',[]);text.clear()
            clear.clicked.connect(clear_history);layout.addWidget(clear)
            self.history_dialog=dialog;dialog.show();dialog.raise_();dialog.activateWindow()
        def show_preferences(self):
            if self.recording or self.starting or self.finishing:
                self.notice('Finish dictation before changing audio preferences');return
            if getattr(self,'preferences_dialog',None) and self.preferences_dialog.isVisible():
                self.preferences_dialog.raise_();self.preferences_dialog.activateWindow();return
            dialog=QDialog();dialog.setWindowTitle('Dictately · Audio & appearance');dialog.setMinimumWidth(440)
            layout=QVBoxLayout(dialog);layout.setContentsMargins(24,24,24,24);layout.setSpacing(14)
            form=QFormLayout();form.setSpacing(14)
            microphones=QComboBox();positions=QComboBox()
            for label,code in POSITIONS.items():positions.addItem(label,code)
            positions.setCurrentText(position_name(self.settings.get('overlay_position')))
            form.addRow('Microphone',microphones);form.addRow('Waveform position',positions);layout.addLayout(form)
            feedback=QLabel('Choose a default microphone and where the waveform appears');feedback.setWordWrap(True);layout.addWidget(feedback)
            refresh=QPushButton('Refresh microphones');save=QPushButton('Save preferences')
            layout.addWidget(refresh);layout.addWidget(save)
            selected=self.settings.get('microphone')
            def load_microphones():
                preference=microphones.currentData() if microphones.count() else selected
                refresh.setEnabled(False);save.setEnabled(False)
                future=self.pool.submit(lambda:desktop_microphones(sd))
                def loaded():
                    if not dialog.isVisible():return
                    if not future.done():QTimer.singleShot(50,loaded);return
                    microphones.clear();microphones.addItem('System default',None)
                    try:
                        devices=future.result()
                        for device in devices:
                            identity={'name':device['name'],'host':device['host']}
                            microphones.addItem(f"{device['name']} ({device['host']}, {device['index']+1})",identity)
                        index=next((i for i in range(microphones.count()) if microphones.itemData(i)==preference),-1)
                        if index<0:
                            microphones.addItem('Saved microphone (unavailable)',preference);index=microphones.count()-1
                        microphones.setCurrentIndex(index)
                        feedback.setText('Preferences apply to your next recording')
                    except Exception as error:
                        if preference:microphones.addItem('Saved microphone (unavailable)',preference);microphones.setCurrentIndex(1)
                        feedback.setText(str(error))
                    refresh.setEnabled(True);save.setEnabled(True)
                QTimer.singleShot(50,loaded)
            def save_preferences():
                self.settings.update(microphone=microphones.currentData(),overlay_position=positions.currentData())
                write_json(data/'desktop-settings.json',self.settings);dialog.accept()
            refresh.clicked.connect(load_microphones);save.clicked.connect(save_preferences)
            self.preferences_dialog=dialog;dialog.show();dialog.raise_();dialog.activateWindow();load_microphones()
        def quit(self):
            self.cancel()
            for hook in self.hooks:
                if hasattr(hook,'stop'):hook.stop()
            self.pool.shutdown(wait=False,cancel_futures=True)
            self.tray.hide();app.quit()

    try:
        controller=Controller()
        if probe is not None:
            probe(controller,app)
        elif smoke:
            controller.window.open()
            if os.environ.get('DICTATELY_SMOKE_IMAGE'):
                QTimer.singleShot(250,lambda:controller.window.grab().save(os.environ['DICTATELY_SMOKE_IMAGE']))
            QTimer.singleShot(300,controller.window.close_wave)
            QTimer.singleShot(650,app.quit)
        app.exec()
    except Exception as error:
        logger.exception('Startup failed')
        QMessageBox.critical(None,'Dictately',str(error))
        return 1
    finally:
        handler.close()
    return 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-model',choices=['small','turbo','large-v3'])
    parser.add_argument('--install-launcher',action='store_true')
    parser.add_argument('--startup',action='store_true')
    parser.add_argument('--smoke-test',action='store_true',help='GUI only; no microphone, hotkey or typing')
    args=parser.parse_args()
    if args.install_model:
        install_model(args.install_model);return 0
    if args.install_launcher:
        from desktop_support import install_launchers
        print(install_launchers(ROOT,sys.executable,args.startup));return 0
    return run_desktop(args.smoke_test)


if __name__=='__main__':
    sys.exit(main())
