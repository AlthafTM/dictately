use std::{sync::{Arc, Mutex, mpsc::{self, Receiver, Sender}, atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering}}, time::Duration};
use windows_sys::Win32::Media::Audio::*;
use windows_sys::Win32::{Foundation::{CloseHandle, HANDLE, WAIT_OBJECT_0}, System::Threading::{CreateEventW, SetEvent, WaitForSingleObject, INFINITE}};
use serde::Serialize;

#[derive(Clone, Serialize)]
pub struct Device { pub index:u32, pub id:String, pub name:String }

/// Query an endpoint identity without opening or recording from the microphone.
pub fn devices() -> Vec<Device> {
    unsafe { (0..waveInGetNumDevs()).filter_map(|index| {
        let mut caps:WAVEINCAPSW=std::mem::zeroed();
        if waveInGetDevCapsW(index as usize,&mut caps,std::mem::size_of::<WAVEINCAPSW>() as u32)!=0 {return None;}
        let label=caps.szPname;let manufacturer=caps.wMid;let product=caps.wPid;
        let name=String::from_utf16_lossy(&label[..label.iter().position(|c|*c==0).unwrap_or(label.len())]);
        // DRV_QUERYFUNCTIONINSTANCEIDSIZE / DRV_QUERYFUNCTIONINSTANCEID, mmddk.h.
        // Endpoint IDs survive reordering; numeric WinMM indices do not.
        let mut bytes=0u32;
        let id=if waveInMessage(index as HWAVEIN,2066,&mut bytes as *mut u32 as usize,0)==0 && (2..=65536).contains(&bytes) {
            let mut buffer=vec![0u16;((bytes+1)/2) as usize];
            if waveInMessage(index as HWAVEIN,2065,buffer.as_mut_ptr() as usize,bytes as usize)==0 {
                String::from_utf16_lossy(&buffer[..buffer.iter().position(|c|*c==0).unwrap_or(buffer.len())])
            } else {String::new()}
        } else {String::new()};
        let id=if id.is_empty() {format!("legacy:{manufacturer}:{product}:{name}")} else {id};
        Some(Device{index,id,name})
    }).collect() }
}

fn resolve_device(preference:&str, devices:&[Device])->Result<u32,String> {
    if preference.is_empty() {return Ok(WAVE_MAPPER);}
    let matches:Vec<_>=devices.iter().filter(|device|device.id==preference).collect();
    match matches.as_slice() {
        [device]=>Ok(device.index),
        []=>Err("Your preferred microphone is disconnected. Choose an available microphone in Audio & appearance".into()),
        _=>Err("Microphone identity is ambiguous. Choose System default in Audio & appearance".into()),
    }
}

pub struct SharedAudio {
    pub samples: Mutex<Vec<i16>>,
    pub level: AtomicU32,
    pub running: AtomicBool,
    recycle_event: usize,
}

impl SharedAudio {
    pub fn take_into(&self, destination: &mut Vec<i16>) -> usize {
        self.samples.lock().map(|mut samples| {
            let count = samples.len();
            destination.extend_from_slice(&samples);
            samples.clear();
            count
        }).unwrap_or(0)
    }
    pub fn level(&self) -> f32 { f32::from_bits(self.level.load(Ordering::Relaxed)) }
}

enum Command { Start(u64, Duration, String), Stop(u64) }
pub enum Event { Started(u64, Result<Arc<SharedAudio>, String>), Stopped(u64, Vec<i16>) }

