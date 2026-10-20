use std::{sync::{Arc, Mutex, mpsc::{self, Receiver, Sender}, atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering}}, time::Duration};
use windows_sys::Win32::Media::Audio::*;

pub struct SharedAudio {
    pub samples: Mutex<Vec<i16>>,
    pub level: AtomicU32,
    pub running: AtomicBool,
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

enum Command { Start(u64, Duration), Stop(u64) }
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
                    Command::Start(session, delay) => {
                        // Serialize close/open so rapid cancel/restart cannot race
                        // two microphone handles, even if the old open was slow.
                        active = None;
                        if !delay.is_zero() { std::thread::sleep(delay); }
                        let result = Capture::start_if_current(||current.load(Ordering::Acquire) == session).map(|capture| {
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
    pub fn start(&self, session: u64, delay: Duration) {
        self.wanted.store(session, Ordering::Release);
        let _ = self.commands.send(Command::Start(session, delay));
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
    // The pump thread recycles headers; no multimedia API in this callback.
}

impl Capture {
    pub fn start() -> Result<Self, String> {
        Self::start_if_current(||true)
    }
    fn start_if_current(current: impl Fn() -> bool) -> Result<Self, String> {
        if !current() { return Err("Microphone start cancelled".into()); }
        unsafe {
            let shared = Arc::new(SharedAudio { samples: Mutex::new(Vec::with_capacity(16000 * 20)),
                level: AtomicU32::new(0), running: AtomicBool::new(true) });
            let format = WAVEFORMATEX { wFormatTag: 1, nChannels: 1, nSamplesPerSec: 16000,
                nAvgBytesPerSec: 32000, nBlockAlign: 2, wBitsPerSample: 16, cbSize: 0 };
            let mut handle = std::ptr::null_mut();
            let code = waveInOpen(&mut handle, WAVE_MAPPER, &format, callback as *const () as usize,
                Arc::as_ptr(&shared) as usize, CALLBACK_FUNCTION);
            if code != 0 { return Err(format!("Microphone unavailable (Windows code {code})")); }
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
            // Recycle independently of UI Automation/typing. A slow editor must not
            // exhaust the microphone queue and punch holes in the recorded speech.
            let active = capture.pump_running.clone();
            let headers: Vec<usize> = capture.headers.iter_mut().map(|h| &mut **h as *mut WAVEHDR as usize).collect();
            let device = handle as usize;
            capture.pump = Some(std::thread::spawn(move || {
                while active.load(Ordering::Acquire) {
                    for address in &headers {
                        let header = *address as *mut WAVEHDR;
                        if std::ptr::read_volatile(std::ptr::addr_of!((*header).dwFlags)) & WHDR_DONE != 0 {
                            waveInAddBuffer(device as HWAVEIN, header, std::mem::size_of::<WAVEHDR>() as u32);
                        }
                    }
                    std::thread::sleep(std::time::Duration::from_millis(10));
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
        microphone.commands.send(Command::Start(7, Duration::ZERO)).unwrap();
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
}
