"""Cloud-only complete dictation flow: synthetic audio, real model and X11 input."""
from _support import ROOT
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch


def main():
    if sys.platform != 'linux' or not os.environ.get('GITHUB_ACTIONS'):
        raise RuntimeError('Run this test only on the Linux GitHub Actions runner')
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QLineEdit
    from faster_whisper.audio import decode_audio
    import numpy as np
    import desktop_app
    from desktop_support import DesktopInput

    samples = (np.clip(decode_audio(sys.argv[1], sampling_rate=16000), -1, 1)*32767).astype('<i2')
    pcm = samples.tobytes() + bytes(16000*2*2)
    failures, result = [], {}

    class FixtureStream:
        """Replace only the physical capture device; deliver timed PCM callbacks."""
        def __init__(self, **options):
            assert options['samplerate'] == 16000 and options['channels'] == 1
            self.callback = options['callback']
            self.stopped = threading.Event()

        def start(self):
            def feed():
                for offset in range(0, len(pcm), 3200):
                    if self.stopped.is_set():
                        break
                    block = pcm[offset:offset+3200]
                    self.callback(block, len(block)//2, None, False)
                    if self.stopped.wait(.1):
                        break
            self.thread = threading.Thread(target=feed, daemon=True)
            self.thread.start()

        def stop(self):
            self.stopped.set()
            self.thread.join(timeout=2)

        def close(self):
            self.stop()

    def probe(controller, app):
        field = QLineEdit()
        field.setWindowTitle('Dictately cloud dictation destination')
        field.resize(640, 80)
        field.show()
        controller.test_field = field
        controller.input = DesktopInput()
        controller.hooks = desktop_app.start_hotkey(
            lambda: controller.events.put('toggle'), lambda: controller.events.put('cancel'))
        controller.notice = lambda message: failures.append(AssertionError(message))
        started = time.monotonic()
        state = {'stopped': False}

        def begin():
            try:
                subprocess.run(['xdotool', 'windowactivate', '--sync', str(int(field.winId()))], check=True, timeout=5)
                field.setFocus()
                app.processEvents()
                subprocess.run(['xdotool', 'key', 'F12'], check=True, timeout=5)
            except Exception as error:
                failures.append(error)

        def poll():
            try:
                if failures:
                    raise failures[0]
                if time.monotonic()-started > 150:
                    raise AssertionError(f'Dictation timed out: text={field.text()!r}, recording={controller.recording}, finishing={controller.finishing}')
                words = field.text().lower()
                if not state['stopped'] and 'report' in words and 'tomorrow' in words:
                    assert controller.recording, 'Text must arrive before stopping capture'
                    assert not controller.blocked, 'The original destination lost focus'
                    result['live_text'] = field.text()
                    result['first_text_seconds'] = round(time.monotonic()-started, 2)
                    subprocess.run(['xdotool', 'key', 'F12'], check=True, timeout=5)
                    state['stopped'] = True
                elif state['stopped'] and not (controller.recording or controller.starting or controller.finishing):
                    assert controller.history and 'report' in controller.history[0]['text'].lower()
                    assert field.text().strip() == controller.history[0]['text'].strip()
                    assert controller.stream is None and not controller.mic_closing
                    result['final_text'] = field.text()
                    result['capture'] = 'synthetic PCM; no physical microphone'
                    result['checks'] = ['native shortcut start/stop', 'real CPU recognition', 'live X11 paste', 'history and finalization']
                    (ROOT/'qa/linux-dictation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
                    print('Complete Linux dictation flow passed:', result, flush=True)
                    controller.quit()
                    return
            except Exception as error:
                if not failures:
                    failures.append(error)
                controller.quit()
                return
            QTimer.singleShot(100, poll)

        QTimer.singleShot(700, begin)
        QTimer.singleShot(1000, poll)

    with patch('sounddevice.RawInputStream', FixtureStream):
        assert desktop_app.run_desktop(smoke=True, probe=probe) == 0
    if failures:
        raise failures[0]
    assert result.get('final_text'), 'No completed dictation'


if __name__ == '__main__':
    main()
