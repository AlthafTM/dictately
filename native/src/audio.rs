use std::sync::{Arc, Mutex, atomic::{AtomicBool, AtomicU32, Ordering}};
use windows_sys::Win32::Media::Audio::*;

pub struct SharedAudio {
    pub samples: Mutex<Vec<i16>>,
    pub level: AtomicU32,
    pub running: AtomicBool,
}

pub struct Capture {
    handle: HWAVEIN,
    headers: Vec<Box<WAVEHDR>>,
    _buffers: Vec<Vec<i16>>,
    pub shared: Box<SharedAudio>,
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
    // The UI thread recycles headers in take(); no multimedia API in this callback.
}

impl Capture {
    pub fn start() -> Result<Self, String> {
        unsafe {
            let shared = Box::new(SharedAudio { samples: Mutex::new(Vec::with_capacity(16000 * 20)),
                level: AtomicU32::new(0), running: AtomicBool::new(true) });
            let format = WAVEFORMATEX { wFormatTag: 1, nChannels: 1, nSamplesPerSec: 16000,
                nAvgBytesPerSec: 32000, nBlockAlign: 2, wBitsPerSample: 16, cbSize: 0 };
            let mut handle = std::ptr::null_mut();
            let code = waveInOpen(&mut handle, WAVE_MAPPER, &format, callback as *const () as usize,
                &*shared as *const SharedAudio as usize, CALLBACK_FUNCTION);
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

    pub fn take_into(&mut self, destination: &mut Vec<i16>) -> usize {
        self.shared.samples.lock().map(|mut samples| {
            let count = samples.len();
            destination.extend_from_slice(&samples);
            // Retain the capture allocation between callbacks instead of taking
            // its Vec and forcing the next microphone block to allocate again.
            samples.clear();
            count
        }).unwrap_or(0)
    }

    pub fn stop(&mut self) {
        if self.shared.running.load(Ordering::Relaxed) {
            self.pump_running.store(false, Ordering::Release);
            if let Some(pump) = self.pump.take() { let _ = pump.join(); }
            unsafe { waveInStop(self.handle); waveInReset(self.handle); }
            self.shared.running.store(false, Ordering::SeqCst);
        }
    }

    pub fn level(&self) -> f32 { f32::from_bits(self.shared.level.load(Ordering::Relaxed)) }
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
