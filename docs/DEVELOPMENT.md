# Development and testing

See the [0.10 performance review](PERFORMANCE-REVIEW.md) for picker startup/transition measurements, renderer cache costs, encoder reuse, idle signaling, and setup streaming changes.

## Experimental macOS / Linux frontend

See [platform setup and current limits](MACOS-LINUX.md). The `desktop.yml` workflow runs shared/desktop unit tests, shell validation, Qt waveform/launcher smoke tests, and real Small-model synthetic speech on native Linux, Apple Silicon, and Intel Mac runners. Linux additionally verifies F12, paste into its own synthetic editor, and rejection after switching windows under Xvfb/Openbox. No real microphone or macOS permission grants are automated. Windows keeps its Rust controller and existing installer.

## Build the Windows installer

The installer is a per-user Windows setup executable. It bundles independent on-directory Python runtimes for inference and settings, including CUDA libraries, rather than depending on the user's Python installation. The application controller remains Rust. Setup uses the .NET Framework compiler included with Windows; no separate installer authoring tool is required.

```powershell
# After setting up the source environment and native build tools
.\scripts/windows/build-installer.ps1
# Reuse already-built runtimes while iterating on installer UI/docs
.\scripts/windows/build-installer.ps1 -SkipFreeze
```

Output: `dist/Dictately-<version>-Setup.exe` and `.exe.sha256`. The checked-in `installer/runtime.spec` controls PyInstaller collection; `assets/package-files.json` is the app-file allowlist shared by ZIP and installer packaging. The installer contains the runtime but downloads the verified Turbo weights on first installation. PyInstaller builds are on-directory, avoiding per-launch extraction overhead.

Setup offers a folder picker, optional sign-in startup, and optional launch. It creates a Start menu shortcut and a per-user Installed Apps entry. Installation folders must be empty or have the matching Dictately ownership marker. A marker tracks installed files for repair/uninstall; uninstallation preserves the separate `%LOCALAPPDATA%/Dictately` settings/history. The optional model-removal path rejects linked directories. Future signing/update support remains separate work.

For automated validation in a **new disposable folder**, the installer accepts:

```powershell
.\dist\Dictately-0.7.0-Setup.exe --silent --dir "C:\path\to\disposable-install" --no-launch --no-startup
```

`--model-cache "C:\path\to\verified-Turbo-files"` allows an offline/cached setup test. Each source model file is checked against the pinned SHA-256 manifest before copying; the normal downloader verifies it again. This does not skip integrity checks. `--preview "C:\path\to\preview.png"` renders the setup window without installation.

Test install, frozen CUDA/CPU inference, native controller startup, settings UI startup, repair, and uninstall in that folder. Preserve/restore any existing Start menu shortcut and startup/Installed Apps registry values during local tests; an installer intentionally manages those per-user entries. Never point an uninstall test at a source checkout. A clean-folder test on the development device does not replace a fresh Windows VM test.

Installed model selection can use `runtime/worker/DictatelyWorker.exe --select-model <turbo|small|large-v3>`. `--self-check` loads the selected local model and reports the backend, without recording or typing. The worker protocol remains compatible with the source installation.

## Source development checks

Run source-build commands from Developer PowerShell for Visual Studio with Rust, the Windows SDK, and Python 3.12 installed. Alternatively use a GNU Rust toolchain with windres. Project-local toolchains are optional; the build wrapper detects them when available.

```powershell
.\scripts/windows/setup.ps1 -Gpu
.\.venv\Scripts\python.exe -m pip install -r requirements/dev.txt
.\scripts/windows/build-native.ps1 test --release --locked
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -q
```

Native tests requiring foreground windows are ignored by default. On an unlocked Windows desktop, with no dictation session active:

```powershell
.\scripts/windows/build-native.ps1 test --release --locked '--' --ignored
```

The desktop routing test opens its own native editors. It checks the original destination, blocks another field/window, verifies correction suffixes, rejects a moved cursor, and rejects cancelled sessions. Do not type or change focus during this test. It does not open personal chats.

