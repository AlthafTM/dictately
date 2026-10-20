"""Platform integration for the experimental macOS / Linux X11 desktop."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile


def run(*args):
    return subprocess.check_output(args, text=True, timeout=3).strip()


def check_platform():
    if sys.platform == "darwin":
        return
    if sys.platform != "linux":
        raise RuntimeError("This launcher supports macOS and Linux X11; use Dictately.exe on Windows")
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
        raise RuntimeError("Wayland is not supported yet. Sign out and choose an X11/Xorg session")
    if not os.environ.get("DISPLAY"):
        raise RuntimeError("Open Dictately from a graphical X11 desktop session")
    missing = [name for name in ("xdotool", "xclip") if not shutil.which(name)]
    if missing:
        raise RuntimeError("Install the Linux desktop dependencies: " + ", ".join(missing))


@dataclass(frozen=True)
class Destination:
    application: int
    window: int
    element: str


class DesktopInput:
    """Never reactivate a destination or type into a newly focused app/field."""
    def __init__(self):
        check_platform()
        if sys.platform == "darwin":
            from AppKit import NSWorkspace
            import ApplicationServices as ax
            import Quartz
            self.workspace, self.ax, self.quartz = NSWorkspace.sharedWorkspace(), ax, Quartz
            if not ax.AXIsProcessTrusted():
                raise RuntimeError("Allow Dictately/Python in System Settings > Privacy & Security > Accessibility, then restart")
            if not Quartz.CGPreflightListenEventAccess():
                raise RuntimeError("Allow Dictately/Python in Privacy & Security > Input Monitoring, then restart")
        else:
            from Xlib import display
            self.display = display.Display()

    def destination(self):
        if sys.platform == "darwin":
            pid = int(self.workspace.frontmostApplication().processIdentifier())
            if pid == os.getpid():
                raise RuntimeError("Click the text field in another app before pressing F12")
            app = self.ax.AXUIElementCreateApplication(pid)
            error, focused = self.ax.AXUIElementCopyAttributeValue(app, "AXFocusedUIElement", None)
            if error or focused is None:
                raise RuntimeError("The active app did not expose an editable text field")
            _, subrole = self.ax.AXUIElementCopyAttributeValue(focused, "AXSubrole", None)
            if subrole == "AXSecureTextField":
                raise RuntimeError("Dictation is unavailable in password fields")
            error, writable = self.ax.AXUIElementIsAttributeSettable(focused, "AXValue", None)
            if error or not writable:
                raise RuntimeError("Click an editable text field before pressing F12")
            # CFEqual-backed object equality is retained for the lifetime of the session.
            self.focused = focused
            return Destination(pid, 0, repr(focused))
        window = int(run("xdotool", "getactivewindow"))
        focused = self.display.get_input_focus().focus
        field = getattr(focused, "id", 0)
        if field in (0, 1):
            raise RuntimeError("No keyboard input destination; click a text field first")
        return Destination(0, window, str(field))

    def matches(self, target):
        if sys.platform == "darwin":
            pid = int(self.workspace.frontmostApplication().processIdentifier())
            app = self.ax.AXUIElementCreateApplication(pid)
            error, focused = self.ax.AXUIElementCopyAttributeValue(app, "AXFocusedUIElement", None)
            return pid == target.application and not error and focused == self.focused
        try:
            return self.destination() == target
        except (RuntimeError, subprocess.SubprocessError):
            return False

    def paste(self, target, text):
        if not self.matches(target):
            raise RuntimeError("Typing paused because focus changed. Copy the transcript from History")
        # The clipboard remains the latest phrase. Restoring it before another app
        # consumes the paste is unreliable, especially with X11 selection owners.
        import pyperclip
        pyperclip.copy(text)
        if not self.matches(target):
            raise RuntimeError("Focus changed before paste; transcript is available in History")
        if sys.platform == "darwin":
            q = self.quartz
            for down in (True, False):
                event = q.CGEventCreateKeyboardEvent(None, 9, down)  # ANSI V
                q.CGEventSetFlags(event, q.kCGEventFlagMaskCommand)
                q.CGEventPost(q.kCGHIDEventTap, event)
        else:
            subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"], check=True, timeout=3)


def desktop_entry(command, icon):
    # Desktop Entry Exec has its own quoting rules (not shell quoting).
    def quote(value):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'
    return "\n".join(["[Desktop Entry]", "Type=Application", "Name=Dictately", "Comment=Local English and Indonesian voice typing",
                       "Exec=" + " ".join(quote(x) for x in command), "Icon=" + str(icon), "Terminal=false", "Categories=Utility;Accessibility;", ""])


def install_launchers(root, python, startup=False, home=None):
    root, python = Path(root).resolve(), Path(python).absolute()
    home = Path(home) if home else Path.home()
    command = [str(python), str(root / "desktop_app.py")]
    if sys.platform == "darwin":
        bundle = home / "Applications/Dictately.app/Contents"
        executable = bundle / "MacOS/Dictately"
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text("#!/bin/sh\nexec " + " ".join(shlex.quote(x) for x in command) + '\n', encoding="utf-8")
        executable.chmod(0o755)
        (bundle / "Info.plist").write_bytes(plistlib.dumps({"CFBundleName":"Dictately", "CFBundleIdentifier":"dev.althafm.dictately.desktop",
            "CFBundleExecutable":"Dictately", "CFBundlePackageType":"APPL", "LSUIElement":True,
            "NSMicrophoneUsageDescription":"Dictately records speech only while you enable dictation with F12", "CFBundleIconFile":"dictately.icns"}))
        # Generate a native icon with macOS's own icon compiler.
        from PySide6.QtGui import QImage
        resources = bundle / "Resources"; resources.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory() as temporary:
            iconset=Path(temporary)/'Dictately.iconset';iconset.mkdir()
            image=QImage(str(root/'assets/dictately.png'))
            for size in (16,32,128,256,512):
                for scale in (1,2):
                    suffix='@2x' if scale==2 else ''
                    if not image.scaled(size*scale,size*scale).save(str(iconset/f'icon_{size}x{size}{suffix}.png')):
                        raise RuntimeError('Could not generate the application icon')
            subprocess.run(['iconutil','--convert','icns','--output',str(resources/'dictately.icns'),str(iconset)],check=True)
        if startup:
            agent = home / "Library/LaunchAgents/dev.althafm.dictately.plist"
            agent.parent.mkdir(parents=True, exist_ok=True)
            agent.write_bytes(plistlib.dumps({"Label":"dev.althafm.dictately", "ProgramArguments":[str(executable)], "RunAtLoad":True}))
        return home / "Applications/Dictately.app"
    entry = home / ".local/share/applications/dictately.desktop"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(desktop_entry(command, root / "assets/dictately.png"), encoding="utf-8")
    entry.chmod(0o755)
    if startup:
        autostart = home / ".config/autostart/dictately.desktop"
        autostart.parent.mkdir(parents=True, exist_ok=True)
        autostart.write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")
    return entry
