"""Local model selection shared by the installer, settings UI, and inference worker."""
import json
from pathlib import Path

# Tuple format: (local_directory, huggingface_repo) — preserved for backward compatibility.
MODELS = {
    "turbo": ("whisper-turbo", "dropbox-dash/faster-whisper-large-v3-turbo"),
    "small": ("whisper-small", "Systran/faster-whisper-small"),
    "large-v3": ("whisper-large-v3", "Systran/faster-whisper-large-v3"),
    "turbo-id": ("whisper-turbo-id", "maleo-ai/whisper-large-v3-turbo-id"),
    "small-id": ("whisper-small-id", "maleo-ai/whisper-small-id"),
}

# Optional subfolder inside the Hugging Face repo where CTranslate2 files live.
MODEL_SUBFOLDERS = {
    "turbo-id": "ct2-int8",
    "small-id": "ct2-int8",
}

# Fine-tuned Indonesian checkpoints that output lowercase without punctuation.
LOWERCASE_MODELS = frozenset({"whisper-turbo-id", "whisper-small-id"})

MODEL_DESCRIPTIONS = {
    "turbo": "Whisper large-v3-turbo — default EN/ID (~1.5 GB)",
    "small": "Whisper small multilingual — lighter CPU/GPU (~460 MB)",
    "large-v3": "Whisper large-v3 — full decoder (~3.0 GB)",
    "turbo-id": "Whisper large-v3-turbo-id (maleo-ai) — Indonesian fine-tuned INT8 (~776 MB)",
    "small-id": "Whisper small-id (maleo-ai) — lightweight Indonesian fine-tuned INT8 (~233 MB)",
}


def installed_models(root) -> list[str]:
    root = Path(root)
    return [name for name, (directory, _) in MODELS.items() if (root / "models" / directory / "model.bin").is_file()]


def selected_model_config(root) -> dict:
    """Return {'model': <name>, 'indonesian_model': <name or None>}."""
    root = Path(root)
    selection = root / "models/selected-model.json"
    if selection.exists():
        data = json.loads(selection.read_text(encoding="utf-8"))
        name = data.get("model")
        if name not in MODELS:
            raise ValueError("Unknown speech model; run app/select_model.py turbo to reset")
        id_name = data.get("indonesian_model")
        if id_name is not None and id_name != "":
            if id_name not in MODELS:
                raise ValueError("Unknown Indonesian speech model; run app/select_model.py --for-indonesian none to reset")
        else:
            id_name = None
        return {"model": name, "indonesian_model": id_name}
    default = "turbo" if (root / "models/whisper-turbo/model.bin").exists() else "small"
    return {"model": default, "indonesian_model": None}


def selected_model(root, language: str | None = None) -> str:
    """Resolve the model directory name for the requested language ('en' or 'id')."""
    root = Path(root)
    selection = root / "models/selected-model.json"
    if selection.exists():
        config = selected_model_config(root)
        chosen = config["indonesian_model"] if (language == "id" and config["indonesian_model"]) else config["model"]
        directory = MODELS[chosen][0]
        if not (root / "models" / directory / "model.bin").is_file():
            raise FileNotFoundError(f"Selected model {chosen} is missing; reinstall with app/select_model.py")
        return directory
    return "whisper-turbo" if (root / "models/whisper-turbo/model.bin").exists() else "whisper-small"


def is_lowercase_model(model_dir: str) -> bool:
    return model_dir in LOWERCASE_MODELS
