use std::{fs::OpenOptions, io::{BufRead, BufReader, Write}, os::windows::{io::AsRawHandle, process::CommandExt}, path::PathBuf,
    process::{Child, ChildStdin, Command, Stdio}, sync::{mpsc::{self, Receiver, RecvTimeoutError, Sender}, Arc, atomic::{AtomicU64, Ordering}},
    time::Duration};
use serde_json::{json, Value};
use windows_sys::Win32::{Foundation::{CloseHandle, HANDLE}, System::JobObjects::*};

pub enum Work { Request(u64, Value, Option<Vec<i16>>), Unload }
pub struct Requests {sender:Sender<Work>,latest:Arc<AtomicU64>}
impl Requests {
    pub fn send(&self,work:Work)->Result<(),mpsc::SendError<Work>> {
        if let Work::Request(session,_,_)=&work {self.latest.store(*session,Ordering::Release);}
        self.sender.send(work)
    }
    pub fn cancel_before(&self,session:u64) {self.latest.store(session,Ordering::Release);}
}
pub struct Event { pub session: u64, pub value: Value }

fn request_timeout(value: &Value) -> Duration {
    match value["command"].as_str().unwrap_or("") {
        "prepare" => Duration::from_secs(60),
        _ => Duration::from_secs(25),
    }
}

fn read_line_timeout(lines: &Receiver<Result<Option<String>, String>>, timeout: Duration) -> Result<String, String> {
    match lines.recv_timeout(timeout) {
        Ok(Ok(Some(line))) => Ok(line),
        Ok(Ok(None)) | Err(RecvTimeoutError::Disconnected) => Err("Speech worker stopped; restarting automatically".into()),
        Ok(Err(error)) => Err(error),
        Err(RecvTimeoutError::Timeout) => Err("Speech worker timed out; restarting engine".into()),
    }
}

fn write_request(output: &mut impl Write, metadata: &Value, samples: Option<&[i16]>) -> Result<(), String> {
    let mut header = metadata.clone();
    if let Some(samples) = samples {
        header["audio_format"] = json!("s16le");
        header["audio_samples"] = json!(samples.len());
    }
    serde_json::to_writer(&mut *output, &header).map_err(|e| e.to_string())?;
    output.write_all(b"\n").map_err(|e| e.to_string())?;
    if let Some(samples) = samples {
        // Encode on the worker thread; the UI only copies its contiguous i16 buffer.
        #[cfg(target_endian="little")]
        // SAFETY: i16 samples occupy initialized contiguous bytes. Windows builds
        // are little-endian, matching the wire format; the slice cannot outlive
        // the borrowed samples and is written synchronously on this thread.
        let bytes=unsafe {std::slice::from_raw_parts(samples.as_ptr().cast::<u8>(),std::mem::size_of_val(samples))};
        #[cfg(not(target_endian="little"))]
        let storage:Vec<u8>=samples.iter().flat_map(|sample|sample.to_le_bytes()).collect();
        #[cfg(not(target_endian="little"))]
        let bytes=storage.as_slice();
        output.write_all(bytes).map_err(|e| e.to_string())?;
    }
    output.flush().map_err(|e| e.to_string())
}