/// The same thread owns and closes all WinMM handles. Only sample storage and
/// completion events cross threads; UI/hotkey handling never waits for a driver.
pub struct Microphone { commands: Sender<Command>, events: Receiver<Event>, wanted: Arc<AtomicU64> }
impl Microphone {
    pub fn new() -> Self {
        let (commands, requests) = mpsc::channel();
        let (results, events) = mpsc::channel();
        let wanted = Arc::new(AtomicU64::new(0));
        let current = wanted.clone();
        std::thread::spawn(move || {
            let mut active: Option<(u64, Capture)> = None;
            while let Ok(command) = requests.recv() {
                match command {
                    Command::Start(session, delay, preference) => {
                        // Serialize close/open so rapid cancel/restart cannot race
                        // two microphone handles, even if the old open was slow.
                        if current.load(Ordering::Acquire)!=session {
                            let _=results.send(Event::Started(session,Err("Microphone start cancelled".into())));continue;
                        }
                        active = None;
                        let deadline=std::time::Instant::now()+delay;
                        while std::time::Instant::now()<deadline&&current.load(Ordering::Acquire)==session {
                            std::thread::sleep(deadline.saturating_duration_since(std::time::Instant::now()).min(Duration::from_millis(10)));
                        }
                        let result = Capture::start_if_current(&preference,||current.load(Ordering::Acquire) == session).map(|capture| {
                            let shared = capture.shared.clone();
                            active = Some((session, capture));
                            shared
                        });
                        if results.send(Event::Started(session, result)).is_err() { break; }
                    }
                    Command::Stop(session) => {
                        let mut tail = Vec::new();
                        if active.as_ref().is_some_and(|(owner, _)| *owner == session) {
                            if let Some((_, mut capture)) = active.take() {
                                capture.stop();
                                tail = capture.take();
                                // Drop/close happens before reporting completion.
                            }
                        }
                        if results.send(Event::Stopped(session, tail)).is_err() { break; }
                    }
                }
            }
        });
        Self { commands, events, wanted }
    }
    pub fn start(&self, session: u64, delay: Duration, preference: &str) {
        self.wanted.store(session, Ordering::Release);
        let _ = self.commands.send(Command::Start(session, delay, preference.into()));
    }
    pub fn stop(&self, session: u64) {
        let _ = self.wanted.compare_exchange(session, 0, Ordering::AcqRel, Ordering::Acquire);
        let _ = self.commands.send(Command::Stop(session));
    }
    pub fn poll(&self) -> Option<Event> { self.events.try_recv().ok() }
}
impl Drop for Microphone {
    fn drop(&mut self) { self.wanted.store(0, Ordering::Release); }
}

pub struct Capture {
    handle: HWAVEIN,
    headers: Vec<Box<WAVEHDR>>,
    _buffers: Vec<Vec<i16>>,
    pub shared: Arc<SharedAudio>,
    pump_running: Arc<AtomicBool>,
    pump: Option<std::thread::JoinHandle<()>>,
}

unsafe extern "system" fn callback(_handle: HWAVEIN, message: u32, instance: usize, param: usize, _: usize) {
    if message != 0x03C0 || instance == 0 { return; } // MM_WIM_DATA
    let state = &*(instance as *const SharedAudio);
    if !state.running.load(Ordering::Relaxed) { return; }
    let header = &mut *(param as *mut WAVEHDR);
    let samples = std::slice::from_raw_parts(header.lpData as *const i16, header.dwBytesRecorded as usize / 2);
    let rms = (samples.iter().map(|v| (*v as f32 / 32768.).powi(2)).sum::<f32>() / samples.len().max(1) as f32).sqrt();
    state.level.store(rms.to_bits(), Ordering::Relaxed);
    if let Ok(mut buffer) = state.samples.lock() {
        // Bounded even if a model or driver stalls: at most 150 seconds.
        if buffer.len() + samples.len() < 16000 * 150 { buffer.extend_from_slice(samples); }
    }
    SetEvent(state.recycle_event as HANDLE);
    // The pump thread recycles headers; no multimedia API in this callback.
}

