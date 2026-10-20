# Transcribr for macOS and Linux — experimental

Local voice typing in **English** or **Indonesian**, with F12, a compact animated waveform, history, and application launchers. The initial dependencies and speech model need an internet connection; dictation works offline afterward. Audio is processed locally.

This frontend uses Python and Qt with the same recognition worker as Windows. The Windows Rust controller and installer are unchanged. These are **source installations**, not standalone signed installers. Native automated checks run on GitHub-hosted Linux and macOS machines; real microphone permissions and everyday Mac/Linux apps still need user testing.

## Compatibility

| System | Status |
| --- | --- |
| macOS 12+ on Apple Silicon or Intel | Experimental source launcher; Python 3.12 required |
| Linux x86-64 with X11/Xorg, such as Ubuntu 22.04/24.04 | Experimental source launcher; Python 3.12 and desktop libraries required |
| Linux Wayland | Not supported; choose an X11/Xorg session |
| Linux ARM, headless servers, Flatpak/Snap sandbox installation | Not validated |

These launchers default to multilingual **Small**, roughly 500 MB, to reduce CPU load. Windows continues to default to Turbo. Small can make more recognition mistakes than Turbo. Macs use CPU inference; Apple Metal/ANE acceleration is not implemented. A compatible NVIDIA Linux installation can use the shared CUDA backend, but this setup does not install CUDA libraries and has only CPU validation. Large models may not keep up with live speech on a CPU.

## macOS: install and launch

