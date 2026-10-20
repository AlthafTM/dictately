"""Resolve application assets in source and self-contained installations."""
import os
from pathlib import Path
import sys


def app_root():
    override = os.environ.get("TRANSCRIBR_ROOT")
    if override:
        return Path(override).resolve()
    if getattr(sys, "frozen", False):
        # <app>/runtime/{worker,settings}/<executable>
        return Path(sys.executable).resolve().parents[2]
    return Path(__file__).resolve().parent


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
