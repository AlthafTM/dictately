"""Local model selection shared by the installer and inference worker."""
import json
from pathlib import Path

MODELS = {
    "turbo": ("whisper-turbo", "dropbox-dash/faster-whisper-large-v3-turbo"),
    "small": ("whisper-small", "Systran/faster-whisper-small"),
    "large-v3": ("whisper-large-v3", "Systran/faster-whisper-large-v3"),
}


def selected_model(root):
    root = Path(root)
    selection = root / "models/selected-model.json"
    if selection.exists():
        name = json.loads(selection.read_text(encoding="utf-8"))["model"]
        if name not in MODELS:
            raise ValueError("Unknown speech model; run app/select_model.py turbo to reset")
        directory = MODELS[name][0]
        if not (root / "models" / directory / "model.bin").is_file():
            raise FileNotFoundError(f"Selected model {name} is missing; reinstall with app/select_model.py")
        return directory
    return "whisper-turbo" if (root / "models/whisper-turbo/model.bin").exists() else "whisper-small"
