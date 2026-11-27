"""Download a supported local model, then select it (globally or for Indonesian only)."""
import argparse
import json
from pathlib import Path
import shutil

from speech_models import MODELS, MODEL_SUBFOLDERS
from runtime_paths import app_root

ROOT = app_root()
_CT2_FILES = (
    "model.bin",
    "config.json",
    "tokenizer.json",
    "vocabulary.json",
    "vocabulary.txt",
    "preprocessor_config.json",
)


def _model_ready(destination: Path) -> bool:
    return (
        all((destination / req).is_file() for req in ("model.bin", "config.json", "tokenizer.json"))
        and any((destination / fname).is_file() for fname in ("vocabulary.json", "vocabulary.txt"))
    )


def _download_model(name: str, root: Path, force: bool = False) -> None:
    directory, repo = MODELS[name]
    destination = root / "models" / directory
    if not force and name != "turbo" and _model_ready(destination):
        return
    if name == "turbo":
        from download_turbo import main as download
        download()
        return

    from huggingface_hub import HfApi, snapshot_download

    # Resolve a specific revision once so all files come from one snapshot.
    revision = HfApi().model_info(repo).sha
    subfolder = MODEL_SUBFOLDERS.get(name)
    patterns = [f"{subfolder}/{item}" for item in _CT2_FILES] if subfolder else list(_CT2_FILES)
    snapshot_download(
        repo_id=repo,
        revision=revision,
        local_dir=destination,
        allow_patterns=patterns,
    )
    if subfolder:
        nested = destination / subfolder
        for filename in _CT2_FILES:
            source = nested / filename
            if source.is_file():
                shutil.move(str(source), str(destination / filename))
        try:
            nested.rmdir()
        except OSError:
            pass
    for required in ("model.bin", "config.json", "tokenizer.json"):
        if not (destination / required).is_file():
            raise RuntimeError(f"Incomplete download: {required}")
    if not any((destination / fname).is_file() for fname in ("vocabulary.json", "vocabulary.txt")):
        raise RuntimeError("Incomplete download: vocabulary is missing")
    source_meta = {"repo": repo, "revision": revision}
    if subfolder:
        source_meta["subfolder"] = subfolder
    (destination / "source.json").write_text(json.dumps(source_meta, indent=2), encoding="utf-8")


def _write_selection(root: Path, data: dict) -> None:
    selection = root / "models/selected-model.json"
    selection.parent.mkdir(parents=True, exist_ok=True)
    temporary = selection.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(selection)


def _read_selection(root: Path) -> dict:
    selection = root / "models/selected-model.json"
    if selection.exists():
        try:
            data = json.loads(selection.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("model") in MODELS:
                return data
        except (OSError, ValueError):
            pass
    default = "turbo" if (root / "models/whisper-turbo/model.bin").exists() else "small"
    return {"model": default}


def select_model(model: str, for_indonesian: bool = False, root: Path | None = None) -> dict:
    """Download (if needed) and atomically persist the chosen model."""
    target_root = Path(root) if root is not None else ROOT
    current = _read_selection(target_root)
    if for_indonesian:
        if model in ("none", "same", "default"):
            current.pop("indonesian_model", None)
            _write_selection(target_root, current)
            return current
        if model not in MODELS:
            raise ValueError(f"Unknown model: {model}")
        _download_model(model, target_root)
        current["indonesian_model"] = model
        _write_selection(target_root, current)
        return current
    if model not in MODELS:
        raise ValueError(f"Unknown model: {model}")
    _download_model(model, target_root)
    current["model"] = model
    _write_selection(target_root, current)
    return current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", choices=list(MODELS) + ["none", "same"])
    parser.add_argument(
        "--for-indonesian",
        action="store_true",
        help="Use this model only when Indonesian is selected (keeps English on the default model). Pass 'none' to clear.",
    )
    args = parser.parse_args()
    if not args.for_indonesian and args.model in ("none", "same"):
        parser.error("Choose a real model name unless --for-indonesian is set")
    result = select_model(args.model, for_indonesian=args.for_indonesian, root=ROOT)
    if args.for_indonesian:
        if result.get("indonesian_model"):
            print(f"Selected {result['indonesian_model']} for Indonesian (default remains {result['model']}). Restart Dictately to apply. Dictation stays local/offline.")
        else:
            print(f"Cleared dedicated Indonesian model; both languages now use {result['model']}. Restart Dictately to apply.")
    else:
        print(f"Selected {args.model}. Restart Dictately to apply. Dictation stays local/offline.")


if __name__ == "__main__":
    main()
