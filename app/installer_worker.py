"""Bundled worker entry point, including setup and local model management."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from runtime_paths import app_root, configure_gpu_libraries


def install_model(cache=None):
    root = app_root()
    if cache:
        cache = Path(cache).resolve()
        manifest = json.loads((root / "assets/speech-model.json").read_text(encoding="utf-8"))
        destination = root / "models/whisper-turbo"
        destination.mkdir(parents=True, exist_ok=True)
        for item in manifest:
            name = Path(item["file"]).name
            source = cache / name
            with source.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != item["sha256"]:
                    raise ValueError(f"Cached speech model failed verification: {name}")
            target = destination / name
            if source != target:
                temporary = target.with_suffix(target.suffix + ".installing")
                shutil.copyfile(source, temporary)
                temporary.replace(target)
            print(f"Verified cached {name}", flush=True)
    from download_turbo import main as download
    download()
    configure_gpu_libraries()
    import ctranslate2
    from faster_whisper import WhisperModel
    import onnxruntime
    import numpy
    print("Runtime and local speech model are ready", flush=True)


def main():
    if "--install-model" in sys.argv:
        parser = argparse.ArgumentParser()
        parser.add_argument("--install-model", action="store_true")
        parser.add_argument("--model-cache")
        args = parser.parse_args()
        install_model(args.model_cache)
    elif "--select-model" in sys.argv:
        from select_model import main as select
        sys.argv.remove("--select-model")
        select()
    elif "--self-check" in sys.argv:
        from live_worker import Worker
        worker = Worker()
        worker.prepare()
        print(json.dumps({"ready": True, "device": worker.device, "model": worker.model_name}), flush=True)
        worker.engine.close()
    else:
        from live_worker import main as run
        run()


if __name__ == "__main__":
    main()
