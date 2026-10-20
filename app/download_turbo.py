"""Verified, resumable range download for the larger speech model."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import time
import requests

from runtime_paths import app_root
ROOT = app_root()
DEST = ROOT / "models/whisper-turbo"
REPO = "dropbox-dash/faster-whisper-large-v3-turbo"

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
            def small_part(bounds):
                offset, stop = bounds
                path = tiny / str(offset)
                if path.exists() and path.stat().st_size == stop-offset+1:
                    return
                for attempt in range(8):
                    try:
                        response = requests.get(url + f"&offset={offset}&try={attempt}", headers={"Range": f"bytes={offset}-{stop}"}, timeout=(10, 20))
                        response.raise_for_status()
                        if response.status_code != 206 or response.headers.get("Content-Range") != f"bytes {offset}-{stop}/{size}" or len(response.content) != stop-offset+1:
                            raise RuntimeError("Unexpected download range")
                        path.write_bytes(response.content)
                        return
                    except Exception:
                        if attempt == 7:
                            raise
            with ThreadPoolExecutor(max_workers=32) as pool:
                for done, future in enumerate(as_completed([pool.submit(small_part, bounds) for bounds in missing]), 1):
                    future.result()
                    if done % 32 == 0:
                        print(f"Downloaded {done}/{len(missing)} remaining MiB", flush=True)
            def part(index):
                start, end = index * step, min(size, (index + 1) * step) - 1
                path = parts / f"{index:04d}"
                if path.exists() and path.stat().st_size == end - start + 1:
                    return path
                for attempt in range(1):
                    try:
                        chunks = []
                        for offset in range(start, end + 1, 1024 * 1024):
                            stop = min(end, offset + 1024 * 1024 - 1)
                            chunks.append((tiny / str(offset)).read_bytes())
                        path.write_bytes(b"".join(chunks))
                        print(f"Downloaded part {index + 1}/{count}", flush=True)
                        return path
                    except Exception:
                        if attempt == 3:
                            raise
                        time.sleep(attempt + 1)
            count = (size + step - 1) // step
            with ThreadPoolExecutor(max_workers=16) as pool:
                files = list(pool.map(part, range(count)))
            temporary = target.with_suffix(".downloading")
            with temporary.open("wb") as output:
                for path in files:
                    output.write(path.read_bytes())
            temporary.replace(target)
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
