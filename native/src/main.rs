#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod audio;
mod display;
mod typing;
mod corrections;
mod worker;
mod hotkeys;
mod frame_clock;

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
const LANGUAGE: u32 = WM_APP + 5;
static HWND_ATOMIC: AtomicIsize = AtomicIsize::new(0);
static RECORDING: AtomicBool = AtomicBool::new(false);
static F12_DOWN: AtomicBool = AtomicBool::new(false);
static LANGUAGE_DOWN: AtomicBool = AtomicBool::new(false);
// Only the Windows message-loop thread accesses APP; other threads use channels.
static mut APP: *mut App = std::ptr::null_mut();

#[derive(Clone, Serialize, Deserialize)]
#[serde(default)]
struct Settings { language: String, cleanup: bool, accuracy: bool, vocabulary: String, max_recording_seconds: u32, dictation_hotkey:u32, language_hotkey:u32, overlay_position:String, microphone:String }
impl Default for Settings {
    fn default() -> Self { Self { language: "en".into(), cleanup: true, accuracy: true, vocabulary: String::new(), max_recording_seconds: 120, dictation_hotkey:123,language_hotkey:121,overlay_position:"bottom_center".into(),microphone:String::new() } }
}
impl Settings {
    fn load(data: &PathBuf) -> Self {
        let mut value: Self = fs::read(data.join("settings.json")).ok().and_then(|bytes| serde_json::from_slice(&bytes).ok()).unwrap_or_default();
        if !matches!(value.language.as_str(),"en"|"id"|"both") { value.language = "en".into(); }
        if !hotkeys::pair(value.dictation_hotkey,value.language_hotkey) {value.dictation_hotkey=123;value.language_hotkey=121;}
        value.max_recording_seconds = value.max_recording_seconds.clamp(10, 120);
        if !display::valid_position(&value.overlay_position) {value.overlay_position="bottom_center".into();}
        value
    }
}

struct App {
    root: PathBuf, data: PathBuf, display: Display, settings: Settings, input: typing::InputWorker,
    editor_locked: bool, input_inflight: bool, finish_attempted: bool,
    recent: String, recent_at: Instant, correction_wait: bool, phrase_start: bool,
    requests: Sender<worker::Work>, responses: Receiver<worker::Event>,
    capture: Option<std::sync::Arc<audio::SharedAudio>>, microphone: audio::Microphone,
    mic_starting: bool, mic_stopping: bool, recording: bool, finishing: bool, inflight: bool,
    session: u64, request_id: u64, buffer: Vec<i16>, total_samples: usize,
    last_sent_len: usize, skip_samples: usize, last_request: Instant, started: Instant, last_voice: Instant,
    last_work: Instant, last_insert: Instant, worker_loaded: bool, hide_at: Option<Instant>, timer_interval: u32,
    raw: String, cleaned: String, pending: String, inserted: usize, languages: HashSet<String>, speech_model: String,
    tray: NOTIFYICONDATAW, icon: HICON, record_icon: HICON,
    test_samples: Option<Vec<i16>>, test_offset: usize, test_target: Option<usize>,
    picker:bool, picker_pending:bool, picker_voice:Instant, picker_has_voice:bool, settings_checked:Instant,
    frame_clock:bool,
}

