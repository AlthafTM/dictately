# Development and testing

## Experimental macOS / Linux frontend

See [platform setup and current limits](MACOS-LINUX.md). The `desktop.yml` workflow runs shared/desktop unit tests, shell validation, Qt waveform/launcher smoke tests, and real Small-model synthetic speech on native Linux, Apple Silicon, and Intel Mac runners. Linux additionally verifies F12, paste into its own synthetic editor, and rejection after switching windows under Xvfb/Openbox. No real microphone or macOS permission grants are automated. Windows keeps its Rust controller and existing installer.

## Build the Windows installer

The installer is a per-user Windows setup executable. It bundles independent on-directory Python runtimes for inference and settings, including CUDA libraries, rather than depending on the user's Python installation. The application controller remains Rust. Setup uses the .NET Framework compiler included with Windows; no separate installer authoring tool is required.

```powershell
# After setting up the source environment and native build tools
.\build-installer.ps1
# Reuse already-built runtimes while iterating on installer UI/docs
.\build-installer.ps1 -SkipFreeze
```

Output: `dist/Transcribr-<version>-Setup.exe` and `.exe.sha256`. The checked-in `installer/runtime.spec` controls PyInstaller collection; `assets/package-files.json` is the app-file allowlist shared by ZIP and installer packaging. The installer contains the runtime but downloads the verified Turbo weights on first installation. PyInstaller builds are on-directory, avoiding per-launch extraction overhead.

Setup offers a folder picker, optional sign-in startup, and optional launch. It creates a Start menu shortcut and a per-user Installed Apps entry. Installation folders must be empty or have the matching Transcribr ownership marker. A marker tracks installed files for repair/uninstall; uninstallation preserves the separate `%LOCALAPPDATA%/Transcribr` settings/history. The optional model-removal path rejects linked directories. Future signing/update support remains separate work.

For automated validation in a **new disposable folder**, the installer accepts:

```powershell
.\dist\Transcribr-0.6.0-Setup.exe --silent --dir "C:\path\to\disposable-install" --no-launch --no-startup
```

`--model-cache "C:\path\to\verified-Turbo-files"` allows an offline/cached setup test. Each source model file is checked against the pinned SHA-256 manifest before copying; the normal downloader verifies it again. This does not skip integrity checks. `--preview "C:\path\to\preview.png"` renders the setup window without installation.

Test install, frozen CUDA/CPU inference, native controller startup, settings UI startup, repair, and uninstall in that folder. Preserve/restore any existing Start menu shortcut and startup/Installed Apps registry values during local tests; an installer intentionally manages those per-user entries. Never point an uninstall test at a source checkout. A clean-folder test on the development device does not replace a fresh Windows VM test.

Installed model selection can use `runtime/worker/TranscribrWorker.exe --select-model <turbo|small|large-v3>`. `--self-check` loads the selected local model and reports the backend, without recording or typing. The worker protocol remains compatible with the source installation.

## Source development checks

Run source-build commands from Developer PowerShell for Visual Studio with Rust, the Windows SDK, and Python 3.12 installed. Alternatively use a GNU Rust toolchain with windres. Project-local toolchains are optional; the build wrapper detects them when available.

```powershell
.\setup.ps1 -Gpu
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\build-native.ps1 test --release --locked
.\.venv\Scripts\python.exe -m unittest test_live_worker -q
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -q
```

Native tests requiring foreground windows are ignored by default. On an unlocked Windows desktop, with no dictation session active:

```powershell
.\build-native.ps1 test --release --locked '--' --ignored
```

The desktop routing test opens its own native editors. It checks the original destination, blocks another field/window, verifies correction suffixes, rejects a moved cursor, and rejects cancelled sessions. Do not type or change focus during this test. It does not open personal chats.

The model-free Python tests use mock speech results. They cover fixed language selection, phrase agreement, and cleanup behavior. Microphone/desktop tests are separate from recognition accuracy evaluations.

```powershell
# Save microphone sample-count metadata only, without recording an audio file
.\Transcribr.exe --check-mic

# Lifecycle smoke test (typing disabled)
.\.venv\Scripts\python.exe test_lifecycle.py
```

`test_native.py` additionally requires local synthesized WAV/PCM fixtures; generate them with `tests/make_audio_fixtures.ps1` before running `--quick` or `--correction`. Full desktop tests must run one at a time. Model inference timings and synthetic speech are not promises of real-microphone accuracy.

To regenerate the icon:

```powershell
.\.venv\Scripts\python.exe assets/make_icon.py
```

To package a release:

```powershell
.\package.ps1 -Build
```

The archive contains the Windows executable, Python worker/settings sources, setup script, icon, pinned model manifest, and notices. It excludes the private Python environment, model weights, local logs, and history. Setup downloads dependencies and weights on the recipient's computer. Test release installation in a fresh extracted directory before publishing a release.

Runtime code retains the `LOCALFLOW_DATA` environment override for isolated developer tests. Normal installations use `%LOCALAPPDATA%\Transcribr`. Old experimental Python UI and generative-cleanup tools on the development machine are not distributed or used by the native app.

Worker transport uses newline-terminated JSON control headers. Recognition headers specify `audio_format: "s16le"` and `audio_samples`; exactly twice that count of PCM bytes follows immediately. Responses remain JSON lines. Use binary stdin reads so newline bytes inside PCM cannot desynchronize frames. Legacy JSON requests with `samples` remain accepted for development tools. Update the native controller and Python worker together when changing the protocol.

Run the receive/parsing microbenchmark with `.\.venv\Scripts\python.exe tests/benchmark_transport.py`. Its timings exclude inference and editor insertion; see the [comparison and optimization report](WISPR-FLOW-COMPARISON.md) for methodology and practical limits.

Microphone open/start/stop/close operations belong to a dedicated audio thread, separate from both the UI/hotkey thread and inference. Shared sample buffers use `Arc`; WinMM handles stay on their owner thread. Open/close completions carry a session ID, queued starts check the current cancellation token before opening/recording, and final recognition waits for the stop-tail event.

With the normal app stopped, run `.\.venv\Scripts\python.exe tests/check_startup.py` for a real-microphone responsiveness test without text insertion or audio-file storage. It adds 600 ms to microphone opening, checks UI pings and the waveform, cancels a pending start, restarts, and checks final stop/sample collection. The delay override is accepted only in `--test-target 0` mode. `--baseline` records timings for older executables; it does not enforce the new protocol/status checks. Test results stay under ignored `qa/startup`.
