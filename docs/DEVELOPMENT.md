# Development and testing

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
