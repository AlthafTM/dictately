"""Resolve application assets in source and self-contained installations."""
import os
import shutil
import json
import stat
import tempfile
from pathlib import Path
import sys


def private_data_dir(path):
    path = Path(path)
    if os.name == "nt":
        return path
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        if os.fstat(descriptor).st_uid != os.getuid():
            raise PermissionError("Dictately data directory must belong to the current user")
        os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)
    for name in ("settings.json", "history.json", "desktop-settings.json", "desktop-history.json",
                 "desktop.log", "desktop.log.1", "desktop.log.2"):
        try:
            descriptor = os.open(path / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            continue
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise PermissionError("Dictately state must be a regular file owned by the current user")
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
    return path


def write_private_json(path, value):
    path = Path(path)
    private_data_dir(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise PermissionError("Dictately state files cannot be symbolic links")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            json.dump(value, output, ensure_ascii=False, indent=2)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def app_root():
    override = os.environ.get("DICTATELY_ROOT") or os.environ.get("TRANSCRIBR_ROOT")
    if override:
        return Path(override).resolve()
    if getattr(sys, "frozen", False):
        # <app>/runtime/{worker,settings}/<executable>
        return Path(sys.executable).resolve().parents[2]
    return Path(__file__).resolve().parents[1]


def user_data_dir():
    """Keep platform data out of the application/source directory."""
    override = os.environ.get("DICTATELY_DATA") or os.environ.get("LOCALFLOW_DATA")
    if override:
        return private_data_dir(Path(override).expanduser())
    if sys.platform == "win32":
        parent = Path(os.environ["LOCALAPPDATA"])
        destination = parent / "Dictately"
        previous = [parent / name for name in ("Transcribr", "Transcriber", "LocalFlow")]
    elif sys.platform == "darwin":
        parent = Path.home() / "Library/Application Support"
        destination = parent / "Dictately"
        previous = [parent / "Transcribr"]
    else:
        parent = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
        destination = parent / "dictately"
        previous = [parent / "transcribr"]
    destination = private_data_dir(destination)
    for directory in previous:
        for name in ("settings.json", "history.json"):
            source, target = directory / name, destination / name
            if source.is_file() and not target.exists():
                destination.mkdir(parents=True, exist_ok=True)
                # Exclusive creation avoids overwriting settings another process
                # just migrated or saved. Original files remain available.
                try:
                    with source.open("rb") as old, target.open("xb") as new:
                        if os.name != "nt":
                            os.fchmod(new.fileno(), 0o600)
                        if name=="settings.json":
                            contents=old.read()
                            try:
                                settings=json.loads(contents)
                                if settings.get("language_hotkey")==122 and settings.get("dictation_hotkey",123)!=121:
                                    settings["language_hotkey"]=121
                                    contents=json.dumps(settings,ensure_ascii=False,indent=2).encode("utf-8")
                            except (ValueError,AttributeError,UnicodeError):
                                pass
                            new.write(contents)
                        else:
                            shutil.copyfileobj(old, new)
                except FileExistsError:
                    pass
    return destination


_dll_handles = []


def configure_gpu_libraries():
    if os.name != "nt":
        return
    import site
    bases = [Path(path) for path in site.getsitepackages()]
    if getattr(sys, "frozen", False):
        bases.insert(0, Path(sys._MEIPASS))
    paths = []
    for base in bases:
        for component in ("cublas", "cudnn", "cuda_nvrtc"):
            directory = base / "nvidia" / component / "bin"
            if directory.is_dir():
                _dll_handles.append(os.add_dll_directory(str(directory)))
                paths.append(str(directory))
    os.environ["PATH"] = os.pathsep.join(paths + [os.environ.get("PATH", "")])
