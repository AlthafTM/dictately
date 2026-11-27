"""On-demand settings/history UI; the always-running controller is native Rust."""
from datetime import datetime
import json
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import winreg

from runtime_paths import app_root, user_data_dir, write_private_json
from hotkeys import capture_shortcut, shortcut_name, valid_shortcut, DICTATION_DEFAULT, LANGUAGE_DEFAULT
from ui_preferences import POSITIONS, position_name
from speech_models import MODELS, MODEL_DESCRIPTIONS, installed_models, selected_model_config

ROOT = app_root()
DATA = user_data_dir()
BG, CARD, FIELD, TEXT, MUTED, ACCENT = "#111111", "#1d1d1d", "#2a2a2a", "#f1f1f1", "#aaaaaa", "#e5e5e5"
LANGUAGES = {"English only": "en", "Indonesian only": "id"}


def read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write(path, value):
    write_private_json(path, value)


def parse_mapping_lines(raw_text: str, left_key: str, right_key: str, limit: int = 80) -> list[dict]:
    """Parse 'left => right' (or 'left -> right') lines into validated JSON-ready objects."""
    entries = []
    seen = set()
    for line in str(raw_text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        sep = "=>" if "=>" in line else "->" if "->" in line else None
        if not sep:
            continue
        left, right = line.split(sep, 1)
        left = left.strip()[:100]
        right = right.strip().replace("\\n", "\n")[:500]
        if not left or not right:
            continue
        key = left.casefold()
        if key in seen:
            continue
        seen.add(key)
        entries.append({left_key: left, right_key: right})
        if len(entries) >= limit:
            break
    return entries


def format_mapping_lines(entries, left_key: str, right_key: str) -> str:
    if not isinstance(entries, list):
        return ""
    lines = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        left = str(item.get(left_key) or "").strip()
        right = str(item.get(right_key) or "").replace("\n", "\\n").strip()
        if left and right:
            lines.append(f"{left} => {right}")
    return "\n".join(lines)


def startup(enabled=None):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if enabled is None:
            try:
                return bool(winreg.QueryValueEx(key, "Dictately")[0])
            except FileNotFoundError:
                return False
        if enabled:
            winreg.SetValueEx(key, "Dictately", 0, winreg.REG_SZ, f'"{ROOT / "Dictately.exe"}"')
        else:
            try:
                winreg.DeleteValue(key, "Dictately")
            except FileNotFoundError:
                pass


class Window:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Dictately: History & settings")
        self.root.iconbitmap(default=str(ROOT / "assets/dictately.ico"))
        self.root.configure(bg=BG)
        width, height = 880, 740
        self.root.geometry(f"{width}x{height}+{max(0,(self.root.winfo_screenwidth()-width)//2)}+{max(0,(self.root.winfo_screenheight()-height)//2)}")
        self.root.minsize(760, 640)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 11))
        style.configure("Muted.TLabel", foreground=MUTED, font=("Segoe UI", 10))
        style.configure("TButton", background=FIELD, foreground=TEXT, borderwidth=0, padding=(16, 9), font=("Segoe UI", 10))
        style.map("TButton", background=[("active", "#3b3b3b")])
        style.configure("Accent.TButton", background=ACCENT, foreground=BG)
        style.map("Accent.TButton", background=[("active", "#ffffff")])
        style.configure("TCheckbutton", background=BG, foreground=TEXT, font=("Segoe UI", 11), padding=(0, 5))
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(16, 10), font=("Segoe UI", 10))
        style.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", TEXT)])
        style.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=TEXT, padding=7, arrowsize=16)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)], selectbackground=[("readonly", FIELD)])
        style.configure("TEntry", fieldbackground=FIELD, foreground=TEXT, padding=9, borderwidth=0)
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)

        outer = ttk.Frame(self.root, padding=24)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="Dictately", font=("Segoe UI", 24, "bold")).pack(side="left")
        ttk.Label(header, text="●  ON DEVICE", foreground=ACCENT, font=("Segoe UI", 10, "bold")).pack(side="right")
        ttk.Label(outer, text="Your voice, right where you type.", style="Muted.TLabel").pack(anchor="w", pady=(3, 16))

        tabs = ttk.Notebook(outer)
        tabs.pack(fill="both", expand=True)
        history = ttk.Frame(tabs, padding=(0, 16))
        settings = ttk.Frame(tabs, padding=(0, 18))
        dictionary_tab = ttk.Frame(tabs, padding=(0, 18))
        shortcuts = ttk.Frame(tabs, padding=(0, 18))
        preferences = ttk.Frame(tabs, padding=(0, 18))
        tabs.add(history, text="Recent dictations")
        tabs.add(settings, text="Settings & models")
        tabs.add(dictionary_tab, text="Dictionary & snippets")
        tabs.add(shortcuts, text="Keyboard shortcuts")
        tabs.add(preferences, text="Audio & appearance")

        # --- History tab ---
        self.list = tk.Listbox(history, height=6, bg=CARD, fg=TEXT, selectbackground="#444444", selectforeground=TEXT,
            font=("Segoe UI", 11), bd=0, highlightthickness=0, activestyle="none", exportselection=False)
        self.list.pack(fill="x")
        self.list.bind("<<ListboxSelect>>", lambda _: self.show_entry())
        self.detail = tk.Text(history, wrap="word", font=("Segoe UI", 11), bg=CARD, fg=TEXT,
            bd=0, highlightthickness=0, padx=18, pady=15, height=8)
        self.detail.pack(fill="both", expand=True, pady=(12, 12))
        buttons = ttk.Frame(history)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Copy cleaned", style="Accent.TButton", command=lambda: self.copy("cleaned")).pack(side="left")
        ttk.Button(buttons, text="Copy original", command=lambda: self.copy("raw")).pack(side="left", padx=8)
        ttk.Button(buttons, text="Clear history", command=self.clear).pack(side="right")

        current = read(DATA / "settings.json", {})

        # --- Audio & appearance tab ---
        ttk.Label(preferences, text="Microphone", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(preferences, text="Follow the Windows default or remember a specific microphone", style="Muted.TLabel").pack(anchor="w", pady=(5, 12))
        row = ttk.Frame(preferences)
        row.pack(fill="x")
        self.microphone = tk.StringVar()
        self.microphone_choices = {"System default": ""}
        self.microphone_box = ttk.Combobox(row, textvariable=self.microphone, state="readonly", font=("Segoe UI", 11))
        self.microphone_box.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Refresh", command=self.refresh_microphones).pack(side="right", padx=(10, 0))
        ttk.Label(preferences, text="Waveform position", font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(24, 10))
        self.overlay_position = tk.StringVar(value=position_name(current.get("overlay_position")))
        ttk.Combobox(preferences, values=list(POSITIONS), textvariable=self.overlay_position, state="readonly", font=("Segoe UI", 11)).pack(fill="x")
        ttk.Label(preferences, text="Position applies to the waveform and language picker on the active screen", style="Muted.TLabel", wraplength=700).pack(anchor="w", pady=(10, 20))
        self.preferences_feedback = tk.StringVar()
        ttk.Label(preferences, textvariable=self.preferences_feedback, style="Muted.TLabel", wraplength=700).pack(anchor="w", pady=(0, 12))
        ttk.Button(preferences, text="Save preferences", style="Accent.TButton", command=self.save_preferences).pack(anchor="e")
        self.refresh_microphones(current.get("microphone", ""))

        # --- Settings & models tab ---
        language_code = current.get("language", "en")
        self.language = tk.StringVar(value=next((name for name, code in LANGUAGES.items() if code == language_code), next(iter(LANGUAGES))))
        ttk.Label(settings, text="Recognition language", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(settings, text="Choose English or Indonesian. Scroll, drag, or speak in the language picker.", style="Muted.TLabel").pack(anchor="w", pady=(3, 8))
        ttk.Combobox(settings, values=list(LANGUAGES), textvariable=self.language, state="readonly", font=("Segoe UI", 11)).pack(fill="x")

        self.cleanup = tk.BooleanVar(value=current.get("cleanup", True))
        self.formal_indonesian = tk.BooleanVar(value=current.get("formal_indonesian", False))
        self.accuracy = tk.BooleanVar(value=current.get("accuracy", True))
        self.save_history = tk.BooleanVar(value=current.get("save_history", True))
        self.startup = tk.BooleanVar(value=startup())
        ttk.Checkbutton(settings, text="Remove hesitation sounds, repeated pronouns, and normalize Indonesian kata ulang", variable=self.cleanup).pack(anchor="w", pady=(12, 0))
        ttk.Checkbutton(settings, text="Normalize casual Indonesian to formal / baku (gak → tidak, udah → sudah)", variable=self.formal_indonesian).pack(anchor="w")
        ttk.Checkbutton(settings, text="Higher recognition accuracy (wider beam)", variable=self.accuracy).pack(anchor="w")
        ttk.Checkbutton(settings, text="Save dictation history locally on this computer", variable=self.save_history).pack(anchor="w")
        ttk.Checkbutton(settings, text="Start quietly when I sign in to Windows", variable=self.startup).pack(anchor="w")

        ttk.Label(settings, text="Local speech models", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(16, 3))
        ttk.Label(settings, text="Use turbo-id or small-id specifically for Indonesian while keeping English on Turbo.", style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        self.default_model_var = tk.StringVar()
        self.id_model_var = tk.StringVar()
        self.default_model_map = {}
        self.id_model_map = {}

        m_row1 = ttk.Frame(settings)
        m_row1.pack(fill="x", pady=(0, 6))
        ttk.Label(m_row1, text="Default / English:", width=18).pack(side="left")
        self.default_model_box = ttk.Combobox(m_row1, textvariable=self.default_model_var, state="readonly", font=("Segoe UI", 10))
        self.default_model_box.pack(side="left", fill="x", expand=True)

        m_row2 = ttk.Frame(settings)
        m_row2.pack(fill="x", pady=(0, 8))
        ttk.Label(m_row2, text="Indonesian model:", width=18).pack(side="left")
        self.id_model_box = ttk.Combobox(m_row2, textvariable=self.id_model_var, state="readonly", font=("Segoe UI", 10))
        self.id_model_box.pack(side="left", fill="x", expand=True)
        self._refresh_model_choices()

        self.model_busy = False
        self.feedback = tk.StringVar(value="Changes apply to your next dictation. Model downloads run in the background.")
        ttk.Label(settings, textvariable=self.feedback, style="Muted.TLabel", wraplength=760).pack(anchor="w", pady=(10, 10))
        s_actions = ttk.Frame(settings)
        s_actions.pack(fill="x")
        self.apply_model_btn = ttk.Button(s_actions, text="Apply / download models", command=self.apply_models)
        self.apply_model_btn.pack(side="left")
        ttk.Button(s_actions, text="Save settings", style="Accent.TButton", command=self.save).pack(side="right")

        # --- Dictionary & snippets tab ---
        ttk.Label(dictionary_tab, text="Personal vocabulary", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(dictionary_tab, text="Names, acronyms, and technical terms separated by commas (e.g. KTP, QRIS, GoPay, Node.js, Telkom University).", style="Muted.TLabel", wraplength=780).pack(anchor="w", pady=(3, 8))
        self.vocabulary = tk.StringVar(value=current.get("vocabulary", ""))
        ttk.Entry(dictionary_tab, textvariable=self.vocabulary, font=("Segoe UI", 11)).pack(fill="x")

        ttk.Label(dictionary_tab, text="Word replacements  (source => target, one per line)", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(16, 3))
        ttk.Label(dictionary_tab, text="Fix recurring misrecognitions or expand abbreviations (e.g. jaksel => Jakarta Selatan).", style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        self.replacements_text = tk.Text(dictionary_tab, height=5, wrap="word", font=("Consolas", 10), bg=CARD, fg=TEXT, insertbackground=TEXT, bd=0, highlightthickness=0, padx=12, pady=10)
        self.replacements_text.pack(fill="x")
        self.replacements_text.insert("1.0", format_mapping_lines(current.get("replacements", []), "source", "target"))

        ttk.Label(dictionary_tab, text="Voice snippets  (trigger => expansion, one per line)", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(16, 3))
        ttk.Label(dictionary_tab, text="Variables: {{tanggal}}, {{waktu}}, {{date}}, {{time}}, {{datetime}}. Use \\n for line breaks.", style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        self.snippets_text = tk.Text(dictionary_tab, height=6, wrap="word", font=("Consolas", 10), bg=CARD, fg=TEXT, insertbackground=TEXT, bd=0, highlightthickness=0, padx=12, pady=10)
        self.snippets_text.pack(fill="both", expand=True)
        self.snippets_text.insert("1.0", format_mapping_lines(current.get("snippets", []), "trigger", "replacement"))

        self.dict_feedback = tk.StringVar(value="Triggers are also sent as recognition hints (capped to Whisper's prompt budget).")
        ttk.Label(dictionary_tab, textvariable=self.dict_feedback, style="Muted.TLabel").pack(anchor="w", pady=(10, 8))
        ttk.Button(dictionary_tab, text="Save dictionary & snippets", style="Accent.TButton", command=self.save_dictionary).pack(anchor="e")

        # --- Keyboard shortcuts tab ---
        self.dictation_hotkey = current.get("dictation_hotkey", DICTATION_DEFAULT)
        self.language_hotkey = current.get("language_hotkey", LANGUAGE_DEFAULT)
        if not valid_shortcut(self.dictation_hotkey):
            self.dictation_hotkey = DICTATION_DEFAULT
        if not valid_shortcut(self.language_hotkey) or self.language_hotkey == self.dictation_hotkey:
            self.dictation_hotkey = DICTATION_DEFAULT
            self.language_hotkey = LANGUAGE_DEFAULT
        ttk.Label(shortcuts, text="Make Dictately fit your keyboard", font=("Segoe UI", 15, "bold")).pack(anchor="w", pady=(0, 10))
        ttk.Label(shortcuts, text="Click a shortcut, then press a key or combination. These shortcuts work globally.", style="Muted.TLabel").pack(anchor="w", pady=(0, 25))
        self.shortcut_buttons = {}
        for field, label in [("dictation_hotkey", "Open / close microphone"), ("language_hotkey", "Choose language")]:
            row = ttk.Frame(shortcuts)
            row.pack(fill="x", pady=8)
            ttk.Label(row, text=label).pack(side="left")
            button = ttk.Button(row, text=shortcut_name(getattr(self, field)), command=lambda name=field: self.assign_shortcut(name))
            button.pack(side="right")
            self.shortcut_buttons[field] = button
        self.shortcut_feedback = tk.StringVar(value="Defaults: F12 dictation · F10 language")
        ttk.Label(shortcuts, textvariable=self.shortcut_feedback, style="Muted.TLabel").pack(anchor="w", pady=(20, 10))
        actions = ttk.Frame(shortcuts)
        actions.pack(fill="x", pady=12)
        ttk.Button(actions, text="Restore defaults", command=self.reset_shortcuts).pack(side="left")
        ttk.Button(actions, text="Save shortcuts", style="Accent.TButton", command=self.save_shortcuts).pack(side="right")

        if "--shortcuts" in sys.argv:
            tabs.select(shortcuts)
        if "--preferences" in sys.argv:
            tabs.select(preferences)
        if "--dictionary" in sys.argv:
            tabs.select(dictionary_tab)

        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(12, 0))
        ttk.Button(bottom, text="Keyboard shortcuts", command=lambda: tabs.select(shortcuts)).pack(side="left")
        ttk.Label(bottom, text="Local EN / ID phrase dictation", style="Muted.TLabel").pack(side="right")
        self.rows = []
        self.refresh()
        self.root.after(2000, self.poll)

    def _refresh_model_choices(self):
        installed = set(installed_models(ROOT))
        try:
            config = selected_model_config(ROOT)
        except (OSError, ValueError):
            config = {"model": "turbo", "indonesian_model": None}

        self.default_model_map = {}
        for name in ("turbo", "small", "large-v3"):
            tag = " [installed]" if name in installed else " [download]"
            label = f"{MODEL_DESCRIPTIONS[name]}{tag}"
            self.default_model_map[label] = name
        self.default_model_box.configure(values=list(self.default_model_map))
        cur_default = config["model"] if config["model"] in ("turbo", "small", "large-v3") else "turbo"
        self.default_model_var.set(next((lbl for lbl, code in self.default_model_map.items() if code == cur_default), next(iter(self.default_model_map))))

        self.id_model_map = {"Same as default model": "none"}
        for name in ("turbo-id", "small-id", "large-v3", "turbo", "small"):
            tag = " [installed]" if name in installed else " [download]"
            label = f"{MODEL_DESCRIPTIONS[name]}{tag}"
            self.id_model_map[label] = name
        self.id_model_box.configure(values=list(self.id_model_map))
        cur_id = config.get("indonesian_model") or "none"
        self.id_model_var.set(next((lbl for lbl, code in self.id_model_map.items() if code == cur_id), "Same as default model"))

    def apply_models(self):
        if self.model_busy:
            return
        default_choice = self.default_model_map[self.default_model_var.get()]
        id_choice = self.id_model_map[self.id_model_var.get()]
        self.model_busy = True
        self.apply_model_btn.configure(state="disabled")
        self.feedback.set("Downloading / applying selected model(s)… keep this window open.")

        def worker():
            try:
                from select_model import select_model
                select_model(default_choice, for_indonesian=False, root=ROOT)
                select_model(id_choice, for_indonesian=True, root=ROOT)
                msg = f"Saved models: default={default_choice}" + (f", Indonesian={id_choice}" if id_choice != "none" else " (shared)") + ". Restart Dictately or wait for idle reload."
            except Exception as exc:
                msg = f"Model update failed: {exc}"

            def done():
                self.model_busy = False
                self.apply_model_btn.configure(state="normal")
                self._refresh_model_choices()
                self.feedback.set(msg)

            self.root.after(0, done)

        threading.Thread(target=worker, daemon=True).start()

    def refresh(self):
        self.history_stamp = self.history_version()
        self.rows = read(DATA / "history.json", [])
        self.list.delete(0, "end")
        for item in self.rows:
            date = str(item.get("at", ""))
            try:
                date = datetime.fromtimestamp(int(date)).strftime("%d %b  %H:%M") if date.isdigit() else datetime.fromisoformat(date).strftime("%d %b  %H:%M")
            except (ValueError, OSError):
                pass
            self.list.insert("end", f"  {date}     {item.get('cleaned','')[:68]}")
        if self.rows:
            self.list.selection_set(0)
        self.show_entry()

    def refresh_microphones(self, selected=None):
        if selected is None:
            selected = self.microphone_choices.get(self.microphone.get(), "")
        choices = {"System default": ""}
        try:
            result = subprocess.run([str(ROOT / "Dictately.exe"), "--list-microphones"], capture_output=True, text=True, encoding="utf-8", check=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            for device in json.loads(result.stdout):
                choices[f"{device['name']}  ({device['index']+1})"] = device["id"]
            self.preferences_feedback.set("A disconnected preferred microphone must be reselected before recording")
        except (OSError, ValueError, subprocess.SubprocessError):
            self.preferences_feedback.set("Could not list microphones. Reconnect your device and press Refresh")
        if selected and selected not in choices.values():
            choices["Saved microphone (unavailable)"] = selected
        self.microphone_choices = choices
        self.microphone_box.configure(values=list(choices))
        self.microphone.set(next((name for name, value in choices.items() if value == selected), "System default"))

    def save_preferences(self):
        current = read(DATA / "settings.json", {})
        current.update(microphone=self.microphone_choices[self.microphone.get()], overlay_position=POSITIONS[self.overlay_position.get()])
        write(DATA / "settings.json", current)
        self.preferences_feedback.set("Saved · applies to the next recording or language picker")

    def poll(self):
        if self.history_version() != self.history_stamp:
            self.refresh()
        self.root.after(2000, self.poll)

    def history_version(self):
        try:
            status = (DATA / "history.json").stat()
            return status.st_mtime_ns, status.st_size, status.st_ino
        except OSError:
            return None

    def show_entry(self):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        selection = self.list.curselection()
        if selection:
            item = self.rows[selection[0]]
            self.detail.insert("end", f"CLEANED\n\n{item.get('cleaned','')}\n\nORIGINAL\n\n{item.get('raw','')}")
        else:
            self.detail.insert("end", "Start with your voice.\n\nClick a text field, use your microphone shortcut, and speak.\nYour recent dictations will appear here.")
        self.detail.configure(state="disabled")

    def copy(self, field):
        import pyperclip
        selected = self.list.curselection()
        if selected:
            pyperclip.copy(self.rows[selected[0]].get(field, ""))

    def clear(self):
        if messagebox.askyesno("Clear history", "Delete all saved transcripts from this laptop?", parent=self.root):
            write(DATA / "history.json", [])
            self.refresh()

    def save(self):
        current = read(DATA / "settings.json", {})
        current.update(
            language=LANGUAGES[self.language.get()],
            cleanup=self.cleanup.get(),
            formal_indonesian=self.formal_indonesian.get(),
            accuracy=self.accuracy.get(),
            save_history=self.save_history.get(),
            vocabulary=self.vocabulary.get().strip()[:600],
        )
        write(DATA / "settings.json", current)
        startup(self.startup.get())
        self.feedback.set("Saved. Changes apply to your next dictation.")

    def save_dictionary(self):
        current = read(DATA / "settings.json", {})
        replacements = parse_mapping_lines(self.replacements_text.get("1.0", "end"), "source", "target")
        snippets = parse_mapping_lines(self.snippets_text.get("1.0", "end"), "trigger", "replacement")
        current.update(
            vocabulary=self.vocabulary.get().strip()[:600],
            replacements=replacements,
            snippets=snippets,
        )
        write(DATA / "settings.json", current)
        self.dict_feedback.set(f"Saved {len(replacements)} replacement(s) and {len(snippets)} snippet(s). Applies to your next dictation.")

    def assign_shortcut(self, field):
        def chosen(value):
            if any(value == getattr(self, other) for other in self.shortcut_buttons if other != field):
                self.shortcut_feedback.set("Choose a different shortcut for each control")
                return
            setattr(self, field, value)
            self.shortcut_buttons[field].configure(text=shortcut_name(value))
            self.shortcut_feedback.set("Press Save shortcuts to apply")
        capture_shortcut(self.root, getattr(self, field), chosen)

    def reset_shortcuts(self):
        self.dictation_hotkey = DICTATION_DEFAULT
        self.language_hotkey = LANGUAGE_DEFAULT
        for field, button in self.shortcut_buttons.items():
            button.configure(text=shortcut_name(getattr(self, field)))
        self.shortcut_feedback.set("Defaults restored · press Save shortcuts to apply")

    def save_shortcuts(self):
        values = (self.dictation_hotkey, self.language_hotkey)
        if not all(valid_shortcut(value) for value in values) or len(set(values)) != 2:
            self.shortcut_feedback.set("Choose two different valid shortcuts")
            return
        current = read(DATA / "settings.json", {})
        current.update(dictation_hotkey=self.dictation_hotkey, language_hotkey=self.language_hotkey)
        write(DATA / "settings.json", current)
        self.shortcut_feedback.set("Saved · active when the current recording finishes")


if __name__ == "__main__":
    Window().root.mainloop()
