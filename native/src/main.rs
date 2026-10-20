#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod audio;
mod display;
mod typing;
mod corrections;
mod worker;

use std::{collections::HashSet, fs, io::Write, os::windows::process::CommandExt, path::PathBuf,
    process::Command, sync::{atomic::{AtomicBool, AtomicIsize, Ordering}, mpsc::{Receiver, Sender}}, time::{Duration, Instant, SystemTime, UNIX_EPOCH}};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use windows_sys::Win32::{Foundation::*, System::{LibraryLoader::GetModuleHandleW, Registry::*, Threading::*},
    UI::{Input::KeyboardAndMouse::*, Shell::*, WindowsAndMessaging::*}};
use display::{wide, Display};

const TOGGLE: u32 = WM_APP + 1;
const CANCEL: u32 = WM_APP + 2;
const TRAY: u32 = WM_APP + 3;
const QUIT: u32 = WM_APP + 4;
static HWND_ATOMIC: AtomicIsize = AtomicIsize::new(0);
static RECORDING: AtomicBool = AtomicBool::new(false);
static F12_DOWN: AtomicBool = AtomicBool::new(false);
// Only the Windows message-loop thread accesses APP; other threads use channels.
static mut APP: *mut App = std::ptr::null_mut();

#[derive(Clone, Serialize, Deserialize)]
#[serde(default)]
struct Settings { language: String, cleanup: bool, accuracy: bool, vocabulary: String, max_recording_seconds: u32 }
impl Default for Settings {
    fn default() -> Self { Self { language: "en".into(), cleanup: true, accuracy: true, vocabulary: String::new(), max_recording_seconds: 120 } }
}
impl Settings {
    fn load(data: &PathBuf) -> Self {
        let mut value: Self = fs::read(data.join("settings.json")).ok().and_then(|bytes| serde_json::from_slice(&bytes).ok()).unwrap_or_default();
        if value.language != "en" && value.language != "id" { value.language = "en".into(); }
        value.max_recording_seconds = value.max_recording_seconds.clamp(10, 120);
        value
    }
}

struct App {
    root: PathBuf, data: PathBuf, display: Display, settings: Settings, input: typing::InputWorker,
    editor_locked: bool, input_inflight: bool, finish_attempted: bool,
    recent: String, recent_at: Instant, correction_wait: bool, phrase_start: bool,
    requests: Sender<worker::Work>, responses: Receiver<worker::Event>,
    capture: Option<audio::Capture>, recording: bool, finishing: bool, inflight: bool,
    session: u64, request_id: u64, buffer: Vec<i16>, total_samples: usize,
    last_sent_len: usize, skip_samples: usize, last_request: Instant, started: Instant, last_voice: Instant,
    last_work: Instant, last_insert: Instant, worker_loaded: bool, hide_at: Option<Instant>, timer_interval: u32,
    raw: String, cleaned: String, pending: String, inserted: usize, languages: HashSet<String>, speech_model: String,
    tray: NOTIFYICONDATAW, icon: HICON, record_icon: HICON,
    test_samples: Option<Vec<i16>>, test_offset: usize, test_target: Option<usize>,
}

