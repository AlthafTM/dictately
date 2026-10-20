"""Package explicit app/runtime files, including runtime dependency licenses."""
import importlib.metadata
from pathlib import Path
import shutil
import sys
import zipfile

stage = Path(sys.argv[1]).resolve()
licenses = stage / "licenses/runtime"
licenses.mkdir(parents=True, exist_ok=True)
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.exists():
    shutil.copyfile(python_license, licenses / "Python-LICENSE.txt")
for distribution in importlib.metadata.distributions():
    for file in distribution.files or []:
        if ".dist-info/licenses/" in str(file).replace("\\", "/") or file.name.lower().startswith(("license", "copying", "notice")):
            source = distribution.locate_file(file)
            if source.is_file():
                target = licenses / distribution.metadata["Name"] / file.name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
payload = stage / "payload.zip"
with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
    for file in sorted(stage.rglob("*")):
        if file.is_file() and file != payload:
            relative = file.relative_to(stage)
            if relative.parts[0] in {".venv", "models", "qa", ".tools"}:
                raise ValueError(f"Unexpected runtime/private data: {relative}")
            archive.write(file, relative.as_posix())
print(f"Payload: {payload.stat().st_size / 1024**2:.1f} MB", flush=True)
