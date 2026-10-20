# Build independent Python runtimes; no user's Python installation is needed.
from pathlib import Path
import site
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs, copy_metadata

root = Path(SPECPATH).parent
datas, binaries, hidden = [], [], []
for package in ("faster_whisper", "ctranslate2"):
    data, binary, imports = collect_all(package)
    datas += data
    binaries += binary
    hidden += imports
for distribution in ("huggingface-hub", "numpy", "onnxruntime", "requests", "tokenizers"):
    datas += copy_metadata(distribution)
for packages in site.getsitepackages():
    for component in ("cublas", "cudnn", "cuda_nvrtc"):
        directory = Path(packages) / "nvidia" / component / "bin"
        for dll in directory.glob("*.dll"):
            if dll.name.endswith(".alt.dll") or dll.name == "nvblas64_12.dll":
                continue
            # Opaque data avoids duplicating large GPU binaries at the bundle root.
            datas.append((str(dll), f"nvidia/{component}/bin"))

worker = Analysis([str(root / "installer_worker.py")], pathex=[str(root)],
                  binaries=binaries, datas=datas, hiddenimports=hidden,
                  excludes=["torch", "tensorflow", "matplotlib", "IPython", "pytest"])
worker_pyz = PYZ(worker.pure)
worker_exe = EXE(worker_pyz, worker.scripts, [], exclude_binaries=True,
                 name="DictatelyWorker", console=True, upx=False,
                 icon=str(root / "assets/dictately.ico"))
COLLECT(worker_exe, worker.binaries, worker.datas, name="DictatelyWorker", upx=False)

settings = Analysis([str(root / "settings_window.py")], pathex=[str(root)])
settings_pyz = PYZ(settings.pure)
settings_exe = EXE(settings_pyz, settings.scripts, [], exclude_binaries=True,
                   name="DictatelySettings", console=False, upx=False,
                   icon=str(root / "assets/dictately.ico"))
COLLECT(settings_exe, settings.binaries, settings.datas, name="DictatelySettings", upx=False)