impl App {
    unsafe fn new(root: PathBuf, data: PathBuf, hwnd: HWND, test_samples: Option<Vec<i16>>) -> Self {
        let (requests, responses) = worker::start(root.clone(), data.clone());
        let icon = LoadImageW(std::ptr::null_mut(), wide(&root.join("assets/transcribr.ico").to_string_lossy()).as_ptr(), IMAGE_ICON, 32, 32, LR_LOADFROMFILE) as HICON;
        let record_icon = LoadImageW(std::ptr::null_mut(), wide(&root.join("assets/transcribr.ico").to_string_lossy()).as_ptr(), IMAGE_ICON, 32, 32, LR_LOADFROMFILE) as HICON;
        let mut tray: NOTIFYICONDATAW = std::mem::zeroed();
        tray.cbSize = std::mem::size_of_val(&tray) as u32; tray.hWnd = hwnd; tray.uID = 1;
        tray.uFlags = NIF_ICON | NIF_TIP | NIF_MESSAGE; tray.hIcon = icon; tray.uCallbackMessage = TRAY;
        let tip = wide("Transcribr · F12 · English only"); tray.szTip[..tip.len()].copy_from_slice(&tip);
        Shell_NotifyIconW(NIM_ADD, &tray);
        let now = Instant::now();
        let mut app = Self { settings: Settings::load(&data), input: typing::InputWorker::new(), display: Display::new(hwnd), root, data,
            editor_locked: false, input_inflight: false, finish_attempted:false, recent: String::new(), recent_at: now, correction_wait: false, phrase_start: true,
            requests, responses, capture: None, recording: false, finishing: false, inflight: false,
            session: 0, request_id: 0, buffer: Vec::new(), total_samples: 0, last_sent_len: 0, skip_samples: 0,
            last_request: now, started: now, last_voice: now, last_work: now, last_insert: now,
            worker_loaded: false, hide_at: None, timer_interval:1000, raw: String::new(), cleaned: String::new(), pending: String::new(),
            inserted: 0, languages: HashSet::new(), speech_model:"loading".into(), tray, icon, record_icon, test_samples, test_offset: 0, test_target: None };
        app.update_tip(); app
    }
    unsafe fn update_tip(&mut self) {
        self.tray.szTip.fill(0);
        let language = if self.settings.language == "id" { "Indonesian only" } else { "English only" };
        let state = if self.recording { "Recording" } else if self.finishing { "Finishing" } else { "F12" };
        let tip = wide(&format!("Transcribr · {language} · {state}"));
        self.tray.szTip[..tip.len()].copy_from_slice(&tip);
        Shell_NotifyIconW(NIM_MODIFY, &self.tray);
    }
    fn log(&self, text: &str) {
        if let Ok(mut file) = fs::OpenOptions::new().create(true).append(true).open(self.data.join("native.log")) {
            let _ = writeln!(file, "{} {text}", unix());
        }
    }
    fn status(&self, state: &str) {
        let value = json!({"pid": std::process::id(), "state": state, "version":"0.5.0", "speech_model":self.speech_model, "app":"Transcribr", "hwnd":self.display.hwnd as usize,
            "at_unix":unix(), "live_commits":self.inserted, "language":self.settings.language, "worker_loaded":self.worker_loaded,
            "engine_ready":self.worker_loaded && !self.inflight, "editor_locked":self.editor_locked, "pending_characters":self.pending.chars().count()});
        let _ = fs::write(self.data.join("status.json"), value.to_string());
    }
    unsafe fn notice(&mut self, title: &str, message: &str, seconds: u64) {
        self.display.recording = false; self.display.preview.clear();
        self.display.cue(if title.contains("microphone") { "Mic error" } else if title.contains("attention") { "Engine error" }
            else if title == "Done" { "Stopped" } else if title == "Dictation stopped" { "Cancelled" } else { "No speech" });
        self.display.show(title, message);
        self.hide_at = Some(Instant::now() + Duration::from_secs(seconds));
        SetTimer(self.display.hwnd, 1, 16, None);
        self.timer_interval = 16;
        if title.contains("microphone") || title.contains("attention") {
            let mut balloon = self.tray;
            balloon.uFlags = NIF_INFO;
            let title: Vec<u16> = title.encode_utf16().take(63).collect();
            let message: Vec<u16> = message.encode_utf16().take(255).collect();
            balloon.szInfoTitle[..title.len()].copy_from_slice(&title);
            balloon.szInfo[..message.len()].copy_from_slice(&message);
            balloon.dwInfoFlags = NIIF_WARNING;
            Shell_NotifyIconW(NIM_MODIFY, &balloon);
        }
    }
    unsafe fn toggle(&mut self, expected: HWND) {
        if self.finishing { return; }
        else if self.recording { self.stop(false); }
        else { self.start(expected); }
    }
    unsafe fn start(&mut self, expected: HWND) {
        // Record the original OS input destination before querying lazy accessibility
        // providers. Resolution may finish later, only while that origin is unchanged.
        self.session += 1;
        let valid=self.input.begin(self.session,expected);
        self.editor_locked=false;self.input_inflight=true;self.finish_attempted=false;self.last_insert=Instant::now();
        self.correction_wait=false; self.phrase_start=true;
        if !valid && self.test_target != Some(0) {
            self.log("No keyboard input destination at F12");
            self.notice("Input needs attention", "Click the message or text box, then press F12 again.", 3);
            return;
        }
        self.settings = Settings::load(&self.data);
        self.request_id = 0; self.buffer.clear(); self.raw.clear(); self.cleaned.clear();
        self.pending.clear(); self.inserted = 0; self.languages.clear(); self.total_samples = 0;
        self.test_offset = 0; self.last_sent_len = 0; self.skip_samples = 0; self.started = Instant::now(); self.last_voice = self.started;
        if self.test_samples.is_none() {
            match audio::Capture::start() {
                Ok(capture) => self.capture = Some(capture),
                Err(error) => { self.notice("Check your microphone", &error, 8); self.status("error"); return; }
            }
        }
        self.recording = true; self.finishing = false; self.hide_at = None;
        RECORDING.store(true, Ordering::Relaxed); self.display.recording = true;
        self.display.wave.iter_mut().for_each(|v| *v = 0.); self.display.preview.clear();
        self.display.language = if self.settings.language == "id" { "ID" } else { "EN" }.into();
        self.display.cue("Listening");
        self.display.show(if self.worker_loaded { "Listening" } else { "Listening · loading speech model" }, "");
        self.tray.hIcon = self.record_icon; Shell_NotifyIconW(NIM_MODIFY, &self.tray);
        self.update_tip();
        SetTimer(self.display.hwnd, 1, 16, None);
        self.timer_interval = 16;
        self.inflight = true;
        let _ = self.requests.send(worker::Work::Request(self.session, json!({"command":"prepare", "id":0, "cleanup":self.settings.cleanup})));
        self.last_work = Instant::now(); self.worker_loaded = true; self.status("recording"); self.log("Recording started");
    }
    fn collect_audio(&mut self) {
        let mut level = 0.;
        let incoming = if let Some(capture) = &mut self.capture { level = capture.level(); capture.take() }
        else if self.recording {
            if let Some(samples) = &self.test_samples {
                let end = ((self.started.elapsed().as_secs_f32() * 16000.) as usize).min(samples.len());
                let result = samples[self.test_offset..end].to_vec(); self.test_offset = end;
                if !result.is_empty() { level = (result.iter().map(|s| (*s as f32 / 32768.).powi(2)).sum::<f32>() / result.len() as f32).sqrt(); }
                result
            } else { Vec::new() }
        } else { Vec::new() };
        if level > 0.0025 { self.last_voice = Instant::now(); }
        self.display.amplitude(level); self.total_samples += incoming.len(); self.buffer.extend(incoming);
    }
    unsafe fn stop(&mut self, cancel: bool) {
        if !self.recording { return; }
        self.collect_audio();
        if let Some(mut capture) = self.capture.take() {
            capture.stop(); let tail = capture.take(); self.total_samples += tail.len(); self.buffer.extend(tail);
        }
        self.recording = false; RECORDING.store(false, Ordering::Relaxed);
        self.tray.hIcon = self.icon; Shell_NotifyIconW(NIM_MODIFY, &self.tray); self.display.recording = false;
        self.display.cue("Mic off");
        if cancel {
            self.session += 1; self.inflight = false; self.buffer.clear(); self.pending.clear(); self.finishing = false;
            self.input.invalidate(self.session);self.input_inflight=false;
            self.save_history(); self.notice("Dictation stopped", "Unfinished audio discarded. Earlier text stays in place.", 4);
            self.hide_at = Some(Instant::now() + Duration::from_millis(100)); self.update_tip();
            self.status("ready"); return;
        }
        self.finishing = true; self.display.show("Finishing the last phrase…", "Still on your laptop. No cloud processing.");
        self.hide_at = Some(Instant::now() + Duration::from_millis(100));
        self.update_tip();
        self.status("processing"); self.dispatch();
    }
    unsafe fn dispatch(&mut self) {
        if self.inflight { return; }
        if self.finishing && self.buffer.len() < 3200 { self.finish(); return; }
        if !self.recording && !self.finishing { return; }
        let final_chunk = self.finishing;
        let paused = self.last_voice.elapsed() > Duration::from_millis(750);
        if !final_chunk && (self.buffer.len() < if paused { 16000 } else { 16000 * 3 } || self.last_request.elapsed() < Duration::from_millis(900)
            || self.buffer.len() < self.last_sent_len + 12000) { return; }
        let len = self.buffer.len();
        let lookahead = if self.last_voice.elapsed() > Duration::from_millis(650) { 0.15 } else { 0.8 };
        let cutoff = len as f32 / 16000. - if final_chunk { 0. } else { lookahead };
        self.request_id += 1;
        let request = json!({"command":"recognize", "id":self.request_id, "samples":self.buffer,
            "final":final_chunk, "flush":paused || self.buffer.len() >= 16000*18, "commit_before":cutoff.max(0.), "language":self.settings.language,
            "at_pause":self.last_voice.elapsed() > Duration::from_millis(650),
            "skip_before":self.skip_samples as f32 / 16000.,
            "vocabulary":self.settings.vocabulary, "cleanup":self.settings.cleanup, "accuracy":self.settings.accuracy,
            "previous_text": self.raw.chars().rev().take(160).collect::<String>().chars().rev().collect::<String>()});
        self.inflight = true; self.last_sent_len = len; self.last_request = Instant::now();
        let _ = self.requests.send(worker::Work::Request(self.session, request));
    }
    unsafe fn receive(&mut self, event: worker::Event) {
        if event.session != self.session { return; }
        self.inflight = false; self.last_work = Instant::now();
        let value = event.value;
        match value["type"].as_str().unwrap_or("error") {
            "ready" => { self.speech_model = value["model"].as_str().unwrap_or("unknown").into(); self.log(&format!("Inference ready: {} {}", value["device"], self.speech_model)); if self.recording { self.display.title = "Listening".into(); } self.status(if self.recording { "recording" } else { "ready" }); }
            "result" => {
                let consumed = value["consumed"].as_u64().unwrap_or(0) as usize;
                let raw = value["raw"].as_str().unwrap_or("");
                let cleaned = value["cleaned"].as_str().unwrap_or(raw);
                if !raw.is_empty() {
                    append_phrase(&mut self.raw, raw); append_phrase(&mut self.cleaned, cleaned);
                    if let Some(language) = value["language"].as_str() {
                        self.languages.insert(language.to_string());
                    }
                    if self.phrase_start {
                        self.correction_wait = self.recent_at.elapsed()<Duration::from_secs(90) && corrections::might_correct(&self.recent, cleaned);
                        self.phrase_start=false;
                    }
                    self.pending.push_str(cleaned.trim()); self.pending.push(' ');
                    self.log(&format!("Phrase recognized: {} samples, {}s inference", consumed, value["seconds"]));
                }
                if value["flushed"].as_bool().unwrap_or(false) {
                    self.correction_wait=false; self.phrase_start=true;
                }
                self.try_insert();
                self.display.preview = value["preview"].as_str().unwrap_or("").to_string();
                let consumed = consumed.min(self.buffer.len()); self.buffer.drain(..consumed);
                self.skip_samples = ((value["through"].as_f64().unwrap_or(0.) * 16000.) as usize).saturating_sub(consumed).min(self.buffer.len());
                self.last_sent_len = self.last_sent_len.saturating_sub(consumed);
                if self.recording { self.display.title = if self.pending.is_empty() { "Listening · typing live" } else { "Click a text field to type" }.into(); }
                if self.finishing && self.buffer.len() < 3200 { self.finish(); return; }
                self.status(if self.recording { "recording" } else { "processing" });
            }
            _ => {
                let error = value["error"].as_str().unwrap_or("Local speech engine stopped"); self.log(error); self.worker_loaded = false;
                if self.recording { self.stop(true); }
                self.finishing = false; self.save_history(); self.notice("Speech engine needs attention", error, 8); self.status("error");
            }
        }
        self.display.redraw(); self.dispatch();
    }
    fn try_insert(&mut self) {
        if self.pending.is_empty() || self.correction_wait || self.input_inflight {return;}
        self.last_insert=Instant::now();
        if let Some(target)=self.test_target {
            if unsafe {GetForegroundWindow() as usize}!=target {return;}
        }
        let correction=if self.recent_at.elapsed()<Duration::from_secs(90) {
            corrections::plan(&self.recent,&self.pending).map(|fix|(self.recent[fix.start..].to_string(),fix.replacement))
        } else {None};
        self.input_inflight=self.input.deliver(self.session,self.pending.clone(),correction);
    }
    fn receive_input(&mut self,event:typing::InputEvent) {
        if !self.input.is_active(self.session) {return;}
        match event {
            typing::InputEvent::Captured{session,locked,same,diagnostic}=> {
                if session!=self.session {return;}
                self.input_inflight=false;self.editor_locked=locked;
                if !same || self.recent_at.elapsed()>Duration::from_secs(90) {self.recent.clear();}
                if !locked {self.log(&format!("Waiting for original editor: {diagnostic}"));}
            }
            typing::InputEvent::Delivered{session,text,success,corrected,locked}=> {
                if session!=self.session {return;}
                self.input_inflight=false;self.editor_locked=locked;
                if self.last_insert.elapsed()>Duration::from_secs(1) {self.log(&format!("Editor operation finished in {:.2}s; success={success}, correction={corrected}",self.last_insert.elapsed().as_secs_f32()));}
                if success && self.pending.starts_with(&text) {
                    if corrected {
                        if let Some(fix)=corrections::plan(&self.recent,&text) {
                            let old=self.recent[fix.start..].trim().to_string();
                            if let Some(prior)=self.cleaned.strip_suffix(text.trim()) {
                                if let Some(prefix)=prior.trim_end().strip_suffix(&old) {self.cleaned=format!("{}{}",prefix,fix.replacement.trim());}
                            }
                            self.recent.truncate(fix.start);self.recent.push_str(&fix.replacement);
                            self.log("Spoken correction applied to verified dictated suffix");
                        }
                    } else {self.recent.push_str(&text);}
                    self.pending.drain(..text.len());self.inserted+=1;self.recent_at=Instant::now();
                    if self.recent.len()>2048 {
                        let mut cut=self.recent.len()-1024;while !self.recent.is_char_boundary(cut) {cut+=1;}
                        self.recent.drain(..cut);
                    }
                }
            }
        }
        self.status(if self.recording {"recording"} else if self.finishing {"processing"} else {"ready"});
    }
    unsafe fn finish(&mut self) {
        self.correction_wait=false;
        if self.input_inflight {
            if self.last_insert.elapsed()<Duration::from_secs(3) {return;}
            self.input.invalidate(self.session+1);self.input_inflight=false;self.finish_attempted=true;
            self.log("Editor timeout; uninserted text retained in history");
        }
        if !self.finish_attempted {
            self.finish_attempted=true;self.try_insert();if self.input_inflight {return;}
        }
        self.finishing=false;self.save_history();self.buffer.clear();self.update_tip();
        if self.hide_at.is_none() {self.display.hide();}self.status("ready");
    }
    fn save_history(&self) {
        if self.raw.is_empty() { return; }
        let path = self.data.join("history.json");
        let mut rows: Vec<Value> = fs::read(&path).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or_default();
        let entry = json!({"raw":self.raw, "cleaned":self.cleaned, "at":format!("{}", unix()),
            "language":if self.languages.len() > 1 {"en + id"} else if self.languages.contains("id") {"id"} else {"en"},
            "cleanup_applied":self.settings.cleanup, "total_seconds":self.started.elapsed().as_secs_f32(),
            "audio_seconds":self.total_samples as f32 / 16000., "live_commits":self.inserted});
        if rows.first().and_then(|r| r["raw"].as_str()) == Some(&self.raw) { return; }
        rows.insert(0, entry); rows.truncate(50);
        let tmp = path.with_extension("tmp");
        if fs::write(&tmp, serde_json::to_vec_pretty(&rows).unwrap()).is_ok() { let _ = fs::rename(tmp, path); }
    }
    unsafe fn tick(&mut self) {
        if self.recording {
            self.collect_audio();
            if self.last_insert.elapsed() >= Duration::from_millis(if self.editor_locked {120} else {500}) { self.try_insert(); }
            self.display.redraw();
            let test_done = self.test_samples.as_ref().is_some_and(|s| self.test_offset >= s.len());
            if self.started.elapsed().as_secs() >= self.settings.max_recording_seconds as u64 || test_done { self.stop(false); }
        }
        while let Ok(event) = self.responses.try_recv() { self.receive(event); }
        while let Some(event)=self.input.poll() {self.receive_input(event);}
        self.dispatch();
        if !self.recording && !self.finishing && !self.inflight && self.worker_loaded && self.last_work.elapsed() >= Duration::from_secs(90) {
            let _ = self.requests.send(worker::Work::Unload); self.worker_loaded = false; self.status("ready");
        }
        if self.hide_at.is_some_and(|at| Instant::now() >= at) { self.display.hide(); self.hide_at = None; }
        self.display.animate();
        let interval = if !self.recording && !self.finishing && !self.display.is_visible() { 1000 }
            else if self.recording && self.started.elapsed().as_millis() > 1200 { 33 } else { 16 };
        if interval != self.timer_interval { SetTimer(self.display.hwnd, 1, interval, None); self.timer_interval = interval; }
    }
    unsafe fn menu(&mut self) {
        if !self.recording && !self.finishing { self.settings = Settings::load(&self.data); }
        let menu = CreatePopupMenu();
        for (id, title) in [(102, "English only"), (103, "Indonesian only"), (100, "History & settings"), (104, "Start with Windows"), (105, "Quit Transcribr")] {
            let checked = (id == 102 && self.settings.language == "en")
                || (id == 103 && self.settings.language == "id") || (id == 104 && startup_command().is_some());
            let disabled = if (id == 102 || id == 103) && (self.recording || self.finishing) { MF_GRAYED } else { 0 };
            AppendMenuW(menu, MF_STRING | disabled | if checked { MF_CHECKED } else { 0 }, id, wide(title).as_ptr());
        }
        let mut point: POINT = std::mem::zeroed(); GetCursorPos(&mut point); SetForegroundWindow(self.display.hwnd);
        let selection = TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, point.x, point.y, 0, self.display.hwnd, std::ptr::null());
        DestroyMenu(menu);
        match selection {
            100 => { self.open_settings(); }
            102..=103 => { self.settings.language = if selection == 103 { "id" } else { "en" }.into();
                let _ = fs::write(self.data.join("settings.json"), serde_json::to_vec_pretty(&self.settings).unwrap());
                self.update_tip(); self.status("ready"); }
            104 => { set_startup(&self.root, startup_command().is_none()); }
            105 => { PostMessageW(self.display.hwnd, QUIT, 0, 0); }
            _ => {}
        }
    }
    fn open_settings(&self) {
        let _ = Command::new(self.root.join(".venv/Scripts/pythonw.exe")).arg(self.root.join("settings_window.py"))
            .current_dir(&self.root).creation_flags(0x08000000).spawn();
    }
}

