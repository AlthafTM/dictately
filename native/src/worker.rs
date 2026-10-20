use std::{fs::OpenOptions, io::{BufRead, BufReader, Write}, os::windows::{io::AsRawHandle, process::CommandExt}, path::PathBuf,
    process::{Child, ChildStdin, ChildStdout, Command, Stdio}, sync::mpsc::{self, Receiver, Sender}};
use serde_json::{json, Value};
use windows_sys::Win32::{Foundation::{CloseHandle, HANDLE}, System::JobObjects::*};

pub enum Work { Request(u64, Value, Option<Vec<i16>>), Unload }
pub struct Event { pub session: u64, pub value: Value }

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
        let bytes: Vec<u8> = samples.iter().flat_map(|sample| sample.to_le_bytes()).collect();
        output.write_all(&bytes).map_err(|e| e.to_string())?;
    }
    output.flush().map_err(|e| e.to_string())
}

struct Process { child: Child, input: ChildStdin, output: BufReader<ChildStdout>, job: HANDLE }
impl Process {
    fn start(root: &PathBuf, data: &PathBuf) -> Result<Self, String> {
        let log = OpenOptions::new().create(true).append(true).open(data.join("live-worker.log")).map_err(|e| e.to_string())?;
        let bundled = root.join("runtime/worker/TranscribrWorker.exe");
        let mut command = if bundled.is_file() { Command::new(bundled) } else {
            let mut python = Command::new(root.join(".venv/Scripts/python.exe"));
            python.arg(root.join("live_worker.py")); python
        };
        let mut child = command
            .current_dir(root).creation_flags(0x08000000).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(log)
            .env("PYTHONIOENCODING", "utf-8").env("TRANSCRIBR_ROOT", root).env("LOCALFLOW_DATA", data).spawn().map_err(|e| e.to_string())?;
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
            Ok(Self { input: child.stdin.take().unwrap(), output: BufReader::new(child.stdout.take().unwrap()), child, job })
        }
    }
    fn request(&mut self, value: &Value, samples: Option<&[i16]>) -> Result<Value, String> {
        write_request(&mut self.input, value, samples)?;
        let mut line = String::new();
        if self.output.read_line(&mut line).map_err(|e| e.to_string())? == 0 {
            return Err("Speech worker stopped; try F12 again".into());
        }
        serde_json::from_str(&line).map_err(|e| format!("Speech worker response: {e}"))
    }
}
impl Drop for Process {
    fn drop(&mut self) {
        unsafe { CloseHandle(self.job); }
        self.child.kill().ok(); self.child.wait().ok();
    }
}

pub fn start(root: PathBuf, data: PathBuf) -> (Sender<Work>, Receiver<Event>) {
    let (tx, rx) = mpsc::channel();
    let (results, events) = mpsc::channel();
    std::thread::spawn(move || {
        let mut process: Option<Process> = None;
        while let Ok(work) = rx.recv() {
            match work {
                Work::Unload => { process = None; }
                Work::Request(session, request, samples) => {
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
    (tx, events)
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
}