1. Install **Python 3.12** from [python.org](https://www.python.org/downloads/macos/), using the package for your Mac. Run its included **Install Certificates.command** if Python's HTTPS certificate check fails
2. Download this repository through **Code → Download ZIP**, extract it, and move the folder somewhere permanent, for example `~/Applications/transcribr-source`
3. Open **Terminal**, type `cd `, drag the extracted folder into Terminal, and press Return
4. Run:

   ```bash
   bash install-desktop.sh
   ```

   Setup creates `.venv-desktop`, installs libraries, downloads Small to your user data folder, and creates **`~/Applications/Transcribr.app`**. It does not modify system Python. You can also double-click **Install Transcribr.command** if executable permissions were preserved; otherwise use Terminal as above
5. Open **Transcribr** from your home **Applications** folder; find its five-bar menu-bar icon
6. In **System Settings → Privacy & Security**, grant the identity shown by macOS (Transcribr, Python, or Terminal, depending on how it was launched) **Microphone**, **Accessibility**, and **Input Monitoring** access. Quit and reopen afterward. The app explains missing typing/listening access; it does not change permissions itself
7. Choose **English only** or **Indonesian only**, click an editable field, press **F12**, speak, then press **F12** again to finish

Some keyboards require **Fn/Globe + F12** unless “Use F1, F2, etc. keys as standard function keys” is enabled. Reassign any conflicting F12 desktop shortcut. Password fields and apps without editable Accessibility elements are unsupported. The destination must allow clipboard paste.

The generated `.app` launches this source folder's private Python environment. Keep the folder in place. It is not a notarized redistributable bundle; no paid developer account is needed for this source installation.

## Linux: install and launch

1. Sign in to an **X11/Xorg** desktop session. Select an option such as **Ubuntu on Xorg** at login when available. `echo "$XDG_SESSION_TYPE"` should print `x11`; having `$DISPLAY` under Wayland is insufficient
2. Install **Python 3.12**, `venv` support, PortAudio, X11 utilities, and Qt desktop libraries. On **Ubuntu 24.04**:

   ```bash
   sudo apt update
   sudo apt install python3.12 python3.12-venv libportaudio2 xdotool xclip libegl1 libxcb-cursor0 libxkbcommon-x11-0 libxcb-icccm4 libxcb-keysyms1 libxcb-image0 libxcb-render-util0 libxcb-xinerama0
   ```

   Ubuntu 22.04 does not include Python 3.12 by default: install it with your preferred Python version manager, or use Ubuntu 24.04. Other distributions have different package names. Setup does not add package repositories or require running Transcribr as root
3. Download/extract the repository to a permanent folder, or clone it:

   ```bash
   git clone https://github.com/AlthafTM/transcribr.git
   cd transcribr
   bash install-desktop.sh
   ```

4. Open **Transcribr** from your application menu, or run `bash start-desktop.sh`. Desktops hiding tray icons need a tray/AppIndicator extension to reach its menu
5. Select **English only** or **Indonesian only**, click a field, press **F12**, speak, then press **F12** again. **Esc** cancels the unfinished phrase; already pasted phrases remain

F12 is reserved while running, so it should not also open browser developer tools. Remove conflicting window-manager shortcuts before launching. Linux uses standard **Ctrl+V** paste; terminals requiring **Ctrl+Shift+V** need manual copying from History. Running the app requires no `sudo`, `uinput` rules, or input-device permission changes.

## Startup, model choices, and data

Enable optional startup during installation or afterward:

```bash
bash install-desktop.sh --startup
# Without reinstalling dependencies/models:
.venv-desktop/bin/python desktop_app.py --install-launcher --startup
```

macOS writes `~/Library/LaunchAgents/dev.althafm.transcribr.plist`, effective at the next sign-in. Linux writes `~/.config/autostart/transcribr.desktop`. Disable startup by removing only that file, or disabling the desktop startup entry. Quit from the menu icon to stop the running app.

Quit Transcribr before switching models:

```bash
.venv-desktop/bin/python desktop_app.py --install-model turbo
# Return to the lighter default:
.venv-desktop/bin/python desktop_app.py --install-model small
```

Downloads use a single Hugging Face revision and the Hub's snapshot/cache mechanism. Selection changes only after required files exist. Turbo needs about 1.6 GB of model storage plus libraries. Keep several GB free for libraries, model caches, and staging. `--install-model large-v3` is also available but costs substantially more CPU time and RAM.

| Data | Location |
| --- | --- |
| macOS models, settings, history, log | `~/Library/Application Support/Transcribr` |
| Linux models, settings, history, log | `${XDG_DATA_HOME:-~/.local/share}/transcribr` |
| Private Python environment | `.venv-desktop` in the source folder |

**History / copy transcript** lets you copy or clear the last 50 transcripts, stored on your own disk. Audio stays in memory during a session and is not saved to recordings. Clipboard contents become the latest pasted phrase. To change recognition hints, accuracy, or cleanup, quit and edit `desktop-settings.json` (`vocabulary`, `accuracy`, `cleanup`); language is `en` or `id`.

## Current limits

- This experimental frontend has phrase output, language locking, light cleanup, no final full stops, history, animation, launchers, and optional startup
- It lacks Windows' recent-text spoken replacements, rich settings UI, robust caret tracking, and idle model unloading. Weights stay in RAM until quit
- Each paste checks the original app/window. After focus changes, insertion stops and History retains the transcript. macOS also checks the Accessibility element. X11 apps often expose one native window for several fields, so same-window field/caret changes cannot always be detected. Do not move the cursor or change fields while dictating
- Focus can still race with a simulated paste. Protected fields, terminals, unusual editors, and apps blocking automation are not guaranteed
- Live output is phrase-based and depends on CPU/model speed. Microphone opening runs off the GUI thread. Native noise reduction and resampling for microphones rejecting 16 kHz are not implemented; choose another input device if opening fails
- Hosted tests cannot fully exercise Mac permissions or real microphones. Linux routing checks use synthetic X11 editors. Smoke tests do not guarantee compatibility with every app

## Uninstall

Quit Transcribr. Remove the launcher (`~/Applications/Transcribr.app` on macOS or `~/.local/share/applications/transcribr.desktop` on Linux) and the optional startup file above. Remove the source folder if no longer needed. Models/history/settings remain in the data folder unless you choose to remove it too. Windows installations and data are separate.

## Development approach

The Rust Windows controller directly calls Win32 for audio, focus, typing, hotkeys, tray, and graphics. This frontend reuses recognition and adds Qt, PortAudio, macOS Accessibility, and X11 integration. A future Rust frontend can reuse the speech model/protocol.

[Native packages must be built on their target OS](https://pyinstaller.org/en/stable/operating-mode.html). GitHub-hosted checks avoid a VM on the Windows laptop. The [recognition backend supports macOS and Linux CPUs](https://opennmt.net/CTranslate2/installation.html), and [input hooks have platform-specific restrictions](https://pynput.readthedocs.io/en/latest/limitations.html). This workflow does not publish `.dmg`, `.deb`, or AppImage packages.
