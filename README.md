# Transcribr

<p align="center">
  <img src="assets/transcribr.png" width="112" alt="Transcribr waveform icon">
</p>

**Local voice typing for Windows — press F12, speak, and type into the app you are using**

Transcribr turns **English** or **Indonesian** speech into text in your focused text box. Recognition runs locally on your computer using Whisper large-v3-turbo by default. There is no account, subscription, or paid transcription API, and your speech is not sent to a cloud transcription service. After the initial downloads, dictation works offline. A small animated waveform near the bottom of the screen shows when recording is active.

**Currently supported languages: English and Indonesian**. Additional language support may be added in the future; it is not available yet and has no scheduled release date.

[Installation](#download-and-install) · [Usage](#how-to-use) · [Local AI model options](docs/AI-MODELS.md) · [Releases](https://github.com/AlthafTM/transcribr/releases)

See the [strengths, weaknesses, and Wispr Flow comparison report](docs/WISPR-FLOW-COMPARISON.md) for current tradeoffs, measured optimizations, and suggested next work.

## Features

- **English** or **Indonesian** — select one language in the Windows notification-area menu; it stays fixed for the recording
- **Voice typing across apps** — dictate into supported browser, messaging, document, and other editable fields
- **Live phrase output** — text arrives in short phrases while speaking; pauses and stopping finish the remaining words
- **Spoken corrections** — repeat a recent sentence with a small change to replace it, or say `Correction, Tuesday` / `Ralat, Selasa` to correct the last dictated word
- **Light cleanup** — optional removal of hesitation sounds and repeated pronouns
- **Personal vocabulary** — add names or technical terms as recognition hints
- **No automatic full stops** — sentence/session-ending periods are removed; internal dots in numbers, filenames, and URLs stay intact
- **GPU acceleration** — CUDA INT8/FP16 on compatible NVIDIA hardware, with a slower CPU INT8 fallback
- **Local model choices** — keep Turbo, switch to multilingual Small, or try full Large-v3 using the [step-by-step guide](docs/AI-MODELS.md)
- **Quiet desktop UI** — native Rust controller, text-free waveform, smooth opening/closing animations, and a monochrome glass effect
- **Optional startup** — launch quietly when you sign in to Windows
- **Local history** — retain up to 50 original and cleaned transcripts, with copy and clear controls

## Requirements

| Component | Requirement |
| --- | --- |
| Operating system | Windows 10/11, 64-bit x86 |
| Microphone | A working Windows recording device |
| Python | Python 3.12, 64-bit, including the Python launcher and Tcl/Tk |
| Storage | About 1.6 GB for model weights, plus Python dependencies; GPU libraries require additional space |
| GPU | Optional compatible NVIDIA CUDA GPU and driver; the development configuration used an RTX 3050 with 6 GB VRAM |
| Internet | Needed for initial dependency/model downloads; dictation runs offline afterward |

CPU mode is supported, but the large model can be substantially slower. Cold starts also take longer than subsequent phrases. This is phrase-based dictation, not guaranteed instantaneous word-by-word streaming.

## Download and install

### From a Windows release package

When a build is available, open this repository's **Releases** page and download `Transcribr-<version>-windows-x64.zip` from **Assets**. Extract the entire archive to a permanent folder. Do not run the executable from inside the ZIP.

Install [Python 3.12 for Windows](https://www.python.org/downloads/windows/), including the launcher and Tcl/Tk, then open PowerShell in the extracted folder:

```powershell
# NVIDIA GPU setup
.\setup.ps1 -Gpu

# Or CPU-only setup
.\setup.ps1
```

The setup script creates a private `.venv`, installs dependencies, downloads and verifies the pinned speech model, and adds a Start menu shortcut. A release ZIP contains the controller and app files; Python dependencies and model weights are downloaded during setup. It is not a self-contained, single-executable installer.

To also enable sign-in startup:

```powershell
.\setup.ps1 -Gpu -StartWithWindows
```

Launch **Transcribr** from the Start menu or double-click `Start Transcribr.vbs`. Keep the extracted folder in place: the executable uses the worker, environment, and model files beside it.

### Build from source

Download this repository through **Code → Download ZIP**, or clone it with Git. Source checkouts do not include the compiled executable or model weights.

```powershell
git clone https://github.com/AlthafTM/transcribr.git
cd transcribr
```

In addition to Python, install [Rust](https://rust-lang.org/tools/install/) and the [Windows C++ build tools / Windows SDK](https://learn.microsoft.com/en-us/windows/dev-environment/rust/setup). Use **Developer PowerShell for Visual Studio** so the MSVC linker and resource compiler are available:

```powershell
cd path\to\Transcribr
.\setup.ps1 -Gpu
```

Setup builds the native controller if `Transcribr.exe` is missing. Use `-Rebuild` to rebuild an existing installation. GNU Rust with `windres` is also supported; the build wrapper reuses project-local tools when present.

## How to use

1. Launch Transcribr; find the five-bar icon in the Windows notification area, possibly under the hidden-icons arrow
2. Click the tray icon and choose **English only** or **Indonesian only**
3. Click the message box, document, or other editable field where you want the words
4. Press **F12** once and speak normally; the waveform opens and responds to your microphone
5. Press **F12** again to stop the microphone and finish the last phrase

| Control | Action |
| --- | --- |
| F12 | Start or stop recording |
| Esc | Cancel unfinished speech; text already inserted stays |
| Tray icon | Language, history/settings, startup, and quit |
| History & settings | Copy transcripts, clear history, edit vocabulary, accuracy, cleanup, and startup |
| `Stop Transcribr.ps1` | Stop the installed controller from PowerShell |

The selected language is fixed for each recording. Automatic language detection, translation, and mixed-language dictation are not supported modes. Change the language before starting a new recording.

### Choose a local AI model

Turbo is installed by default. For a smaller model or an accuracy-oriented Large-v3 experiment, quit the app, open PowerShell in its folder, and run **one** command:

```powershell
.\.venv\Scripts\python.exe select_model.py small
.\.venv\Scripts\python.exe select_model.py large-v3
# Restore the default
.\.venv\Scripts\python.exe select_model.py turbo
```

Wait for the download/selection to finish, then reopen `Start Transcribr.vbs`. All choices run locally and retain the English/Indonesian language controls. Larger models can increase latency and memory use; a better result is not guaranteed. See the [step-by-step AI model guide](docs/AI-MODELS.md) for choosing, installing, switching back, and developing other engine integrations.

### Correct something you just dictated

Pause, then repeat the sentence with the correction:

> I would like to send the report on Friday
>
> I would like to send the report on Monday

A closely matching repeat can replace the earlier dictated sentence instead of adding a duplicate. You can also say **Correction, Tuesday** or **Ralat, Selasa** to replace the last dictated word.

Corrections apply to recently inserted dictation in the **same field**, for up to **90 seconds**. Keep the cursor immediately after that text. Transcribr verifies the old suffix before replacing it. Moving the cursor, manually editing/submitting the text, or unsupported editor APIs can prevent correction; the new speech may be appended normally instead. Similarity matching is conservative and does not understand arbitrary editing instructions.

### Where the text goes

A recording is tied to its original editable field. Switching windows or fields pauses insertion; returning to the original field during the recording can resume it. Start a new recording to change destinations.

If an app takes time to expose its editor, recording stays active while Transcribr retries the original destination. Clicking, scrolling, or typing before that initial lookup completes cancels deferred binding, so delayed text cannot choose a new field. Text that could not be inserted remains in history.

Transcribr never presses Enter to send a message and does not replace your clipboard while dictating. Password and read-only controls are excluded. Custom, protected, or elevated applications may reject injected typing. Browser/editor compatibility can vary; there is no guarantee that every text surface is supported.

## Settings, privacy, and troubleshooting

Settings, status, logs, and transcript history are stored in `%LOCALAPPDATA%\Transcribr`. Existing settings/history from the previous Transcriber name migrate on first launch.

- **Recording but no text:** stop, open history to recover the transcript, click the intended editable field, and start a new recording
- **Wrong language:** select the correct fixed language in the tray menu before recording
- **Slow first phrase:** the model may be loading; it unloads after about 90 seconds of inactivity
- **CPU fallback:** verify the GPU setup and driver if acceleration is expected; CUDA libraries are loaded from the app's private Python environment
- **Poor recognition:** confirm Windows is using the intended microphone, reduce background noise, and add unusual names to personal vocabulary
- **Stop or uninstall:** quit from the tray, disable **Start with Windows**, remove the Start menu shortcut, and remove the app folder; delete `%LOCALAPPDATA%\Transcribr` separately if you also want to remove local transcripts

Speech inference stays on the device. Normal dictation does not save audio recordings. Original and cleaned transcripts are stored locally until cleared or displaced by newer history. No browser extension, cloud transcription service, or generative rewriting model is required.

## Development

The Win32 controller handles the hotkey, microphone buffers, waveform, tray menu, destination tracking, and spoken-correction edits. A separate Python worker runs [faster-whisper](https://github.com/SYSTRAN/faster-whisper) / CTranslate2. Editor accessibility calls run on a dedicated COM thread so a slow provider cannot block F12 or the waveform.

```powershell
.\build-native.ps1 build --release --locked
.\build-native.ps1 test --release --locked
.\.venv\Scripts\python.exe -m unittest test_live_worker -q

# Create a Windows release ZIP and SHA-256 file
.\package.ps1 -Build
```

Tests requiring desktop focus and synthetic audio are documented in [Development and testing](docs/DEVELOPMENT.md). GPU dependencies are optional; see `requirements-gpu.txt`. Icon generation uses `requirements-dev.txt`.

The checked-in model manifest pins the download revision and SHA-256 checksums. Generated executables, weights, environments, runtimes, recordings, logs, and personal data are excluded from Git.

See [third-party notices](THIRD_PARTY.md) for recognition/model dependencies and their licenses.
