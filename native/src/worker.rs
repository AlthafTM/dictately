use std::{fs::OpenOptions, io::{BufRead, BufReader, Write}, os::windows::{io::AsRawHandle, process::CommandExt}, path::PathBuf,
    process::{Child, ChildStdin, ChildStdout, Command, Stdio}, sync::mpsc::{self, Receiver, Sender}};
use serde_json::{json, Value};
use windows_sys::Win32::{Foundation::{CloseHandle, HANDLE}, System::JobObjects::*};

pub enum Work { Request(u64, Value), Unload }
pub struct Event { pub session: u64, pub value: Value }

struct Process { child: Child, input: ChildStdin, output: BufReader<ChildStdout>, job: HANDLE }
impl Process {
    fn start(root: &PathBuf, data: &PathBuf) -> Result<Self, String> {
        let log = OpenOptions::new().create(true).append(true).open(data.join("live-worker.log")).map_err(|e| e.to_string())?;
        let mut child = Command::new(root.join(".venv/Scripts/python.exe")).arg(root.join("live_worker.py"))
            .current_dir(root).creation_flags(0x08000000).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(log)
            .env("PYTHONIOENCODING", "utf-8").env("LOCALFLOW_DATA", data).spawn().map_err(|e| e.to_string())?;
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
    fn request(&mut self, value: &Value) -> Result<Value, String> {
        serde_json::to_writer(&mut self.input, value).map_err(|e| e.to_string())?;
        self.input.write_all(b"\n").and_then(|_| self.input.flush()).map_err(|e| e.to_string())?;
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
                Work::Request(session, request) => {
                    let result = (|| {
                        if process.is_none() { process = Some(Process::start(&root, &data)?); }
                        process.as_mut().unwrap().request(&request)
                    })();
                    let value = match result { Ok(value) => value, Err(error) => { process = None; json!({"type":"error", "error":error}) } };
                    if results.send(Event { session, value }).is_err() { break; }
                }
            }
        }
    });
    (tx, events)
}