impl Capture {
    pub fn start(preference:&str) -> Result<Self, String> {
        Self::start_if_current(preference,||true)
    }
    fn start_if_current(preference:&str,current: impl Fn() -> bool) -> Result<Self, String> {
        if !current() { return Err("Microphone start cancelled".into()); }
        let device=if preference.is_empty() {WAVE_MAPPER} else {resolve_device(preference,&devices())?};
        if !current() { return Err("Microphone start cancelled".into()); }
        unsafe {
            let recycle_event=CreateEventW(std::ptr::null(),0,0,std::ptr::null());
            if recycle_event.is_null() {return Err("Microphone notification event unavailable".into());}
            let shared = Arc::new(SharedAudio { samples: Mutex::new(Vec::with_capacity(16000 * 2)),
                level: AtomicU32::new(0), running: AtomicBool::new(true),recycle_event:recycle_event as usize });
            let format = WAVEFORMATEX { wFormatTag: 1, nChannels: 1, nSamplesPerSec: 16000,
                nAvgBytesPerSec: 32000, nBlockAlign: 2, wBitsPerSample: 16, cbSize: 0 };
            let mut handle = std::ptr::null_mut();
            let code = waveInOpen(&mut handle, device, &format, callback as *const () as usize,
                Arc::as_ptr(&shared) as usize, CALLBACK_FUNCTION);
            if code != 0 { CloseHandle(recycle_event);return Err(format!("Microphone unavailable (Windows code {code})")); }
            let mut capture = Self { handle, headers: Vec::new(), _buffers: Vec::new(), shared,
                pump_running: Arc::new(AtomicBool::new(true)), pump:None };
            for _ in 0..6 {
                let mut buffer = vec![0i16; 1600];
                let mut header = Box::new(WAVEHDR { lpData: buffer.as_mut_ptr() as _, dwBufferLength: 3200, ..std::mem::zeroed() });
                let code = waveInPrepareHeader(handle, &mut *header, std::mem::size_of::<WAVEHDR>() as u32);
                if code != 0 { return Err(format!("Microphone buffer failed ({code})")); }
                capture._buffers.push(buffer);
                capture.headers.push(header);
                let h = capture.headers.last_mut().unwrap();
                if waveInAddBuffer(handle, &mut **h, std::mem::size_of::<WAVEHDR>() as u32) != 0 {
                    return Err("Microphone queue failed".into());
                }
            }
            if !current() { return Err("Microphone start cancelled".into()); }
            if waveInStart(handle) != 0 { return Err("Microphone could not start".into()); }
            if !current() {return Err("Microphone start cancelled".into());}
            // Recycle independently of UI Automation/typing. A slow editor must not
            // exhaust the microphone queue and punch holes in the recorded speech.
            let active = capture.pump_running.clone();
            let headers: Vec<usize> = capture.headers.iter_mut().map(|h| &mut **h as *mut WAVEHDR as usize).collect();
            let device = handle as usize;
            let wake=recycle_event as usize;
            capture.pump = Some(std::thread::spawn(move || {
                loop {
                    if WaitForSingleObject(wake as HANDLE,INFINITE)!=WAIT_OBJECT_0 || !active.load(Ordering::Acquire) {break;}
                    for address in &headers {
                        let header = *address as *mut WAVEHDR;
                        if std::ptr::read_volatile(std::ptr::addr_of!((*header).dwFlags)) & WHDR_DONE != 0 {
                            waveInAddBuffer(device as HWAVEIN, header, std::mem::size_of::<WAVEHDR>() as u32);
                        }
                    }
                }
            }));
            Ok(capture)
        }
    }

    pub fn take(&mut self) -> Vec<i16> {
        let result = self.shared.samples.lock().map(|mut samples| std::mem::take(&mut *samples)).unwrap_or_default();
        result
    }

    pub fn stop(&mut self) {
        if self.shared.running.load(Ordering::Relaxed) {
            self.pump_running.store(false, Ordering::Release);
            unsafe {SetEvent(self.shared.recycle_event as HANDLE);}
            if let Some(pump) = self.pump.take() { let _ = pump.join(); }
            unsafe { waveInStop(self.handle); waveInReset(self.handle); }
            self.shared.running.store(false, Ordering::SeqCst);
        }
    }

    pub fn level(&self) -> f32 { self.shared.level() }
}

