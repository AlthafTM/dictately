"""On-demand settings/history UI; the always-running controller is native Rust."""
from datetime import datetime
import ctypes
import json
import os
import sys
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
import winreg

from runtime_paths import app_root
from hotkeys import capture_shortcut, shortcut_name, valid_shortcut, DICTATION_DEFAULT, LANGUAGE_DEFAULT
ROOT = app_root()
DATA = Path(os.environ.get("LOCALFLOW_DATA", str(Path(os.environ["LOCALAPPDATA"]) / "Transcribr")))
BG, CARD, FIELD, TEXT, MUTED, ACCENT = "#111111", "#1d1d1d", "#2a2a2a", "#f1f1f1", "#aaaaaa", "#e5e5e5"
LANGUAGES = {"English only": "en", "Indonesian only": "id", "English + Indonesian": "both"}

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
        shortcuts = ttk.Frame(tabs, padding=(0,22))
        tabs.add(history, text="Recent dictations")
        tabs.add(settings, text="Settings")
        tabs.add(shortcuts,text="Keyboard shortcuts")
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
        ttk.Label(settings, text="Choose a fixed language or both. The language shortcut opens a voice/mouse picker.", style="Muted.TLabel").pack(anchor="w", pady=(5, 12))
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
        self.feedback = tk.StringVar(value="Choose Keyboard shortcuts to change the microphone and language keys")
        ttk.Label(settings, textvariable=self.feedback, style="Muted.TLabel").pack(anchor="w", pady=(22, 12))
        ttk.Button(settings, text="Save settings", style="Accent.TButton", command=self.save).pack(anchor="e")
        self.dictation_hotkey=current.get('dictation_hotkey',DICTATION_DEFAULT)
        self.language_hotkey=current.get('language_hotkey',LANGUAGE_DEFAULT)
        if not valid_shortcut(self.dictation_hotkey):self.dictation_hotkey=DICTATION_DEFAULT
        if not valid_shortcut(self.language_hotkey) or self.language_hotkey==self.dictation_hotkey:
            self.dictation_hotkey=DICTATION_DEFAULT;self.language_hotkey=LANGUAGE_DEFAULT
        ttk.Label(shortcuts,text="Make Transcribr fit your keyboard",font=("Segoe UI",15,"bold")).pack(anchor="w",pady=(0,10))
        ttk.Label(shortcuts,text="Click a shortcut, then press a key or combination. These shortcuts work globally.",style="Muted.TLabel").pack(anchor="w",pady=(0,25))
        self.shortcut_buttons={}
        for field,label in [('dictation_hotkey','Open / close microphone'),('language_hotkey','Choose language')]:
            row=ttk.Frame(shortcuts);row.pack(fill='x',pady=8)
            ttk.Label(row,text=label).pack(side='left')
            button=ttk.Button(row,text=shortcut_name(getattr(self,field)),command=lambda name=field:self.assign_shortcut(name))
            button.pack(side='right');self.shortcut_buttons[field]=button
        self.shortcut_feedback=tk.StringVar(value='Defaults: F12 for dictation · F11 for language')
        ttk.Label(shortcuts,textvariable=self.shortcut_feedback,style='Muted.TLabel').pack(anchor='w',pady=(20,10))
        actions=ttk.Frame(shortcuts);actions.pack(fill='x',pady=12)
        ttk.Button(actions,text='Restore F12 / F11',command=self.reset_shortcuts).pack(side='left')
        ttk.Button(actions,text='Save shortcuts',style='Accent.TButton',command=self.save_shortcuts).pack(side='right')
        if '--shortcuts' in sys.argv:tabs.select(shortcuts)
        bottom=ttk.Frame(outer);bottom.pack(fill='x',pady=(14,0))
        ttk.Button(bottom,text='Keyboard shortcuts',command=lambda:tabs.select(shortcuts)).pack(side='left')
        ttk.Label(bottom, text="Local phrase dictation", style="Muted.TLabel").pack(side='right')
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
        current.update(language=LANGUAGES[self.language.get()], cleanup=self.cleanup.get(), accuracy=self.accuracy.get(), vocabulary=self.vocabulary.get().strip()[:600])
        write(DATA / "settings.json", current)
        startup(self.startup.get())
        self.feedback.set("Saved. Changes apply to your next dictation.")

    def assign_shortcut(self,field):
        def chosen(value):
            other='language_hotkey' if field=='dictation_hotkey' else 'dictation_hotkey'
            if value==getattr(self,other):
                self.shortcut_feedback.set('Choose different shortcuts for microphone and language');return
            setattr(self,field,value);self.shortcut_buttons[field].configure(text=shortcut_name(value))
            self.shortcut_feedback.set('Press Save shortcuts to apply')
        capture_shortcut(self.root,getattr(self,field),chosen)

    def reset_shortcuts(self):
        self.dictation_hotkey=DICTATION_DEFAULT;self.language_hotkey=LANGUAGE_DEFAULT
        for field,button in self.shortcut_buttons.items():button.configure(text=shortcut_name(getattr(self,field)))
        self.shortcut_feedback.set('Defaults restored · press Save shortcuts to apply')

    def save_shortcuts(self):
        if not all(valid_shortcut(value) for value in (self.dictation_hotkey,self.language_hotkey)) or self.dictation_hotkey==self.language_hotkey:
            self.shortcut_feedback.set('Choose two different valid shortcuts');return
        current=read(DATA/'settings.json',{})
        current.update(dictation_hotkey=self.dictation_hotkey,language_hotkey=self.language_hotkey)
        write(DATA/'settings.json',current)
        self.shortcut_feedback.set('Saved · active when the current recording finishes')

if __name__ == "__main__":
    Window().root.mainloop()
