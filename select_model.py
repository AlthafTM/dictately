"""Download a supported local model, then select it for the next app launch."""
import argparse
import json
from pathlib import Path
from speech_models import MODELS

from runtime_paths import app_root
ROOT = app_root()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", choices=MODELS)
    args = parser.parse_args()
    directory, repo = MODELS[args.model]
    if args.model == "turbo":
        from download_turbo import main as download
        download()
    else:
        from huggingface_hub import HfApi, snapshot_download
        # Resolve a specific revision once so all files come from one snapshot.
        revision = HfApi().model_info(repo).sha
        destination = ROOT / "models" / directory
        snapshot_download(repo_id=repo, revision=revision, local_dir=destination,
                          allow_patterns=["model.bin", "config.json", "tokenizer.json",
                                          "vocabulary.json", "vocabulary.txt", "preprocessor_config.json"])
        for required in ("model.bin", "config.json", "tokenizer.json"):
            if not (destination / required).is_file():
                raise RuntimeError(f"Incomplete download: {required}")
        if not any((destination/name).is_file() for name in ('vocabulary.json','vocabulary.txt')):
            raise RuntimeError('Incomplete download: vocabulary is missing')
        (destination / "source.json").write_text(
            json.dumps({"repo": repo, "revision": revision}, indent=2), encoding="utf-8")
    selection = ROOT / "models/selected-model.json"
    temporary = selection.with_suffix(".tmp")
    temporary.write_text(json.dumps({"model": args.model}), encoding="utf-8")
    temporary.replace(selection)
    print(f"Selected {args.model}. Restart Transcribr to apply. Dictation stays local/offline.")


if __name__ == "__main__":
    main()