fn unix() -> u64 { SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs() }
fn append_phrase(target: &mut String, phrase: &str) {
    if !target.is_empty() && !target.ends_with(char::is_whitespace) { target.push(' '); } target.push_str(phrase.trim());
}
fn startup_command() -> Option<String> {
    unsafe {
        let mut key = std::ptr::null_mut();
        if RegOpenKeyExW(HKEY_CURRENT_USER, wide("Software\\Microsoft\\Windows\\CurrentVersion\\Run").as_ptr(), 0, KEY_READ, &mut key) != 0 { return None; }
        let mut bytes = [0u16; 2048]; let mut size = 4096;
        let result = RegQueryValueExW(key, wide("Transcribr").as_ptr(), std::ptr::null(), std::ptr::null_mut(), bytes.as_mut_ptr() as _, &mut size);
        RegCloseKey(key);
        if result != 0 { None } else { Some(String::from_utf16_lossy(&bytes[..size as usize / 2]).trim_end_matches('\0').to_string()) }
    }
}
fn set_startup(root: &PathBuf, enabled: bool) {
    unsafe {
        let mut key = std::ptr::null_mut();
        if RegCreateKeyExW(HKEY_CURRENT_USER, wide("Software\\Microsoft\\Windows\\CurrentVersion\\Run").as_ptr(), 0, std::ptr::null(), 0, KEY_SET_VALUE,
            std::ptr::null(), &mut key, std::ptr::null_mut()) != 0 { return; }
        if enabled {
            let value = wide(&format!("\"{}\"", root.join("Transcribr.exe").display()));
            RegSetValueExW(key, wide("Transcribr").as_ptr(), 0, REG_SZ, value.as_ptr() as _, (value.len() * 2) as u32);
        } else { RegDeleteValueW(key, wide("Transcribr").as_ptr()); }
        RegDeleteValueW(key, wide("LocalFlow").as_ptr());
        RegCloseKey(key);
    }
}