impl Drop for Capture {
    fn drop(&mut self) {
        unsafe {
            self.stop();
            for header in &mut self.headers {
                waveInUnprepareHeader(self.handle, &mut **header, std::mem::size_of::<WAVEHDR>() as u32);
            }
            waveInClose(self.handle);
            CloseHandle(self.shared.recycle_event as HANDLE);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn stale_queued_start_never_opens_the_microphone() {
        let microphone = Microphone::new();
        // The desired token is already cancelled before stale work is queued,
        // so this test cannot open a real device even on a slow CI machine.
        microphone.commands.send(Command::Start(7, Duration::ZERO,String::new())).unwrap();
        microphone.stop(7);
        let deadline = std::time::Instant::now() + Duration::from_secs(2);
        let mut cancelled = false;
        loop {
            assert!(std::time::Instant::now() < deadline, "Audio worker did not complete cancellation");
            match microphone.poll() {
                Some(Event::Started(7, result)) => { assert!(result.is_err()); cancelled = true; }
                Some(Event::Stopped(7, tail)) => { assert!(cancelled); assert!(tail.is_empty()); break; }
                Some(_) => panic!("Unexpected microphone session"),
                None => std::thread::sleep(Duration::from_millis(2)),
            }
        }
    }

    #[test]
    fn cancelling_delayed_open_does_not_wait_for_the_delay_or_open_a_device() {
        let microphone=Microphone::new();
        microphone.start(9,Duration::from_secs(30),"");
        std::thread::sleep(Duration::from_millis(20));
        let start=std::time::Instant::now();microphone.stop(9);
        loop {
            assert!(start.elapsed()<Duration::from_secs(1),"Cancelled start blocked shutdown");
            match microphone.poll() {
                Some(Event::Started(9,result))=>assert!(result.is_err()),
                Some(Event::Stopped(9,tail))=>{assert!(tail.is_empty());break;},
                Some(_)=>panic!("Unexpected session"),
                None=>std::thread::sleep(Duration::from_millis(2)),
            }
        }
    }

    #[test]
    #[ignore="Requires the Windows default microphone; records briefly in memory only"]
    fn rapid_real_device_switches_release_the_final_capture() {
        let microphone=Microphone::new();
        for session in 1..=16 {
            microphone.start(session,Duration::ZERO,"");
            std::thread::sleep(Duration::from_millis(15));
            microphone.stop(session);
        }
        microphone.start(17,Duration::ZERO,"");
        let started=std::time::Instant::now();
        let shared=loop {
            assert!(started.elapsed()<Duration::from_secs(8),"Stale device requests delayed restart");
            match microphone.poll() {
                Some(Event::Started(17,result))=>break result.expect("Default microphone failed to open"),
                _=>std::thread::sleep(Duration::from_millis(2)),
            }
        };
        assert!(shared.running.load(Ordering::Acquire));
        microphone.stop(17);
        let stopped=std::time::Instant::now();
        loop {
            assert!(stopped.elapsed()<Duration::from_secs(3),"Microphone did not close");
            match microphone.poll() {
                Some(Event::Stopped(17,_))=>break,
                _=>std::thread::sleep(Duration::from_millis(2)),
            }
        }
        assert!(!shared.running.load(Ordering::Acquire),"Stopped capture is still running");
    }

    #[test]
    fn preferred_microphone_survives_reordering_and_never_silently_falls_back() {
        let devices=vec![Device{index:4,id:"usb-stable".into(),name:"USB mic".into()}];
        assert_eq!(resolve_device("",&devices).unwrap(),WAVE_MAPPER);
        assert_eq!(resolve_device("usb-stable",&devices).unwrap(),4);
        assert!(resolve_device("disconnected",&devices).is_err());
        let mut duplicates=devices.clone();duplicates.push(devices[0].clone());
        assert!(resolve_device("usb-stable",&duplicates).is_err());
    }

    #[test]
    fn completed_audio_signals_recycler_and_preserves_stop_tail() {
        unsafe {
            let event=CreateEventW(std::ptr::null(),0,0,std::ptr::null());assert!(!event.is_null());
            let state=SharedAudio{samples:Mutex::new(Vec::new()),level:AtomicU32::new(0),running:AtomicBool::new(true),recycle_event:event as usize};
            assert_ne!(WaitForSingleObject(event,0),WAIT_OBJECT_0);
            let mut samples=[1200i16,-1200,600,-600];
            let mut header=WAVEHDR{lpData:samples.as_mut_ptr() as _,dwBytesRecorded:8,..std::mem::zeroed()};
            callback(std::ptr::null_mut(),0x03C0,&state as *const SharedAudio as usize,&mut header as *mut WAVEHDR as usize,0);
            assert_eq!(WaitForSingleObject(event,0),WAIT_OBJECT_0);
            assert_eq!(*state.samples.lock().unwrap(),samples);
            assert!(state.level()>0.);
            state.running.store(false,Ordering::Relaxed);
            callback(std::ptr::null_mut(),0x03C0,&state as *const SharedAudio as usize,&mut header as *mut WAVEHDR as usize,0);
            assert_ne!(WaitForSingleObject(event,0),WAIT_OBJECT_0);
            CloseHandle(event);
        }
    }
}