The model-free Python tests use mock speech results. They cover fixed language selection, phrase agreement, and cleanup behavior. Microphone/desktop tests are separate from recognition accuracy evaluations.

```powershell
# Save microphone sample-count metadata only, without recording an audio file
.\Dictately.exe --check-mic

# Lifecycle smoke test (typing disabled)
.\.venv\Scripts\python.exe tests/check_lifecycle.py
```

`tests/check_native.py` additionally requires local synthesized WAV/PCM fixtures; generate them with `tests/make_audio_fixtures.ps1` before running `--quick` or `--correction`. Full desktop tests must run one at a time. Model inference timings and synthetic speech are not promises of real-microphone accuracy.

To regenerate the icon:

```powershell
.\.venv\Scripts\python.exe assets/make_icon.py
```

To package a release:

```powershell
.\scripts/windows/package.ps1 -Build
```

The archive contains the Windows executable, Python worker/settings sources, setup script, icon, pinned model manifest, and notices. It excludes the private Python environment, model weights, local logs, and history. Setup downloads dependencies and weights on the recipient's computer. Test release installation in a fresh extracted directory before publishing a release.

Runtime code retains the `LOCALFLOW_DATA` environment override for isolated developer tests. Normal installations use `%LOCALAPPDATA%\Dictately`. Old experimental Python UI and generative-cleanup tools on the development machine are not distributed or used by the native app.

Worker transport uses newline-terminated JSON control headers. Recognition headers specify `audio_format: "s16le"` and `audio_samples`; exactly twice that count of PCM bytes follows immediately. Responses remain JSON lines. Use binary stdin reads so newline bytes inside PCM cannot desynchronize frames. Legacy JSON requests with `samples` remain accepted for development tools. Update the native controller and Python worker together when changing the protocol.

Run the receive/parsing microbenchmark with `.\.venv\Scripts\python.exe tests/benchmark_transport.py`. Its timings exclude inference and editor insertion; see the [comparison and optimization report](WISPR-FLOW-COMPARISON.md) for methodology and practical limits.

Microphone open/start/stop/close operations belong to a dedicated audio thread, separate from both the UI/hotkey thread and inference. Shared sample buffers use `Arc`; WinMM handles stay on their owner thread. Open/close completions carry a session ID, queued starts check the current cancellation token before opening/recording, and final recognition waits for the stop-tail event.

With the normal app stopped, run `.\.venv\Scripts\python.exe tests/check_startup.py` for a real-microphone responsiveness test without text insertion or audio-file storage. It adds 600 ms to microphone opening, checks UI pings and the waveform, cancels a pending start, restarts, and checks final stop/sample collection. The delay override is accepted only in `--test-target 0` mode. `--baseline` records timings for older executables; it does not enforce the new protocol/status checks. Test results stay under ignored `qa/startup`.

## Dictately 0.8 optimization measurements

The language picker caches unchanged labels, the segmented rail, and the selected pill. Once entry/selection/hover settle, only the small microphone region is composited over the cached panel; transitions retain supersampled interpolation. The smaller dictation overlay does not allocate the picker-only content buffers. PCM transport also writes the existing contiguous little-endian sample bytes without allocating a second byte vector.

A local comparison used the previous 0.7 executable and the new 0.8 executable with the same `--preview-language-panel` sequence: 45 display updates, then 24 closing updates, with no microphone, recognition, or frame-file recording. Four runs per executable at 175% Windows scaling used process kernel+user CPU time. Median CPU time was **765.6 ms before / 515.6 ms after**, a **32.7% reduction for that UI sequence**. Wall-time measurements varied with desktop scheduling and are not a frame-rate guarantee. This does not measure transcription speed, accuracy, or whole-app battery use.

The live worker now owns only its speech model, avoiding initialization/imports for the unused generative rewriting server. Opening the language picker does not enqueue model preparation; silence keeps only a 200 ms audio lead-in and does not repeatedly run speech recognition. Mouse selection can operate after the model has unloaded without reloading it. Voice selection starts recognition asynchronously after microphone activity.