unsafe extern "system" fn keyboard(code: i32, wp: WPARAM, lp: LPARAM) -> LRESULT {
    if code >= 0 {
        let key = &*(lp as *const KBDLLHOOKSTRUCT);
        if key.flags & LLKHF_INJECTED == 0 || key.dwExtraInfo != 0x4C464C4F {
            if key.vkCode != VK_F12 as u32 && (wp as u32 == WM_KEYDOWN || wp as u32 == WM_SYSKEYDOWN) {typing::user_activity();}
            if key.vkCode == VK_F12 as u32 {
                if wp as u32 == WM_KEYDOWN || wp as u32 == WM_SYSKEYDOWN { F12_DOWN.store(true, Ordering::Relaxed); }
                else if (wp as u32 == WM_KEYUP || wp as u32 == WM_SYSKEYUP) && F12_DOWN.swap(false, Ordering::Relaxed) {
                    PostMessageW(HWND_ATOMIC.load(Ordering::Relaxed) as HWND, TOGGLE, GetForegroundWindow() as WPARAM, 0);
                } return 1;
            }
            if key.vkCode == VK_ESCAPE as u32 && wp as u32 == WM_KEYDOWN && RECORDING.load(Ordering::Relaxed) {
                PostMessageW(HWND_ATOMIC.load(Ordering::Relaxed) as HWND, CANCEL, 0, 0); return 1;
            }
        }
    }
    CallNextHookEx(std::ptr::null_mut(), code, wp, lp)
}
unsafe extern "system" fn mouse(code:i32, wp:WPARAM, lp:LPARAM) -> LRESULT {
    if code>=0 && matches!(wp as u32,WM_LBUTTONDOWN|WM_RBUTTONDOWN|WM_MBUTTONDOWN|WM_XBUTTONDOWN|WM_MOUSEWHEEL|WM_MOUSEHWHEEL) {typing::user_activity();}
    CallNextHookEx(std::ptr::null_mut(),code,wp,lp)
}
unsafe extern "system" fn window_proc(hwnd: HWND, message: u32, wp: WPARAM, lp: LPARAM) -> LRESULT {
    if message == WM_MOUSEACTIVATE { return MA_NOACTIVATE as LRESULT; }
    if let Some(app) = APP.as_mut() {
        match message {
            WM_PAINT => { app.display.paint(); return 0; }
            WM_ERASEBKGND => return 1,
            WM_TIMER => { app.tick(); return 0; }
            TOGGLE => { app.toggle(wp as HWND); return 0; }
            CANCEL => { app.stop(true); return 0; }
            TRAY => { if lp as u32 == WM_RBUTTONUP || lp as u32 == WM_LBUTTONUP { app.menu(); } return 0; }
            QUIT | WM_CLOSE => {
                app.input.invalidate(app.session+1);
                if app.recording { app.stop(true); } let _ = app.requests.send(worker::Work::Unload);
                Shell_NotifyIconW(NIM_DELETE, &app.tray); app.status("stopped"); DestroyWindow(hwnd); return 0;
            }
            WM_DESTROY => { PostQuitMessage(0); return 0; }
            _ => {}
        }
    } DefWindowProcW(hwnd, message, wp, lp)
}

