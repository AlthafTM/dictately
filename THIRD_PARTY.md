# Third-party software and models

Transcribr uses these independently licensed components. Source links are provided for complete terms; this document does not replace their licenses.

| Component | Purpose | License / source |
| --- | --- | --- |
| faster-whisper | Speech recognition wrapper | MIT; [source](https://github.com/SYSTRAN/faster-whisper), copy in `licenses/faster-whisper-LICENSE` |
| CTranslate2 | Local inference | MIT; [source](https://github.com/OpenNMT/CTranslate2) |
| OpenAI Whisper large-v3-turbo | Speech weights | MIT; [model](https://huggingface.co/openai/whisper-large-v3-turbo) |
| Whisper Small / Large-v3 conversions | Optional local speech weights | MIT; [Small](https://huggingface.co/Systran/faster-whisper-small), [Large-v3](https://huggingface.co/Systran/faster-whisper-large-v3) |
| huggingface-hub | Optional model download and revision resolution | Apache-2.0; [source](https://github.com/huggingface/huggingface_hub) |
| faster-whisper-large-v3-turbo conversion | Pinned model download | [conversion repository](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo); revision and checksums in `assets/speech-model.json` |
| windows-rs / windows-sys | Windows APIs | MIT or Apache-2.0; [source](https://github.com/microsoft/windows-rs) |
| Serde / serde_json | Local configuration and worker protocol | MIT or Apache-2.0; [source](https://github.com/serde-rs/serde) |
| NVIDIA CUDA libraries | Optional GPU inference | NVIDIA terms; [CUDA](https://developer.nvidia.com/cuda-toolkit), [cuDNN](https://developer.nvidia.com/cudnn) |
| Pillow | Development-only icon generation | HPND; [source](https://github.com/python-pillow/Pillow) |
| Python | Private installer runtime | PSF license; bundled runtime notices in `licenses/runtime` |
| PyInstaller | Build tooling and runtime bootloader | GPL with distribution exception; [license](https://pyinstaller.org/en/stable/license.html) |
| Qt / PySide6 Essentials | Experimental macOS / Linux desktop UI | LGPL-3.0 / GPL / commercial options; [licensing](https://doc.qt.io/qtforpython-6/licenses.html) |
| sounddevice / PortAudio | Experimental desktop microphone capture | MIT; [sounddevice](https://github.com/spatialaudio/python-sounddevice), [PortAudio](https://github.com/PortAudio/portaudio) |
| pynput | Experimental desktop hotkeys | LGPL-3.0; [source](https://github.com/moses-palmer/pynput) |
| PyObjC / python-xlib | macOS Accessibility / X11 integration | [PyObjC](https://github.com/ronaldoussoren/pyobjc), [python-xlib](https://github.com/python-xlib/python-xlib); installed package notices apply |

Python packages install into the recipient's private environment with their own notices. GPU libraries and model weights are not included in the Git repository or release ZIP. No rights to third-party software are granted beyond their respective licenses.

The phrase-confirmation design is informed by [Whisper-Streaming's local-agreement approach](https://github.com/ufal/whisper_streaming); Transcribr's controller and phrase handling are implemented in this repository.
