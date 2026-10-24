# Development and testing

## Compact language picker and processing feedback

The picker is a 156 by 44 logical-pixel capsule, replacing the 380 by 112 segmented panel. Labels are rasterized once, and its settled label pixels are reused while the small orbit region animates. Wheel input supports partial deltas, and clicking or dragging works without activating the overlay. Language selection uses two larger overlapping orbiting balls; the selected language is larger, with a shallow side-view orbit and depth-sorted overlap. Clicking advances the language; microphone activity drives the panel glow. Only English and Indonesian are selectable. Voice selection tries the current language first and skips a second decode after an unambiguous command. An idle persistent picker can release the speech model after 90 seconds without closing its UI or microphone. Stale voice replies cannot override a newer mouse selection.

## Verified corrections and processing feedback

Ordinary corrections use an owned text snapshot and a unique range, preserving surrounding field content. Selection edits and caret insertion update that snapshot. Failed corrections never fall back to ordinary insertion. Native controls use bounded Win32 messages; other editors require readable Value/Text accessibility patterns. The interactive `typing::tests::` checks cover an earlier replacement, Unicode, selection/caret edits, continuation, stale ownership rejection, destination isolation. Run them with `--ignored --test-threads=1` on an unlocked desktop.

Processing uses a softly breathing white halo around a fixed glass panel. Successful editor delivery triggers a 130 ms rise and 460 ms fade with a dimmer halo and a small size pop. The moving rim highlight gets a wider, stronger halo. Microphone levels and a slow breathing phase modulate glow brightness and spread. After the opening animation, only microphone levels drive bar height. A 0.0015 RMS visual noise floor suppresses quiet background motion without filtering recognition audio. Processing uses the panel glow. On stop the waveform holds its shape for panel contraction; the close starts after 180 ms regardless of model completion. Rapid shortcut presses are coalesced over 35 ms. The latest requested mode remains visible while final audio continues to its original editor; canceled microphone opens and stale queued inference are skipped. Hidden finalization polls at 33 ms instead of 16 ms. Shortcut candidates refresh modifier state to recover missed modifier releases. Glow opacity eases in and out; a wider transparent inset allows the halo to extend outward and bleed inward while preserving the solid panel dimensions. The panel and waveform retain their shape. The existing visible-frame clock and supersampled contour renderer remain in use. `pipeline_feedback_renders_ordered_nonblank_frames` renders the state sequence offscreen; `DICTATELY_ACTIVITY_FRAMES` optionally saves raw BGRA frames under a chosen diagnostic folder. Recognition and editing do not wait for the feedback animation. Ordinary editor delivery is bounded to four failures or five seconds of waiting. Structured delivery rejects immediately on a verified write failure. Editor rejection pauses typing without closing recording; speech remains in History. Failed delivery stops processing feedback; unfinished recognition/parsing states settle when finalization completes. `editor_failures_stop_retries_keep_speech_and_clear_busy_feedback` exercises these controller transitions without a real window, microphone, model, or external text input.

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

Ordinary UI Automation dictation chunks use Unicode input at the verified selection rather than resetting the entire field with `SetValue`, which can move the caret to the start. The owned WPF fixture checks consecutive chunks, Unicode, insertion at an earlier caret, corrections, and stale ownership rejection alongside the native EDIT fixture. Both fixtures are local; the WPF fixture compiles with the installed Windows .NET Framework.

`tests/check_stop_controls.py` verifies controller toggle messages with synthetic silence, a cold local model, and repeated stops during finalization. It sends no global keystrokes, opens no real microphone, and delivers no text. The separately ignored `audio::tests::rapid_real_device_switches_release_the_final_capture` test briefly exercises the Windows default microphone in memory, including queued cancellations and final handle closure.

## Native voice screenshots

`app/screen_capture.py` recognizes only screenshot, screen shot, and SS, with identical matching in both selected languages. The speech worker holds possible command prefixes until at least 250 ms after the last recognized word; earlier dictation prevents a later command from triggering in that session. Extra words remain dictation. Command results carry `action: screenshot` and empty dictation text. Ordinary continuations return to normal transcription. The Windows controller stops dictation, waits until the overlay is hidden, then launches the unpackaged-app `ms-screenclip:` interface via `ShellExecuteW`. It does not request an MSIX-only redirect callback or read captured pixels. The desktop frontend waits for its waveform to hide and launches native interactive region tools. No additional model or screenshot dependency is loaded.

`tests/test_screen_capture.py` covers command boundaries, streaming prefix deferral, ordinary sentences, and platform command selection. Run `powershell -File tests/make_screenshot_fixture.ps1` to generate the local voice fixture. `tests/check_screenshot_voice.py` uses built-in synthesized speech and the real local speech model, but simulates the final OS launch through a seam restricted to no-insertion test mode. It verifies recording is stopped and the overlay hidden before launch; it saves no screen image. Native macOS/Linux capture requires validation on those operating systems.

Windows validation also opened the installed native crop UI (`SnipOverlayRootWindow`) through `ms-screenclip:` and cancelled without selecting or saving an image. The screenshot crop/save operation itself remains under user control.

Voice capture has no post-recognition delay: it hides the waveform immediately, bypassing its closing animation for capture. Before the first dictated text, the controller submits growing audio at a 250 ms cadence instead of waiting for a 750 ms RMS pause or three seconds of audio. The worker measures the command pause from recognized word timestamps, independent of microphone energy. On Windows, the microphone pauses while the native crop overlay is active; a bounded watcher observes the selector closing before resuming the same input owner and recording session. Capture time is excluded from the recording duration. The dictation shortcut cancels automatic resumption. The desktop frontend discards microphone audio during the capture subprocess and resumes when that subprocess exits; desktop-specific process reuse still requires native validation. The synthetic controller check verifies two captures and resumptions without OS capture or global keyboard hooks. `--test-no-hooks` is accepted only with synthetic PCM and no-insertion test mode.