fn main() {
    unsafe {
        windows_sys::Win32::UI::HiDpi::SetProcessDpiAwarenessContext(windows_sys::Win32::UI::HiDpi::DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        let args: Vec<String> = std::env::args().collect();
        if args.iter().any(|arg| arg == "--check-input") {
            let typer = typing::Typer::new();
            let result = json!({"captured":typer.capture(std::ptr::null_mut()).is_some(), "diagnostic":typer.diagnostic()});
            if let Some(path)=std::env::var_os("TRANSCRIBER_INPUT_DIAGNOSTIC") {let _=fs::write(path, result.to_string());}
            return;
        }
        let exe = std::env::current_exe().unwrap();
        let root = exe.ancestors().find(|p| p.join("live_worker.py").is_file()).unwrap().to_path_buf();
        let data = std::env::var_os("LOCALFLOW_DATA").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap()).join("Transcribr"));
        let migrate = std::env::var_os("LOCALFLOW_DATA").is_none();
        fs::create_dir_all(&data).ok();
        if migrate {
            for previous in ["Transcriber", "LocalFlow"] {
                let old = PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap()).join(previous);
                for name in ["settings.json", "history.json"] { if old.join(name).is_file() && !data.join(name).exists() { let _ = fs::copy(old.join(name), data.join(name)); } }
            }
        }
        let settings = Settings::load(&data);
        let _ = fs::write(data.join("settings.json"), serde_json::to_vec_pretty(&settings).unwrap());
        if args.iter().any(|a| a == "--enable-startup") { set_startup(&root, true); return; }
        if args.iter().any(|a| a == "--check-mic") {
            let result = match audio::Capture::start() {
                Ok(mut capture) => {
                    // Simulate a blocked editor/UI; capture must keep recycling.
                    std::thread::sleep(Duration::from_millis(1500));
                    let ui_stall_samples = capture.take().len();
                    let mut samples = ui_stall_samples; let mut peak = 0.0f32;
                    for _ in 0..20 {
                        std::thread::sleep(Duration::from_millis(100));
                        peak = peak.max(capture.level()); samples += capture.take().len();
                    }
                    capture.stop(); samples += capture.take().len();
                    json!({"ok":samples > 32000 && ui_stall_samples > 20000, "samples":samples, "ui_stall_samples":ui_stall_samples, "peak_rms":peak, "audio_saved":false})
                },
                Err(error) => json!({"ok":false,"error":error}),
            };
            let _ = fs::write(data.join("microphone-check.json"), result.to_string()); return;
        }
        let mutex = CreateMutexW(std::ptr::null(), 0, wide("Local\\LocalFlow.Dictation.v1").as_ptr());
        if GetLastError() == ERROR_ALREADY_EXISTS { CloseHandle(mutex); return; }
        let test_samples = args.iter().position(|a| a == "--test-pcm").and_then(|i| args.get(i+1)).map(|path| {
            fs::read(path).unwrap().chunks_exact(2).map(|b| i16::from_le_bytes([b[0],b[1]])).collect::<Vec<_>>() });
        let instance = GetModuleHandleW(std::ptr::null()); let cls_name = wide("TranscribrNativeOverlay");
        let class = WNDCLASSW { lpfnWndProc: Some(window_proc), hInstance: instance, lpszClassName: cls_name.as_ptr(), ..std::mem::zeroed() };
        RegisterClassW(&class);
        let hwnd = CreateWindowExW(WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_LAYERED, cls_name.as_ptr(), wide("Transcribr").as_ptr(),
            WS_POPUP, 0, 0, display::WIDTH, display::HEIGHT, std::ptr::null_mut(), std::ptr::null_mut(), instance, std::ptr::null());
        if hwnd.is_null() { CloseHandle(mutex); return; } HWND_ATOMIC.store(hwnd as isize, Ordering::Relaxed);
        let mut app = Box::new(App::new(root, data, hwnd, test_samples));
        app.test_target = args.iter().position(|a| a == "--test-target").and_then(|i| args.get(i+1)).and_then(|s| s.parse().ok());
        APP = &mut *app;
        let hook = SetWindowsHookExW(WH_KEYBOARD_LL, Some(keyboard), instance, 0);
        if hook.is_null() { app.log("F12 hook failed"); app.status("error"); return; }
        let mouse_hook=SetWindowsHookExW(WH_MOUSE_LL,Some(mouse),instance,0);
        if mouse_hook.is_null() {UnhookWindowsHookEx(hook);app.log("Input destination tracking failed");app.status("error");return;}
        // Warm the local models without opening the microphone or showing a popup.
        app.worker_loaded = true; app.inflight = true;
        let _ = app.requests.send(worker::Work::Request(0, json!({"command":"prepare", "id":0, "cleanup":app.settings.cleanup})));
        app.status("ready"); app.log("Native Rust controller ready"); SetTimer(hwnd, 1, 1000, None);
        let mut message: MSG = std::mem::zeroed();
        while GetMessageW(&mut message, std::ptr::null_mut(), 0, 0) > 0 { TranslateMessage(&message); DispatchMessageW(&message); }
        UnhookWindowsHookEx(mouse_hook); UnhookWindowsHookEx(hook); CloseHandle(mutex);
        if !app.icon.is_null() { DestroyIcon(app.icon); } if !app.record_icon.is_null() { DestroyIcon(app.record_icon); }
        APP = std::ptr::null_mut();
    }
}
#[cfg(test)] mod tests {
    use super::*;
    #[test] fn phrase_spacing() { let mut s = "Hello".to_string(); append_phrase(&mut s, " world. "); assert_eq!(s, "Hello world."); }
    #[test] fn defaults_locked_english() { assert_eq!(Settings::default().language, "en"); }
}
