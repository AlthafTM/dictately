"""On-demand settings/history UI; the always-running controller is native Rust."""
from datetime import datetime
import ctypes
import json
import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
import winreg

from runtime_paths import app_root
ROOT = app_root()
DATA = Path(os.environ.get("LOCALFLOW_DATA", str(Path(os.environ["LOCALAPPDATA"]) / "Transcribr")))
BG, CARD, FIELD, TEXT, MUTED, ACCENT = "#111111", "#1d1d1d", "#2a2a2a", "#f1f1f1", "#aaaaaa", "#e5e5e5"
LANGUAGES = {"English only": "en", "Indonesian only": "id"}

def read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default

def write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)

def startup(enabled=None):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if enabled is None:
            try:
                return bool(winreg.QueryValueEx(key, "Transcribr")[0])
            except FileNotFoundError:
                return False
        if enabled:
            winreg.SetValueEx(key, "Transcribr", 0, winreg.REG_SZ, f'"{ROOT / "Transcribr.exe"}"')
        else:
            try:
                winreg.DeleteValue(key, "Transcribr")
            except FileNotFoundError:
                pass

class Window:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Transcribr — History & settings")
        self.root.iconbitmap(str(ROOT / "assets/transcribr.ico"))
        self.root.configure(bg=BG)
        width, height = 840, 670
        self.root.geometry(f"{width}x{height}+{max(0,(self.root.winfo_screenwidth()-width)//2)}+{max(0,(self.root.winfo_screenheight()-height)//2)}")
        self.root.minsize(720, 580)
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
        style.configure("TCheckbutton", background=BG, foreground=TEXT, font=("Segoe UI", 11), padding=(0, 6))
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(20, 10), font=("Segoe UI", 11))
        style.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", TEXT)])
        style.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=TEXT, padding=8, arrowsize=16)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)], selectbackground=[("readonly", FIELD)])
        style.configure("TEntry", fieldbackground=FIELD, foreground=TEXT, padding=10, borderwidth=0)
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        outer = ttk.Frame(self.root, padding=28)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="Transcribr", font=("Segoe UI", 26, "bold")).pack(side="left")
        ttk.Label(header, text="●  ON DEVICE", foreground=ACCENT, font=("Segoe UI", 10, "bold")).pack(side="right")
        ttk.Label(outer, text="Your voice, right where you type.", style="Muted.TLabel").pack(anchor="w", pady=(4, 20))
        tabs = ttk.Notebook(outer)
        tabs.pack(fill="both", expand=True)
        history = ttk.Frame(tabs, padding=(0, 18))
        settings = ttk.Frame(tabs, padding=(0, 22))
        tabs.add(history, text="Recent dictations")
        tabs.add(settings, text="Settings")
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
        language_code = current.get("language", "en")
        self.language = tk.StringVar(value=next((name for name, code in LANGUAGES.items() if code == language_code), next(iter(LANGUAGES))))
        ttk.Label(settings, text="Recognition language", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(settings, text="Choose one language. You can also change it from the taskbar tray icon.", style="Muted.TLabel").pack(anchor="w", pady=(5, 12))
        ttk.Combobox(settings, values=list(LANGUAGES), textvariable=self.language, state="readonly", font=("Segoe UI", 11)).pack(fill="x")
        self.cleanup = tk.BooleanVar(value=current.get("cleanup", True))
        self.accuracy = tk.BooleanVar(value=current.get("accuracy", True))
        self.startup = tk.BooleanVar(value=startup())
        ttk.Checkbutton(settings, text="Remove hesitation sounds and repeated pronouns", variable=self.cleanup).pack(anchor="w", pady=(16, 0))
        ttk.Checkbutton(settings, text="Higher recognition accuracy", variable=self.accuracy).pack(anchor="w")
        ttk.Checkbutton(settings, text="Start quietly when I sign in to Windows", variable=self.startup).pack(anchor="w")
        ttk.Label(settings, text="Personal vocabulary", font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(20, 5))
        ttk.Label(settings, text="Names and technical terms, separated by commas.", style="Muted.TLabel").pack(anchor="w", pady=(0, 10))
        self.vocabulary = tk.StringVar(value=current.get("vocabulary", ""))
        ttk.Entry(settings, textvariable=self.vocabulary, font=("Segoe UI", 11)).pack(fill="x")
        self.feedback = tk.StringVar(value="F12 starts and stops · Esc discards unfinished audio")
        ttk.Label(settings, textvariable=self.feedback, style="Muted.TLabel").pack(anchor="w", pady=(22, 12))
        ttk.Button(settings, text="Save settings", style="Accent.TButton", command=self.save).pack(anchor="e")
        ttk.Label(outer, text="Text appears in short live phrases. Models release memory after inactivity.", style="Muted.TLabel").pack(anchor="w", pady=(14, 0))
        self.rows = []
        self.refresh()
        self.root.after(2000, self.poll)

    def refresh(self):
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

    def poll(self):
        if read(DATA / "history.json", []) != self.rows:
            self.refresh()
        self.root.after(2000, self.poll)

    def show_entry(self):
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        selection = self.list.curselection()
        if selection:
            item = self.rows[selection[0]]
            self.detail.insert("end", f"CLEANED\n\n{item.get('cleaned','')}\n\nORIGINAL\n\n{item.get('raw','')}")
        else:
            self.detail.insert("end", "Start with your voice.\n\nClick a text field, press F12, and speak English or Indonesian.\nYour recent dictations will appear here.")
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
        current.update(language=LANGUAGES[self.language.get()], cleanup=self.cleanup.get(), accuracy=self.accuracy.get(), vocabulary=self.vocabulary.get().strip()[:600])
        write(DATA / "settings.json", current)
        startup(self.startup.get())
        self.feedback.set("Saved. Changes apply to your next dictation.")

if __name__ == "__main__":
    Window().root.mainloop()