impl App {
    fn apply_hotkeys(&self) {
        hotkeys::DICTATION.store(self.settings.dictation_hotkey,Ordering::Relaxed);
        hotkeys::LANGUAGE.store(self.settings.language_hotkey,Ordering::Relaxed);
    }
    unsafe fn request_picker(&mut self) {
        if self.picker {self.close_picker();return;}
        if self.recording || self.finishing || self.mic_stopping || self.display.is_visible() {
            self.picker_pending=!self.picker_pending;
            if self.recording {self.stop(false);}
            return;
        }
        self.open_picker();
    }
    unsafe fn open_picker(&mut self) {
        self.picker_pending=false;self.picker=true;self.session+=1;
        self.input.invalidate(self.session);self.input_inflight=false;
        self.settings=Settings::load(&self.data);self.apply_hotkeys();
        self.pending.clear();self.raw.clear();self.cleaned.clear();self.buffer.clear();self.total_samples=0;
        self.test_offset=0;self.last_sent_len=0;self.request_id=0;self.started=Instant::now();self.last_voice=self.started;self.picker_voice=self.started;
        self.picker_has_voice=false;
        self.recording=true;self.finishing=false;self.hide_at=None;self.mic_stopping=false;
        RECORDING.store(true,Ordering::Relaxed);
        self.mic_starting=self.test_samples.is_none();self.capture=None;
        if self.mic_starting {self.microphone.start(self.session,Duration::ZERO,&self.settings.microphone);}
        self.display.position=self.settings.overlay_position.clone();
        self.display.set_picker(true);
        self.display.wave.iter_mut().for_each(|value|*value=0.);
        self.display.selected=match self.settings.language.as_str() {"id"=>1,"both"=>2,_=>0};
        self.display.recording=true;self.display.cue("");self.display.show("","");
        // Mouse selection needs no model. A voice command starts inference only
        // after actual microphone activity, with the same asynchronous worker.
        self.inflight=false;
        SetTimer(self.display.hwnd,1,16,None);self.timer_interval=16;self.update_tip();self.status("language_picker");
    }
    unsafe fn close_picker(&mut self) {
        self.close_picker_delayed(false);
    }
    unsafe fn close_picker_delayed(&mut self,selected:bool) {
        if !self.picker {return;}
        if self.test_samples.is_none() {self.microphone.stop(self.session);}
        self.session+=1;self.input.invalidate(self.session);
        self.capture=None;self.recording=false;self.picker=false;self.picker_pending=false;
        self.mic_starting=false;self.mic_stopping=false;self.finishing=false;self.inflight=false;
        self.buffer.clear();self.pending.clear();RECORDING.store(false,Ordering::Relaxed);
        self.display.recording=false;self.display.cue("");
        if selected {self.hide_at=Some(Instant::now()+Duration::from_millis(180));} else {self.display.hide();self.hide_at=None;}
        self.update_tip();self.status("ready");
    }
    unsafe fn choose_language(&mut self,language:&str) {
        if !matches!(language,"en"|"id"|"both") {return;}
        self.settings=Settings::load(&self.data);self.settings.language=language.into();
        let path=self.data.join("settings.json");let temporary=path.with_extension("tmp");
        if fs::write(&temporary,serde_json::to_vec_pretty(&self.settings).unwrap()).is_err() || fs::rename(&temporary,&path).is_err() {
            self.log("Could not save language selection");return;
        }
        self.display.selected=match language {"id"=>1,"both"=>2,_=>0};
        if self.picker {self.close_picker_delayed(true);} else {self.update_tip();self.status("ready");}
    }
    unsafe fn new(root: PathBuf, data: PathBuf, hwnd: HWND, test_samples: Option<Vec<i16>>) -> Self {
        let (requests, responses) = worker::start(root.clone(), data.clone());
        let icon = LoadImageW(std::ptr::null_mut(), wide(&root.join("assets/dictately.ico").to_string_lossy()).as_ptr(), IMAGE_ICON, 32, 32, LR_LOADFROMFILE) as HICON;
        let record_icon = LoadImageW(std::ptr::null_mut(), wide(&root.join("assets/dictately.ico").to_string_lossy()).as_ptr(), IMAGE_ICON, 32, 32, LR_LOADFROMFILE) as HICON;
        let mut tray: NOTIFYICONDATAW = std::mem::zeroed();
        tray.cbSize = std::mem::size_of_val(&tray) as u32; tray.hWnd = hwnd; tray.uID = 1;
        tray.uFlags = NIF_ICON | NIF_TIP | NIF_MESSAGE; tray.hIcon = icon; tray.uCallbackMessage = TRAY;
        let tip = wide("Dictately · F12 · English only"); tray.szTip[..tip.len()].copy_from_slice(&tip);
        Shell_NotifyIconW(NIM_ADD, &tray);
        let now = Instant::now();
        let mut app = Self { settings: Settings::load(&data), input: typing::InputWorker::new(), display: Display::new(hwnd), root, data,
            editor_locked: false, input_inflight: false, finish_attempted:false, recent: String::new(), recent_at: now, correction_wait: false, phrase_start: true,
            requests, responses, capture: None, microphone: audio::Microphone::new(), mic_starting:false, mic_stopping:false,
            recording: false, finishing: false, inflight: false,
            session: 0, request_id: 0, buffer: Vec::new(), total_samples: 0, last_sent_len: 0, skip_samples: 0,
            last_request: now, started: now, last_voice: now, last_work: now, last_insert: now,
            worker_loaded: false, hide_at: None, timer_interval:1000, raw: String::new(), cleaned: String::new(), pending: String::new(),
            inserted: 0, languages: HashSet::new(), speech_model:"loading".into(), tray, icon, record_icon, test_samples, test_offset: 0, test_target: None,
            picker:false,picker_pending:false,picker_voice:now,picker_has_voice:false,settings_checked:now,frame_clock:false };
        app.apply_hotkeys();
        app.display.position=app.settings.overlay_position.clone();
        app.display.prewarm_picker();
        app.update_tip(); app
    }
    unsafe fn update_tip(&mut self) {
        self.tray.szTip.fill(0);
        let language = match self.settings.language.as_str() {"id"=>"Indonesian","both"=>"English + Indonesian",_=>"English"};
        let state = if self.picker {"Language picker".to_string()} else if self.recording {"Recording".into()} else if self.finishing {"Finishing".into()} else {hotkeys::name(self.settings.dictation_hotkey)};
        let tip = wide(&format!("Dictately · {language} · {state}"));
        self.tray.szTip[..tip.len()].copy_from_slice(&tip);
        Shell_NotifyIconW(NIM_MODIFY, &self.tray);
    }
    fn log(&self, text: &str) {
        if let Ok(mut file) = fs::OpenOptions::new().create(true).append(true).open(self.data.join("native.log")) {
            let _ = writeln!(file, "{} {text}", unix());
        }
    }
    fn status(&self, state: &str) {
        let value = json!({"pid": std::process::id(), "state": state, "version":env!("CARGO_PKG_VERSION"), "speech_model":self.speech_model, "app":"Dictately", "hwnd":self.display.hwnd as usize,
            "at_unix":unix(), "live_commits":self.inserted, "language":self.settings.language, "worker_loaded":self.worker_loaded,
            "language_picker":self.picker,"dictation_hotkey":self.settings.dictation_hotkey,"language_hotkey":self.settings.language_hotkey,
            "overlay_position":self.settings.overlay_position,"preferred_microphone":self.settings.microphone,
            "engine_ready":self.worker_loaded && !self.inflight, "editor_locked":self.editor_locked, "pending_characters":self.pending.chars().count(),
            "microphone_ready":self.capture.is_some() || self.test_samples.is_some(), "microphone_starting":self.mic_starting,
            "microphone_stopping":self.mic_stopping, "audio_samples":self.total_samples});
        let _ = fs::write(self.data.join("status.json"), value.to_string());
    }
    unsafe fn notice(&mut self, title: &str, message: &str, seconds: u64) {
        if !self.picker {self.display.set_picker(false);}
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
        if self.picker {self.close_picker();return;}
        if self.finishing { return; }
        else if self.recording { self.stop(false); }
        else { self.start(expected); }
    }
    unsafe fn start(&mut self, expected: HWND) {
        self.display.set_picker(false);
        // Record the original OS input destination before querying lazy accessibility
        // providers. Resolution may finish later, only while that origin is unchanged.
        self.session += 1;
        let valid=self.input.begin(self.session,expected);
        self.editor_locked=false;self.input_inflight=true;self.finish_attempted=false;self.last_insert=Instant::now();
        self.correction_wait=false; self.phrase_start=true;
        if !valid && self.test_target != Some(0) {
            self.log("No keyboard input destination at F12");
            self.notice("Input needs attention", &format!("Click a text box, then press {} again.",hotkeys::name(self.settings.dictation_hotkey)), 3);
            return;
        }
        self.settings = Settings::load(&self.data);
        self.display.position=self.settings.overlay_position.clone();
        self.request_id = 0; self.buffer.clear(); self.raw.clear(); self.cleaned.clear();
        self.pending.clear(); self.inserted = 0; self.languages.clear(); self.total_samples = 0;
        self.test_offset = 0; self.last_sent_len = 0; self.skip_samples = 0; self.started = Instant::now(); self.last_voice = self.started;
        self.capture = None; self.mic_starting = self.test_samples.is_none(); self.mic_stopping = false;
        if self.mic_starting {
            // Delay injection is restricted to the no-insertion QA mode.
            let delay = if self.test_target == Some(0) { std::env::var("DICTATELY_TEST_MIC_DELAY_MS").ok()
                .and_then(|value|value.parse::<u64>().ok()).unwrap_or(0).min(2000) } else {0};
            self.microphone.start(self.session, Duration::from_millis(delay),&self.settings.microphone);
        }
        self.recording = true; self.finishing = false; self.hide_at = None;
        RECORDING.store(true, Ordering::Relaxed); self.display.recording = true;
        self.display.wave.iter_mut().for_each(|v| *v = 0.); self.display.preview.clear();
        self.display.language = match self.settings.language.as_str() {"id"=>"ID","both"=>"EN + ID",_=>"EN"}.into();
        self.display.cue("Listening");
        self.display.show(if self.worker_loaded { "Listening" } else { "Listening · loading speech model" }, "");
        self.tray.hIcon = self.record_icon; Shell_NotifyIconW(NIM_MODIFY, &self.tray);
        self.update_tip();
        SetTimer(self.display.hwnd, 1, 16, None);
        self.timer_interval = 16;
        self.inflight = true;
        let _ = self.requests.send(worker::Work::Request(self.session, json!({"command":"prepare", "id":0, "cleanup":self.settings.cleanup}), None));
        self.last_work = Instant::now(); self.worker_loaded = true; self.status("recording"); self.log("Recording started");
    }
    fn collect_audio(&mut self) {
        let mut level = 0.;
        let incoming = if let Some(capture) = &mut self.capture { level = capture.level(); capture.take_into(&mut self.buffer) }
        else if self.recording {
            if let Some(samples) = &self.test_samples {
                let end = ((self.started.elapsed().as_secs_f32() * 16000.) as usize).min(samples.len());
                let result = &samples[self.test_offset..end]; self.test_offset = end;
                if !result.is_empty() { level = (result.iter().map(|s| (*s as f32 / 32768.).powi(2)).sum::<f32>() / result.len() as f32).sqrt(); }
                self.buffer.extend_from_slice(result); result.len()
            } else { 0 }
        } else { 0 };
        if level > 0.0025 { self.last_voice = Instant::now();if self.picker {self.picker_has_voice=true;} }
        self.display.amplitude(level); self.total_samples += incoming;
    }
    unsafe fn receive_audio(&mut self) {
        while let Some(event) = self.microphone.poll() {
            match event {
                audio::Event::Started(session, result) if session == self.session && self.recording => {
                    self.mic_starting = false;
                    match result {
                        Ok(shared) => {
                            self.capture = Some(shared);
                            self.log(&format!("Microphone ready after {}ms; UI remained asynchronous", self.started.elapsed().as_millis()));
                            self.status("recording");
                        }
                        Err(error) => {
                            if self.picker {
                                self.log(&format!("Picker voice unavailable; mouse selection remains active: {error}"));
                                self.display.recording=false;self.status("language_picker");continue;
                            }
                            self.stop(true);
                            self.notice("Check your microphone", &error, 8);
                            self.status("error");
                        }
                    }
                }
                audio::Event::Stopped(session, tail) if session == self.session => {
                    self.mic_stopping = false;
                    if self.finishing { self.total_samples += tail.len(); self.buffer.extend(tail); }
                    self.status(if self.finishing {"processing"} else {"ready"});
                }
                // Old open/close completions must never attach to a newer session.
                _ => {}
            }
        }
    }
    unsafe fn stop(&mut self, cancel: bool) {
        if self.picker {self.close_picker();return;}
        if !self.recording { return; }
        self.collect_audio();
        if self.test_samples.is_none() {
            self.microphone.stop(self.session);
            self.mic_stopping = true;
        }
        self.capture = None; self.mic_starting = false;
        self.recording = false; RECORDING.store(false, Ordering::Relaxed);
        self.tray.hIcon = self.icon; Shell_NotifyIconW(NIM_MODIFY, &self.tray); self.display.recording = false;
        self.display.cue("Mic off");
        if cancel {
            self.session += 1; self.inflight = false; self.buffer.clear(); self.pending.clear(); self.finishing = false;
            self.mic_stopping = false;
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
        if self.inflight || self.mic_stopping { return; }
        if self.picker {
            if !self.picker_has_voice {
                // Retain a short lead-in for soft consonants without decoding
                // repeated silence or growing the mouse-only audio buffer.
                let excess=self.buffer.len().saturating_sub(3200);
                if excess>0 {self.buffer.drain(..excess);}
                return;
            }
            if self.mic_starting || self.buffer.len()<8000 || self.picker_voice.elapsed()<Duration::from_millis(700) {return;}
            if self.last_voice.elapsed()<Duration::from_millis(450) && self.buffer.len()<64000 {return;}
            self.request_id+=1;self.inflight=true;self.worker_loaded=true;self.picker_has_voice=false;self.picker_voice=Instant::now();
            let length=self.buffer.len().min(16000*5);
            let _=self.requests.send(worker::Work::Request(self.session,json!({"command":"select_language","id":self.request_id}),Some(self.buffer[..length].to_vec())));
            return;
        }
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
        let request = json!({"command":"recognize", "id":self.request_id,
            "final":final_chunk, "flush":paused || self.buffer.len() >= 16000*18, "commit_before":cutoff.max(0.), "language":self.settings.language,
            "at_pause":self.last_voice.elapsed() > Duration::from_millis(650),
            "skip_before":self.skip_samples as f32 / 16000.,
            "vocabulary":self.settings.vocabulary, "cleanup":self.settings.cleanup, "accuracy":self.settings.accuracy,
            "previous_text": self.raw.chars().rev().take(160).collect::<String>().chars().rev().collect::<String>()});
        self.inflight = true; self.last_sent_len = len; self.last_request = Instant::now();
        let _ = self.requests.send(worker::Work::Request(self.session, request, Some(self.buffer.clone())));
    }
    unsafe fn receive(&mut self, event: worker::Event) {
        if event.session != self.session { return; }
        self.inflight = false; self.last_work = Instant::now();
        let value = event.value;
        match value["type"].as_str().unwrap_or("error") {
            "language_selected" => {
                if !self.picker {return;}
                let consumed=(value["consumed"].as_u64().unwrap_or(0) as usize).min(self.buffer.len());self.buffer.drain(..consumed);
                if let Some(language)=value["language"].as_str() {self.choose_language(language);return;}
                self.status("language_picker");
            }
            "ready" => { self.speech_model = value["model"].as_str().unwrap_or("unknown").into(); self.log(&format!("Inference ready: {} {}", value["device"], self.speech_model)); if self.recording { self.display.title = "Listening".into(); } self.status(if self.recording { "recording" } else if self.finishing {"processing"} else { "ready" }); }
            "result" => {
                if self.picker {return;}
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
        // A final recognition result can race the driver's stop completion.
        // Keep finishing until all pre-stop samples have been collected.
        if self.mic_stopping { return; }
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
        self.receive_audio();
        if !self.recording && !self.finishing && self.settings_checked.elapsed()>Duration::from_millis(500) {
            let settings=Settings::load(&self.data);
            let changed=settings.dictation_hotkey!=self.settings.dictation_hotkey || settings.language_hotkey!=self.settings.language_hotkey || settings.language!=self.settings.language || settings.overlay_position!=self.settings.overlay_position || settings.microphone!=self.settings.microphone;
            self.settings=settings;self.apply_hotkeys();self.settings_checked=Instant::now();
            if !self.display.is_visible() {self.display.position=self.settings.overlay_position.clone();}
            if changed {self.update_tip();self.status("ready");}
        }
        if self.recording {
            self.collect_audio();
            if !self.picker && self.last_insert.elapsed() >= Duration::from_millis(if self.editor_locked {120} else {500}) { self.try_insert(); }
            self.display.redraw();
            let test_done = self.test_samples.as_ref().is_some_and(|s| self.test_offset >= s.len());
            if self.started.elapsed().as_secs() >= if self.picker {30}else{self.settings.max_recording_seconds as u64} || (test_done&&!self.picker) { self.stop(false); }
        }
        while let Ok(event) = self.responses.try_recv() { self.receive(event); }
        while let Some(event)=self.input.poll() {self.receive_input(event);}
        self.dispatch();
        if !self.recording && !self.finishing && !self.inflight && self.worker_loaded && self.last_work.elapsed() >= Duration::from_secs(90) {
            let _ = self.requests.send(worker::Work::Unload); self.worker_loaded = false; self.status("ready");
        }
        if !self.frame_clock {self.frame();}
        // Audio/controller polling may slow down; animation never follows it.
        let interval = if !self.recording && !self.finishing && !self.display.is_visible() { 1000 }
            else if self.recording && self.started.elapsed().as_millis() > 1200 { 33 } else { 16 };
        if interval != self.timer_interval { SetTimer(self.display.hwnd, 1, interval, None); self.timer_interval = interval; }
    }
    unsafe fn frame(&mut self) {
        if self.hide_at.is_some_and(|at| Instant::now() >= at) {self.display.hide();self.hide_at=None;}
        self.display.animate();
        if self.picker_pending && !self.recording && !self.finishing && !self.inflight && !self.mic_stopping && !self.display.is_visible() {self.open_picker();}
    }
    unsafe fn menu(&mut self) {
        if !self.recording && !self.finishing { self.settings = Settings::load(&self.data); }
        let menu = CreatePopupMenu();
        for (id, title) in [(106,"Change language"),(102, "English only"), (103, "Indonesian only"),(107,"English + Indonesian"), (100, "History & settings"),(109,"Audio & appearance"),(108,"Keyboard shortcuts"), (104, "Start with Windows"), (105, "Quit Dictately")] {
            let checked = (id == 102 && self.settings.language == "en")
                || (id == 103 && self.settings.language == "id") || (id==107 && self.settings.language=="both") || (id == 104 && startup_command().is_some());
            let disabled = if matches!(id,102|103|107) && (self.recording || self.finishing) { MF_GRAYED } else { 0 };
            AppendMenuW(menu, MF_STRING | disabled | if checked { MF_CHECKED } else { 0 }, id, wide(title).as_ptr());
        }
        let mut point: POINT = std::mem::zeroed(); GetCursorPos(&mut point); SetForegroundWindow(self.display.hwnd);
        // The system tray menu runs a nested Windows message loop. Keep the
        // timer fallback painting until our frame-clock loop regains control.
        let frame_clock=self.frame_clock;self.frame_clock=false;
        let selection = TrackPopupMenu(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, point.x, point.y, 0, self.display.hwnd, std::ptr::null());
        self.frame_clock=frame_clock;
        DestroyMenu(menu);
        match selection {
            100|108|109 => { self.open_settings(match selection {108=>"--shortcuts",109=>"--preferences",_=>""}); }
            106=>self.request_picker(),
            102|103|107 => self.choose_language(match selection {103=>"id",107=>"both",_=>"en"}),
            104 => { set_startup(&self.root, startup_command().is_none()); }
            105 => { PostMessageW(self.display.hwnd, QUIT, 0, 0); }
            _ => {}
        }
    }
    fn open_settings(&self,tab:&str) {
        let bundled = self.root.join("runtime/settings/DictatelySettings.exe");
        let mut command = if bundled.is_file() { Command::new(bundled) } else {
            let mut python = Command::new(self.root.join(".venv/Scripts/pythonw.exe"));
            python.arg(self.root.join("app/settings_window.py")); python
        };
        if !tab.is_empty() {command.arg(tab);}
        let _ = command.current_dir(&self.root).env("DICTATELY_ROOT", &self.root).creation_flags(0x08000000).spawn();
    }
}

fn unix() -> u64 { SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs() }
fn append_phrase(target: &mut String, phrase: &str) {
    if !target.is_empty() && !target.ends_with(char::is_whitespace) { target.push(' '); } target.push_str(phrase.trim());
}
fn startup_command() -> Option<String> { run_command("Dictately") }
fn run_command(name:&str) -> Option<String> {
    unsafe {
        let mut key = std::ptr::null_mut();
        if RegOpenKeyExW(HKEY_CURRENT_USER, wide("Software\\Microsoft\\Windows\\CurrentVersion\\Run").as_ptr(), 0, KEY_READ, &mut key) != 0 { return None; }
        let mut bytes = [0u16; 2048]; let mut size = 4096;
        let result = RegQueryValueExW(key, wide(name).as_ptr(), std::ptr::null(), std::ptr::null_mut(), bytes.as_mut_ptr() as _, &mut size);
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
            let value = wide(&format!("\"{}\"", root.join("Dictately.exe").display()));
            RegSetValueExW(key, wide("Dictately").as_ptr(), 0, REG_SZ, value.as_ptr() as _, (value.len() * 2) as u32);
        } else { RegDeleteValueW(key, wide("Dictately").as_ptr()); }
        RegDeleteValueW(key, wide("LocalFlow").as_ptr());
        for previous in ["Transcribr","Transcriber"] {
            if run_command(previous).is_some_and(|command|command.eq_ignore_ascii_case(&format!("\"{}\"",root.join(format!("{previous}.exe")).display()))) {
                RegDeleteValueW(key,wide(previous).as_ptr());
            }
        }
        RegCloseKey(key);
    }
}

unsafe extern "system" fn keyboard(code: i32, wp: WPARAM, lp: LPARAM) -> LRESULT {
    if code >= 0 {
        let key = &*(lp as *const KBDLLHOOKSTRUCT);
        if key.flags & LLKHF_INJECTED == 0 || key.dwExtraInfo != 0x4C464C4F {
            let down=wp as u32==WM_KEYDOWN || wp as u32==WM_SYSKEYDOWN;
            let up=wp as u32==WM_KEYUP || wp as u32==WM_SYSKEYUP;
            let modifier=hotkeys::modifier(key.vkCode,down);
            let dictation=hotkeys::DICTATION.load(Ordering::Relaxed);
            let language=hotkeys::LANGUAGE.load(Ordering::Relaxed);
            let candidate=key.vkCode==dictation&255 || key.vkCode==language&255;
            if candidate && hotkeys::capture_active() {
                F12_DOWN.store(false,Ordering::Relaxed);LANGUAGE_DOWN.store(false,Ordering::Relaxed);
                return CallNextHookEx(std::ptr::null_mut(),code,wp,lp);
            }
            if candidate {
                if down {
                    let chord=(hotkeys::modifiers()<<8)|key.vkCode;
                    if chord==dictation {F12_DOWN.store(true,Ordering::Relaxed);return 1;}
                    if chord==language {LANGUAGE_DOWN.store(true,Ordering::Relaxed);return 1;}
                    if F12_DOWN.load(Ordering::Relaxed)||LANGUAGE_DOWN.load(Ordering::Relaxed) {return 1;}
                } else if up {
                    if key.vkCode==dictation&255 && F12_DOWN.swap(false,Ordering::Relaxed) {
                        PostMessageW(HWND_ATOMIC.load(Ordering::Relaxed) as HWND,TOGGLE,GetForegroundWindow() as WPARAM,0);return 1;
                    }
                    if key.vkCode==language&255 && LANGUAGE_DOWN.swap(false,Ordering::Relaxed) {
                        PostMessageW(HWND_ATOMIC.load(Ordering::Relaxed) as HWND,LANGUAGE,0,0);return 1;
                    }
                }
            }
            if !modifier && down {typing::user_activity();}
            if key.vkCode == VK_ESCAPE as u32 && down && RECORDING.load(Ordering::Relaxed) && !hotkeys::capture_active() {
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
            WM_SETCURSOR if lp as u32&0xffff==HTCLIENT => {
                let mut point=POINT{x:0,y:0};GetCursorPos(&mut point);
                windows_sys::Win32::Graphics::Gdi::ScreenToClient(hwnd,&mut point);
                let cursor=if app.display.option_at(point.x,point.y).is_some() {IDC_HAND}else{IDC_ARROW};
                SetCursor(LoadCursorW(std::ptr::null_mut(),cursor));return 1;
            }
            WM_TIMER => { app.tick(); return 0; }
            TOGGLE => { app.toggle(wp as HWND); return 0; }
            LANGUAGE => {app.request_picker();return 0;}
            CANCEL => { app.stop(true); return 0; }
            WM_LBUTTONUP if app.picker => {
                let x=(lp as u32&0xffff) as i16 as i32;let y=((lp as u32>>16)&0xffff) as i16 as i32;
                if let Some(index)=app.display.option_at(x,y) {app.choose_language(["en","id","both"][index]);}return 0;
            }
            WM_MOUSEMOVE if app.picker => {
                app.display.hovered=app.display.option_at((lp as u32&0xffff) as i16 as i32,((lp as u32>>16)&0xffff) as i16 as i32);return 0;
            }
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
        if args.iter().any(|arg|arg=="--list-microphones") {
            println!("{}",serde_json::to_string(&audio::devices()).unwrap());return;
        }
        if args.iter().any(|arg| arg == "--quit-running") {
            for class in ["DictatelyNativeOverlay","TranscribrNativeOverlay","TranscriberNativeOverlay"] {
            let window = FindWindowW(wide(class).as_ptr(), std::ptr::null());
            if !window.is_null() {
                let mut pid = 0; GetWindowThreadProcessId(window, &mut pid);
                let process = OpenProcess(0x00100000, 0, pid); // SYNCHRONIZE only
                if process.is_null() { std::process::exit(1); }
                let posted = PostMessageW(window, QUIT, 0, 0);
                let stopped = posted != 0 && WaitForSingleObject(process, 10000) == WAIT_OBJECT_0;
                CloseHandle(process);
                if !stopped { std::process::exit(1); }
            }
            }
            return;
        }
        if args.iter().any(|arg| arg == "--check-input") {
            let typer = typing::Typer::new();
            let result = json!({"captured":typer.capture(std::ptr::null_mut()).is_some(), "diagnostic":typer.diagnostic()});
            if let Some(path)=std::env::var_os("TRANSCRIBER_INPUT_DIAGNOSTIC") {let _=fs::write(path, result.to_string());}
            return;
        }
        let exe = std::env::current_exe().unwrap();
        let root = exe.ancestors().find(|p| p.join("app/live_worker.py").is_file()).unwrap().to_path_buf();
        let data_override=std::env::var_os("DICTATELY_DATA").or_else(||std::env::var_os("LOCALFLOW_DATA"));
        let migrate=data_override.is_none();
        let data = data_override.map(PathBuf::from).unwrap_or_else(|| PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap()).join("Dictately"));
        fs::create_dir_all(&data).ok();
        if migrate {
            for previous in ["Transcribr", "Transcriber", "LocalFlow"] {
                let old = PathBuf::from(std::env::var_os("LOCALAPPDATA").unwrap()).join(previous);
                for name in ["settings.json", "history.json"] {
                    if let Ok(mut bytes)=fs::read(old.join(name)) {
                        if name=="settings.json" {
                            if let Ok(mut value)=serde_json::from_slice::<Value>(&bytes) {
                                if value["language_hotkey"]==122 && value["dictation_hotkey"]!=121 {value["language_hotkey"]=json!(121);bytes=serde_json::to_vec_pretty(&value).unwrap();}
                            }
                        }
                        if let Ok(mut destination)=fs::OpenOptions::new().write(true).create_new(true).open(data.join(name)) {let _=destination.write_all(&bytes);}
                    }
                }
            }
            if startup_command().is_none() && run_command("Transcribr").is_some_and(|command|command.eq_ignore_ascii_case(&format!("\"{}\"",root.join("Transcribr.exe").display()))) {
                set_startup(&root,true);
            }
        }
        let settings = Settings::load(&data);
        if args.iter().any(|arg|arg=="--settings") {
            let bundled=root.join("runtime/settings/DictatelySettings.exe");
            let mut command=if bundled.is_file() {Command::new(bundled)} else {
                let mut python=Command::new(root.join(".venv/Scripts/pythonw.exe"));python.arg(root.join("app/settings_window.py"));python
            };
            if args.iter().any(|arg|arg=="--shortcuts") {command.arg("--shortcuts");}
            if args.iter().any(|arg|arg=="--preferences") {command.arg("--preferences");}
            if command.current_dir(&root).env("DICTATELY_ROOT",&root).creation_flags(0x08000000).spawn().is_err() {std::process::exit(1);}
            return;
        }
        if let Some(index)=args.iter().position(|arg|arg=="--set-hotkeys") {
            let dictation=args.get(index+1).and_then(|v|v.parse::<u32>().ok()).unwrap_or(0);
            let language=args.get(index+2).and_then(|v|v.parse::<u32>().ok()).unwrap_or(0);
            if !hotkeys::pair(dictation,language) {std::process::exit(2);}
            let value=Settings{dictation_hotkey:dictation,language_hotkey:language,..settings};
            let temporary=data.join("settings.tmp");
            if fs::write(&temporary,serde_json::to_vec_pretty(&value).unwrap()).is_err() || fs::rename(temporary,data.join("settings.json")).is_err() {std::process::exit(1);}
            return;
        }
        let _ = fs::write(data.join("settings.json"), serde_json::to_vec_pretty(&settings).unwrap());
        if args.iter().any(|a| a == "--enable-startup") { set_startup(&root, true); return; }
        if args.iter().any(|a| a == "--check-mic") {
            let result = match audio::Capture::start(&settings.microphone) {
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
        let mutex_name=if args.iter().any(|a|a=="--preview-language-panel") {format!("Local\\Dictately.PanelPreview.{}",std::process::id())} else {"Local\\LocalFlow.Dictation.v1".into()};
        let mutex = CreateMutexW(std::ptr::null(), 0, wide(&mutex_name).as_ptr());
        if GetLastError() == ERROR_ALREADY_EXISTS { CloseHandle(mutex); return; }
        let test_samples = args.iter().position(|a| a == "--test-pcm").and_then(|i| args.get(i+1)).map(|path| {
            fs::read(path).unwrap().chunks_exact(2).map(|b| i16::from_le_bytes([b[0],b[1]])).collect::<Vec<_>>() });
        let instance = GetModuleHandleW(std::ptr::null()); let cls_name = wide("DictatelyNativeOverlay");
        let class = WNDCLASSW { lpfnWndProc: Some(window_proc), hInstance: instance, hIcon:LoadIconW(instance,1usize as *const u16), hCursor:LoadCursorW(std::ptr::null_mut(),IDC_ARROW),lpszClassName: cls_name.as_ptr(), ..std::mem::zeroed() };
        RegisterClassW(&class);
        let hwnd = CreateWindowExW(WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_LAYERED, cls_name.as_ptr(), wide("Dictately").as_ptr(),
            WS_POPUP, 0, 0, display::WIDTH, display::HEIGHT, std::ptr::null_mut(), std::ptr::null_mut(), instance, std::ptr::null());
        if hwnd.is_null() { CloseHandle(mutex); return; } HWND_ATOMIC.store(hwnd as isize, Ordering::Relaxed);
        let mut app = Box::new(App::new(root, data, hwnd, test_samples));
        app.test_target = args.iter().position(|a| a == "--test-target").and_then(|i| args.get(i+1)).and_then(|s| s.parse().ok());
        APP = &mut *app;
        if args.iter().any(|arg|arg=="--preview-language-panel") {
            app.display.set_picker(true);app.display.selected=0;app.display.recording=true;app.display.cue("");app.display.show("","");
            for _ in 0..45 {app.display.animate();std::thread::sleep(Duration::from_millis(16));}
            app.display.hide();for _ in 0..24 {app.display.animate();std::thread::sleep(Duration::from_millis(16));}
            Shell_NotifyIconW(NIM_DELETE,&app.tray);DestroyWindow(hwnd);APP=std::ptr::null_mut();CloseHandle(mutex);return;
        }
        hotkeys::initialize();
        let hook = SetWindowsHookExW(WH_KEYBOARD_LL, Some(keyboard), instance, 0);
        if hook.is_null() { app.log("F12 hook failed"); app.status("error"); return; }
        let mouse_hook=SetWindowsHookExW(WH_MOUSE_LL,Some(mouse),instance,0);
        if mouse_hook.is_null() {UnhookWindowsHookEx(hook);app.log("Input destination tracking failed");app.status("error");return;}
        // Warm the local models without opening the microphone or showing a popup.
        if !(app.test_target==Some(0) && args.iter().any(|arg|arg=="--test-no-warmup")) {
            app.worker_loaded = true; app.inflight = true;
            let _ = app.requests.send(worker::Work::Request(0, json!({"command":"prepare", "id":0, "cleanup":app.settings.cleanup}), None));
        }
        app.status("ready"); app.log("Native Rust controller ready"); SetTimer(hwnd, 1, 1000, None);
        let mut message: MSG = std::mem::zeroed();
        if let Some(mut clock)=frame_clock::FrameClock::new() {
            app.frame_clock=true;
            'messages:loop {
                if clock.wait(app.display.is_visible()) {app.frame();}
                // Bound message draining so pointer traffic cannot starve frames.
                for _ in 0..64 {
                    if PeekMessageW(&mut message,std::ptr::null_mut(),0,0,PM_REMOVE)==0 {break;}
                    if message.message==WM_QUIT {break 'messages;}
                    TranslateMessage(&message);DispatchMessageW(&message);
                }
            }
        }else{
            app.log("High-resolution frame clock unavailable; using controller timer");
            while GetMessageW(&mut message, std::ptr::null_mut(), 0, 0) > 0 { TranslateMessage(&message); DispatchMessageW(&message); }
        }
        UnhookWindowsHookEx(mouse_hook); UnhookWindowsHookEx(hook); CloseHandle(mutex);
        if !app.icon.is_null() { DestroyIcon(app.icon); } if !app.record_icon.is_null() { DestroyIcon(app.record_icon); }
        APP = std::ptr::null_mut();
    }
}
#[cfg(test)] mod tests {
    use super::*;
    #[test] fn phrase_spacing() { let mut s = "Hello".to_string(); append_phrase(&mut s, " world. "); assert_eq!(s, "Hello world."); }
    #[test] fn defaults_locked_english() {
        let defaults=Settings::default();assert_eq!(defaults.language,"en");
        assert_eq!(defaults.dictation_hotkey,123);assert_eq!(defaults.language_hotkey,121);
        assert_eq!(defaults.overlay_position,"bottom_center");assert!(defaults.microphone.is_empty());
    }
    #[test] fn preferences_survive_language_and_shortcut_serialization() {
        let mut settings:Settings=serde_json::from_str(r#"{"microphone":"stable-usb-endpoint","overlay_position":"top_right","language":"id"}"#).unwrap();
        settings.language="both".into();settings.language_hotkey=889;
        let saved=serde_json::to_string(&settings).unwrap();
        let loaded:Settings=serde_json::from_str(&saved).unwrap();
        assert_eq!(loaded.microphone,"stable-usb-endpoint");assert_eq!(loaded.overlay_position,"top_right");
        assert_eq!(loaded.language,"both");assert_eq!(loaded.language_hotkey,889);
    }
}
