"""Verified, resumable range download for the larger speech model."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import requests
import tempfile
import threading

from runtime_paths import app_root
ROOT = app_root()
DEST = ROOT / "models/whisper-turbo"
REPO = "dropbox-dash/faster-whisper-large-v3-turbo"


def assemble_files(sources, destination, expected_size, expected_digest=None):
    """Bound temporary memory and promote only complete, verified output."""
    checksum=hashlib.sha256() if expected_digest else None
    total=0
    with tempfile.NamedTemporaryFile('wb',dir=destination.parent,prefix=destination.name+'.',suffix='.assembling',delete=False) as output:
        temporary=Path(output.name)
        try:
            for source in sources:
                with source.open('rb') as stream:
                    while block:=stream.read(1024*1024):
                        output.write(block);total+=len(block)
                        if checksum:checksum.update(block)
            output.close()
            if total!=expected_size:raise ValueError('Incomplete model assembly')
            digest=checksum.hexdigest() if checksum else None
            if expected_digest and digest!=expected_digest:raise ValueError('Model assembly checksum mismatch')
            temporary.replace(destination)
            return digest
        finally:
            output.close()
            temporary.unlink(missing_ok=True)

def discard_verified_parts(destination):
    """Remove only downloader-owned numeric fragments, after all hashes pass."""
    parts = destination / "download-parts"
    if not parts.exists() or parts.resolve().parent != destination.resolve():
        return
    chunks = parts / "chunks"
    for directory, parent in ((chunks, parts), (parts, destination)):
        if not directory.is_dir() or directory.resolve().parent != parent.resolve():
            continue
        for file in directory.iterdir():
            if file.name.isdecimal() and file.is_file():
                file.unlink()
        try:
            directory.rmdir()
        except OSError:
            pass  # Preserve unexpected files; never delete a whole arbitrary tree.

def main():
    spec = json.loads((ROOT / "assets/speech-model.json").read_text(encoding="utf-8"))
    revision = spec[0]["revision"]
    meta = {"siblings": [{"rfilename": item["file"], "lfs": {"size": item["bytes"], "sha256": item["sha256"]}} for item in spec]}
    DEST.mkdir(parents=True, exist_ok=True)
    manifest = []
    for entry in meta["siblings"]:
        name = entry["rfilename"]
        if name not in {"model.bin", "config.json", "tokenizer.json", "vocabulary.json", "preprocessor_config.json"}:
            continue
        url = f"https://huggingface.co/{REPO}/resolve/{revision}/{name}?download=true"
        target = DEST / name
        digest = None
        expected = entry.get("lfs", {}).get("sha256")
        if target.exists():
            with target.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if expected and digest != expected:
                target.unlink()
                digest = None
                print(f"Repairing incomplete or invalid {name}", flush=True)
        if name == "model.bin" and not target.exists():
            size = entry["lfs"]["size"]
            step = 16 * 1024 * 1024
            parts = DEST / "download-parts"
            parts.mkdir(exist_ok=True)
            tiny = parts / "chunks"
            tiny.mkdir(exist_ok=True)
            count = (size + step - 1) // step
            missing = []
            for index in range(count):
                start, end = index * step, min(size, (index + 1) * step) - 1
                path = parts / f"{index:04d}"
                if not path.exists() or path.stat().st_size != end-start+1:
                    missing.extend((offset, min(end, offset + 1024 * 1024 - 1)) for offset in range(start, end+1, 1024 * 1024))
            local=threading.local();sessions=[];session_lock=threading.Lock()
            def small_part(bounds):
                offset, stop = bounds
                path = tiny / str(offset)
                if path.exists() and path.stat().st_size == stop-offset+1:
                    return
                if not hasattr(local,'session'):
                    local.session=requests.Session()
                    with session_lock:sessions.append(local.session)
                for attempt in range(8):
                    response=None
                    try:
                        response = local.session.get(url + f"&offset={offset}&try={attempt}", headers={"Range": f"bytes={offset}-{stop}"}, timeout=(10, 20),stream=True)
                        response.raise_for_status()
                        if response.status_code != 206 or response.headers.get("Content-Range") != f"bytes {offset}-{stop}/{size}":
                            raise RuntimeError("Unexpected download range")
                        payload=response.raw.read(stop-offset+2,decode_content=True)
                        if len(payload)!=stop-offset+1:raise RuntimeError('Invalid download range length')
                        path.write_bytes(payload)
                        return
                    except Exception:
                        if attempt == 7:
                            raise
                    finally:
                        if response is not None:response.close()
            try:
                with ThreadPoolExecutor(max_workers=32) as pool:
                    for done, future in enumerate(as_completed([pool.submit(small_part, bounds) for bounds in missing]), 1):
                        future.result()
                        if done % 32 == 0:
                            print(f"Downloaded {done}/{len(missing)} remaining MiB", flush=True)
            finally:
                for session in sessions:session.close()
            def part(index):
                start, end = index * step, min(size, (index + 1) * step) - 1
                path = parts / f"{index:04d}"
                if path.exists() and path.stat().st_size == end - start + 1:
                    return path
                chunks=[tiny/str(offset) for offset in range(start,end+1,1024*1024)]
                assemble_files(chunks,path,end-start+1)
                print(f"Assembled part {index + 1}/{count}", flush=True)
                return path
            count = (size + step - 1) // step
            with ThreadPoolExecutor(max_workers=16) as pool:
                files = list(pool.map(part, range(count)))
            try:
                digest=assemble_files(files,target,size,expected)
            except ValueError:
                discard_verified_parts(DEST)
                raise
            print(f"Assembled {name}: {size / 1024**2:.0f} MiB", flush=True)
        elif not target.exists():
            response = requests.get(url, timeout=(20, 90))
            response.raise_for_status()
            target.write_bytes(response.content)
        if digest is None:
            with target.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if expected and expected != digest:
            target.unlink()
            if name == "model.bin":
                discard_verified_parts(DEST)
            raise RuntimeError(f"Digest mismatch: {name}")
        manifest.append({"repo": REPO, "revision": revision, "file": str(target.relative_to(ROOT)), "sha256": digest, "bytes": target.stat().st_size})
        print(f"Verified {name}", flush=True)
    (DEST / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    try:
        discard_verified_parts(DEST)
    except OSError as error:
        print(f"Verified model is ready; temporary-file cleanup deferred: {error}", flush=True)

if __name__ == "__main__":
    main()
