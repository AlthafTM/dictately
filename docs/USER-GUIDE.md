# User guide

[Back to Dictately](../README.md)

## Requirements

| Component | Requirement |
| --- | --- |
| Operating system | Windows 10/11, 64-bit x86 |
| Microphone | A working Windows recording device |
| Runtime | Included in the installer; source/ZIP installations need Python 3.12, 64-bit, with the launcher and Tcl/Tk |
| Storage | Allow at least 8 GB free during setup, including temporary downloads; the default speech weights are about 1.6 GB |
| GPU | Optional compatible NVIDIA CUDA GPU and driver; the development configuration used an RTX 3050 with 6 GB VRAM |
| Internet | Needed for initial dependency/model downloads; dictation runs offline afterward |

CPU mode is supported, but the large model can be substantially slower. Cold starts also take longer than subsequent phrases. This is phrase-based dictation, not guaranteed instantaneous word-by-word streaming.

## Download and install

### Windows installer: recommended

1. Open [Releases](https://github.com/AlthafTM/dictately/releases) and download **`Dictately-<version>-Setup.exe`**
2. Open it, choose an installation folder, optionally enable **Start Dictately when I sign in to Windows**, and click **Install**
3. Wait while setup installs the private runtime and downloads/verifies the speech model (about 1.6 GB)
4. Open **Dictately** from Start, use your language-selection shortcut to choose a language, click a text field, and use your dictation shortcut to start speaking

The installer includes Python, speech libraries, the settings UI, and NVIDIA runtime libraries. It installs for your Windows user without changing system Python or requiring Rust/build tools. GPU acceleration is selected automatically when a compatible NVIDIA GPU and working driver are available; CPU fallback remains supported. After setup, dictation is local and works offline.

Interrupted model downloads can be resumed by running setup again in the same folder. Setup keeps existing transcript history and settings. To uninstall, use **Windows Settings → Apps → Installed apps → Dictately**, or open `Uninstall Dictately.exe` in the app folder. Uninstall can remove downloaded models and keeps `%LOCALAPPDATA%\Dictately` history/settings.

The current installer is unsigned. See [installer build and validation](DEVELOPMENT.md#build-the-windows-installer) for packaging details.

### Manual ZIP installation

When a build is available, open this repository's **Releases** page and download `Dictately-<version>-windows-x64.zip` from **Assets**. Extract the entire archive to a permanent folder. Do not run the executable from inside the ZIP.

Install [Python 3.12 for Windows](https://www.python.org/downloads/windows/), including the launcher and Tcl/Tk, then open PowerShell in the extracted folder:

```powershell
# NVIDIA GPU setup
.\scripts/windows/setup.ps1 -Gpu

# Or CPU-only setup
.\scripts/windows/setup.ps1
```

The setup script creates a private `.venv`, installs dependencies, downloads and verifies the pinned speech model, and adds a Start menu shortcut. A release ZIP contains the controller and app files; Python dependencies and model weights are downloaded during setup. It is not a self-contained, single-executable installer.

To also enable sign-in startup:

```powershell
.\scripts/windows/setup.ps1 -Gpu -StartWithWindows
```

Launch **Dictately** from the Start menu or double-click `launchers/windows/Start Dictately.vbs`. Keep the extracted folder in place: the executable uses the worker, environment, and model files beside it.

### Build from source

Download this repository through **Code → Download ZIP**, or clone it with Git. Source checkouts do not include the compiled executable or model weights.

```powershell
git clone https://github.com/AlthafTM/dictately.git
cd dictately
```

In addition to Python, install [Rust](https://rust-lang.org/tools/install/) and the [Windows C++ build tools / Windows SDK](https://learn.microsoft.com/en-us/windows/dev-environment/rust/setup). Use **Developer PowerShell for Visual Studio** so the MSVC linker and resource compiler are available:

```powershell
cd path\to\Dictately
.\scripts/windows/setup.ps1 -Gpu
```

Setup builds the native controller if `Dictately.exe` is missing. Use `-Rebuild` to rebuild an existing installation. GNU Rust with `windres` is also supported; the build wrapper reuses project-local tools when present.

## How to use

1. Launch Dictately; find the waveform icon in the Windows notification area, possibly under the hidden-icons arrow
2. Use your language-selection shortcut and click **English**, **Indonesia**, or **Both**, or say “English”, “Indonesia”, or “Both English and Indonesian”
3. Click the message box, document, or other editable field where you want the words
4. Use your dictation shortcut once and speak normally; the waveform opens and responds to your microphone
5. Use your dictation shortcut again to stop the microphone and finish the last phrase

| Control | Action |
| --- | --- |
| Your dictation shortcut | Start or stop recording |
| Your language-selection shortcut | Open / close the voice-and-mouse language picker |
| Esc | Cancel unfinished speech; text already inserted stays |
| Tray icon | Language, history/settings, startup, and quit |
| History & settings | Copy transcripts, clear history, edit vocabulary, accuracy, cleanup, and startup |
| Keyboard shortcuts | Click a shortcut, press your preferred key/combination, then save |
| Audio & appearance | Choose your microphone and waveform position, then save |
| `scripts/windows/Stop Dictately.ps1` | Stop the installed controller from PowerShell |

**Default Windows shortcuts: F12 for dictation and F10 for language selection.** Both are configurable during setup or from **Tray → Keyboard shortcuts**. A **Dictately Settings** Start-menu launcher also opens the editor, with a shortcuts control at its bottom left. Esc cancels shortcut capture; duplicate assignments and modifier-only shortcuts are rejected. Windows-reserved shortcuts may be unavailable, and assigning a normal typing key consumes that key globally while Dictately runs.

Open **Tray → Audio & appearance** to select **Bottom left / center / right** or **Top left / center / right**. The waveform and Windows language picker use that location on the active monitor, with room for the taskbar. **Bottom center** is the default. Changes apply to the next recording or picker opening.

In the same panel, choose **System default** to follow your operating system's microphone setting, or select a specific device and save it as your preferred microphone. Use **Refresh** after connecting a new device. Windows remembers the endpoint identity rather than its changing list position. If a saved device is disconnected, choose an available microphone or System default before recording. These controls are also available in the macOS/Linux tray menu; those editions match device name and audio host, and reject ambiguous duplicate matches.

**English** and **Indonesian** modes stay locked for the recording. **Both** enables local per-segment language selection with candidates restricted to English and Indonesian; it does not translate. Rapid switching within a sentence can still be less reliable than a fixed mode. Pressing the language shortcut during dictation finishes the current phrase before opening the picker. Picker speech is used only for language commands, never inserted or saved as a dictation. Click an option if a voice command is unclear. The picker closes after 30 seconds or when you use your language-selection shortcut again or press Esc. After choosing, use your dictation shortcut to start speaking. The language picker and custom Windows shortcuts are part of the Windows controller; the experimental Mac/Linux frontend keeps its separately documented controls.

### Choose a local AI model

Turbo is installed by default. For a smaller model or an accuracy-oriented Large-v3 experiment, quit the app, open PowerShell in its folder, and run **one** command:

```powershell
# Installer users: run one command from the app folder
.\runtime\worker\DictatelyWorker.exe --select-model small
.\runtime\worker\DictatelyWorker.exe --select-model large-v3
# Restore the default
.\runtime\worker\DictatelyWorker.exe --select-model turbo
```

Source/manual ZIP users use `.\.venv\Scripts\python.exe app/select_model.py <model>` instead. Detailed instructions for both are in the model guide.

Wait for the download/selection to finish, then reopen `launchers/windows/Start Dictately.vbs`. All choices run locally and retain the English/Indonesian language controls. Larger models can increase latency and memory use; a better result is not guaranteed. See the [step-by-step AI model guide](AI-MODELS.md) for choosing, installing, switching back, and developing other engine integrations.

### Correct something you just dictated

Pause, then repeat the sentence with the correction:

> I would like to send the report on Friday
>
> I would like to send the report on Monday

A closely matching repeat can replace the earlier dictated sentence instead of adding a duplicate. You can also say **Correction, Tuesday** or **Ralat, Selasa** to replace the last dictated word.

Corrections apply to recently inserted dictation in the **same field**, for up to **90 seconds**. Keep the cursor immediately after that text. Dictately verifies the old suffix before replacing it. Moving the cursor, manually editing/submitting the text, or unsupported editor APIs can prevent correction; the new speech may be appended normally instead. Similarity matching is conservative and does not understand arbitrary editing instructions.

### Where the text goes

A recording is tied to its original editable field. Switching windows or fields pauses insertion; returning to the original field during the recording can resume it. Start a new recording to change destinations.

If an app takes time to expose its editor, recording stays active while Dictately retries the original destination. Clicking, scrolling, or typing before that initial lookup completes cancels deferred binding, so delayed text cannot choose a new field. Text that could not be inserted remains in history.

Dictately never presses Enter to send a message and does not replace your clipboard while dictating. Password and read-only controls are excluded. Custom, protected, or elevated applications may reject injected typing. Browser/editor compatibility can vary; there is no guarantee that every text surface is supported.

## Settings, privacy, and troubleshooting

Settings, status, logs, and transcript history are stored in `%LOCALAPPDATA%\Dictately`. Existing settings/history from Transcribr and earlier names migrate on first launch without overwriting Dictately data. Original files remain in the old folder. Existing installer upgrades reuse their model folder and update the app/startup shortcuts. During migration, the previous language-selection default is updated when it does not conflict with your dictation binding; other custom bindings are kept.

- **Recording but no text:** stop, open history to recover the transcript, click the intended editable field, and start a new recording
- **Wrong language:** select the correct fixed language in the tray menu before recording
- **Slow first phrase:** the model may be loading; it unloads after about 90 seconds of inactivity
- **CPU fallback:** verify the GPU setup and driver if acceleration is expected; CUDA libraries are loaded from the app's private Python environment
- **Poor recognition:** confirm Windows is using the intended microphone, reduce background noise, and add unusual names to personal vocabulary
- **Stop or uninstall:** quit from the tray, disable **Start with Windows**, remove the Start menu shortcut, and remove the app folder; delete `%LOCALAPPDATA%\Dictately` separately if you also want to remove local transcripts

Speech inference stays on the device. Normal dictation does not save audio recordings. Original and cleaned transcripts are stored locally until cleared or displaced by newer history. No browser extension, cloud transcription service, or generative rewriting model is required.