struct Process { child: Child, input: ChildStdin, lines: Receiver<Result<Option<String>, String>>, job: HANDLE }
impl Process {
    fn start(root: &PathBuf, data: &PathBuf) -> Result<Self, String> {
        let log = OpenOptions::new().create(true).append(true).open(data.join("live-worker.log")).map_err(|e| e.to_string())?;
        let bundled = root.join("runtime/worker/DictatelyWorker.exe");
        let mut command = if bundled.is_file() { Command::new(bundled) } else {
            let mut python = Command::new(root.join(".venv/Scripts/python.exe"));
            python.arg(root.join("app/live_worker.py")); python
        };
        let mut child = command
            .current_dir(root).creation_flags(0x08000000).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(log)
            .env("PYTHONIOENCODING", "utf-8").env("DICTATELY_ROOT", root).env("LOCALFLOW_DATA", data).spawn().map_err(|e| e.to_string())?;
        unsafe {
            let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if job.is_null() || SetInformationJobObject(job, JobObjectExtendedLimitInformation,
                &limits as *const _ as _, std::mem::size_of_val(&limits) as u32) == 0
                || AssignProcessToJobObject(job, child.as_raw_handle() as HANDLE) == 0 {
                child.kill().ok(); child.wait().ok();
                if !job.is_null() { CloseHandle(job); }
                return Err("Could not manage local inference worker".into());
            }
            let stdout = child.stdout.take().unwrap();
            let (tx, lines) = mpsc::channel();
            // Dedicated reader allows bounded request timeouts; dropping Process kills the job and closes stdout.
            std::thread::spawn(move || {
                let mut reader = BufReader::new(stdout);
                loop {
                    let mut line = String::new();
                    match reader.read_line(&mut line) {
                        Ok(0) => { let _ = tx.send(Ok(None)); break; }
                        Ok(_) => { if tx.send(Ok(Some(line))).is_err() { break; } }
                        Err(error) => { let _ = tx.send(Err(error.to_string())); break; }
                    }
                }
            });
            Ok(Self { input: child.stdin.take().unwrap(), lines, child, job })
        }
    }
    fn request(&mut self, value: &Value, samples: Option<&[i16]>) -> Result<Value, String> {
        write_request(&mut self.input, value, samples)?;
        let line = read_line_timeout(&self.lines, request_timeout(value))?;
        serde_json::from_str(&line).map_err(|e| format!("Speech worker response: {e}"))
    }
}
impl Drop for Process {
    fn drop(&mut self) {
        unsafe { CloseHandle(self.job); }
        self.child.kill().ok(); self.child.wait().ok();
    }
}

pub fn start(root: PathBuf, data: PathBuf) -> (Requests, Receiver<Event>) {
    let (tx, rx) = mpsc::channel();
    let (results, events) = mpsc::channel();
    let latest=Arc::new(AtomicU64::new(0));let wanted=latest.clone();
    std::thread::spawn(move || {
        let mut process: Option<Process> = None;
        while let Ok(work) = rx.recv() {
            match work {
                Work::Unload => { process = None; }
                Work::Request(session, request, samples) => {
                    if session!=wanted.load(Ordering::Acquire) {continue;}
                    let result = (|| {
                        if process.is_none() { process = Some(Process::start(&root, &data)?); }
                        process.as_mut().unwrap().request(&request, samples.as_deref())
                    })();
                    let value = match result { Ok(value) => value, Err(error) => { process = None; json!({"type":"error", "error":error}) } };
                    if results.send(Event { session, value }).is_err() { break; }
                }
            }
        }
    });
    (Requests{sender:tx,latest}, events)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn audio_frame_is_little_endian_and_control_frames_have_no_payload() {
        let mut frame = Vec::new();
        write_request(&mut frame, &json!({"command":"recognize","id":7}), Some(&[-32768, -1, 0, 10, 32767])).unwrap();
        let boundary = frame.iter().position(|byte| *byte == b'\n').unwrap();
        let header: Value = serde_json::from_slice(&frame[..boundary]).unwrap();
        assert_eq!(header["audio_samples"], 5);
        assert_eq!(header["audio_format"], "s16le");
        assert_eq!(&frame[boundary+1..], &[0,128,255,255,0,0,10,0,255,127]);
        frame.clear();
        write_request(&mut frame, &json!({"command":"prepare"}), None).unwrap();
        assert_eq!(frame, b"{\"command\":\"prepare\"}\n");
    }

    #[test]
    fn stalled_worker_times_out_instead_of_blocking_forever() {
        let (_tx, rx) = mpsc::channel::<Result<Option<String>, String>>();
        let err = read_line_timeout(&rx, Duration::from_millis(20)).unwrap_err();
        assert!(err.contains("timed out"), "{err}");
        assert_eq!(request_timeout(&json!({"command":"prepare"})), Duration::from_secs(60));
        assert_eq!(request_timeout(&json!({"command":"recognize"})), Duration::from_secs(25));
    }
}