Use `DICTATELY_DATA` / `DICTATELY_ROOT` for explicit development directories; legacy `LOCALFLOW_DATA` / `TRANSCRIBR_ROOT` overrides remain accepted. The new installer accepts the old managed install marker and reuses the model folder. User settings/history are copied to the new data directory only when missing. Branding compatibility strings in migration code are intentional.

## Animation timing and cursor verification (0.8.1)

The controller previously used the same `WM_TIMER` for audio polling and rendering. Its nominal 33 ms steady interval measured **46.86 ms median / 48.45 ms p95** on the development laptop, roughly 21 submitted frames per second. Rendering itself took a median 1.63 ms, so the timer cadence was the main limit in that test.

A high-resolution one-shot waitable timer now drives visible animation at a target of 60 FPS. Deadlines remain on a fixed phase and skip missed intervals rather than queueing catch-up frames. Audio/controller polling remains separate. The frame timer is cancelled while hidden; no rendering thread or fast idle polling is added. Systems unable to create a timer retain the controller-timer fallback. The tray menu's nested message loop also uses that fallback while open.

Owned-process tests with synthetic silence measured **16.69 ms median / 16.98 ms p95** for the picker and **16.68 ms median / 17.17 ms p95** for the waveform. These are local frame-submission intervals, not measured compositor/display presentation or guarantees on every device. The picker microphone level is interpolated between audio updates. The window class now has an arrow cursor, and client cursor handling selects an arrow or hand explicitly instead of inheriting a busy cursor.

With the normal app stopped, run:

```powershell
.\.venv\Scripts\python.exe tests/check_animation.py
.\.venv\Scripts\python.exe tests/check_animation.py --waveform
```

The tests never open the microphone or insert text. They use synthetic PCM, check cursor assignment/handling and frame intervals, and write ignored results under `qa/`. `DICTATELY_FRAME_METRICS` optionally records numeric submission timing; it contains no audio or transcript and is flushed only when the diagnostic process exits.

## Audio and appearance preferences (0.9)

Windows settings persist `overlay_position` (six top/bottom alignment choices, default `bottom_center`) and `microphone` (empty for the system default). Enumeration uses WinMM capability queries and the endpoint ID returned by [DRV_QUERYFUNCTIONINSTANCEID](https://learn.microsoft.com/en-us/windows/win32/coreaudio/endpoint-id-strings). It does not open capture. Selected endpoints are resolved on the audio thread for each new recording or picker session, so device-list reordering cannot silently select another microphone. Missing or ambiguous saved devices fail explicitly. Legacy drivers without endpoint IDs use a capability/name identity and reject duplicate matches.

The WinMM callback now signals an auto-reset event after copying a completed buffer. The recycling thread waits for that event instead of waking every 10 ms to scan unchanged headers. Notifications can coalesce: every completed header is checked after waking, and shutdown explicitly wakes/joins the thread before stopping/resetting the device and collecting the tail. With 100 ms capture buffers this replaces roughly 100 scheduled polling wakeups per second with completion-driven work, normally around 10 notifications per second while capturing. This is a scheduling change, not a measured whole-app battery or recognition-speed improvement.

Initial shared capture storage is **64,000 bytes instead of 640,000 bytes**, a 90% reduction in that allocation. It grows if necessary and retains the 150-second safety bound. Speech models dominate overall memory use; this is not a 90% reduction in total app memory.

`tests/check_preferences.py` checks the Windows controls, native enumeration, saving, unavailable-device preservation, and reset without recording. Shared tests check placement on negative-coordinate monitors and mic reordering/disconnection/ambiguity. Native tests exercise completed-buffer notification and stop-state behavior with artificial samples, without a real device. macOS/Linux preferences use the same positions, background device enumeration, and a host/name identity. Their real-microphone limitations still apply.
