use windows::{core::Interface, Win32::{System::{Com::*, Variant::{VariantClear, VariantToBoolean, VT_DISPATCH, VT_I4, VT_BOOL, VARIANT}}, UI::Accessibility::*}};
use windows_sys::Win32::{Foundation::*, UI::{Input::KeyboardAndMouse::*, WindowsAndMessaging::*}};
use std::sync::atomic::{AtomicU64, Ordering};
static INPUT_REVISION: AtomicU64 = AtomicU64::new(0);
pub fn user_activity() {INPUT_REVISION.fetch_add(1,Ordering::Relaxed);}

#[windows::core::implement(IUIAutomationFocusChangedEventHandler)]
struct FocusListener;
impl IUIAutomationFocusChangedEventHandler_Impl for FocusListener_Impl {
    fn HandleFocusChangedEvent(&self, _sender: windows::core::Ref<IUIAutomationElement>) -> windows::core::Result<()> {Ok(())}
}
pub struct Origin {window: HWND, pid: u32, focus_window: HWND, revision: u64}

// A recording owns one destination. Never resolve a new destination from a late ASR result.
pub struct Destination { window: HWND, pid: u32, focus_window: HWND, element: IUIAutomationElement, native_edit:bool }
// HWND values are opaque identifiers; Origin contains no COM interfaces or borrowed data.
unsafe impl Send for Origin {}
pub struct Typer { automation: Option<IUIAutomation>, listener: Option<IUIAutomationFocusChangedEventHandler>, session:Option<(std::sync::Arc<AtomicU64>,u64)> }
impl Drop for Typer {
    fn drop(&mut self) {if let Some((a,h))=self.automation.as_ref().zip(self.listener.as_ref()) {unsafe {let _=a.RemoveFocusChangedEventHandler(h);}}}
}
impl Typer {
    fn trace(&self,stage:&str) {
        use std::io::Write;
        if let Some(path)=std::env::var_os("TRANSCRIBER_INPUT_TRACE") {
            if let Ok(mut file)=std::fs::OpenOptions::new().create(true).append(true).open(path) {let _=writeln!(file,"{stage}");}
        }
    }
    unsafe fn child_windows(window:HWND)->Vec<HWND> {
        unsafe extern "system" fn collect(hwnd:HWND,data:LPARAM)->i32 {
            (*(data as *mut Vec<HWND>)).push(hwnd);1
        }
        let mut children=Vec::new();EnumChildWindows(window,Some(collect),&mut children as *mut _ as LPARAM);children
    }
    fn current(&self)->bool {self.session.as_ref().map(|(active,id)|active.load(Ordering::Acquire)==*id).unwrap_or(true)}
    pub fn origin(expected: HWND) -> Option<Origin> {
        unsafe {
            let window=GetForegroundWindow(); let mut pid=0;
            GetWindowThreadProcessId(window,&mut pid);
            if window.is_null() || pid==std::process::id() || (!expected.is_null() && window!=expected) {return None;}
            Some(Origin{window,pid,focus_window:Self::focus_window(window)?,revision:INPUT_REVISION.load(Ordering::Relaxed)})
        }
    }
    pub fn resolve(&self, origin: &Origin) -> Option<Destination> {
        let unchanged=|| unsafe {
            let mut pid=0;GetWindowThreadProcessId(origin.window,&mut pid);
            GetForegroundWindow()==origin.window && pid==origin.pid && Self::focus_window(origin.window)==Some(origin.focus_window)
                && INPUT_REVISION.load(Ordering::Relaxed)==origin.revision
        };
        if !unchanged() {return None;}
        let target=self.capture(origin.window)?;
        if unchanged() {Some(target)} else {None}
    }
    pub fn same_destination(&self, a: &Destination, b: &Destination) -> bool {
        a.window == b.window && a.pid == b.pid && self.automation.as_ref().map(|automation| unsafe {
            automation.CompareElements(&a.element, &b.element).map(|v|v.as_bool()).unwrap_or(false)
        }).unwrap_or(false)
    }
    // Verify the suffix at the caret. Legacy native edits use a bounded control buffer.
    pub fn replace_suffix(&self, destination: &Destination, old: &str, replacement: &str) -> bool {
        self.trace("correction: validate input");
        unsafe {
            if old.is_empty() || old.chars().count()>1024 || !self.insert(destination, "") { return false; }
            self.trace("correction: input validated");
            let mut class=[0u16;64];let length=GetClassNameW(destination.focus_window,class.as_mut_ptr(),64);
            let class=String::from_utf16_lossy(&class[..length.max(0) as usize]).to_uppercase();
            // Multiline Win32 edit providers can block range-selection RPCs.
            // Their native selection messages are bounded and do not depend on COM.
            if class=="EDIT" || class.starts_with("RICHEDIT") {return self.replace_native_suffix(destination,old,replacement);}
            let Some(focused)=self.focused(destination.window, destination.focus_window) else {return false;};
            if GetForegroundWindow()!=destination.window || !self.automation.as_ref().unwrap().CompareElements(&destination.element,&focused).map(|b|b.as_bool()).unwrap_or(false) {return false;}
            let Ok(pattern)=focused.GetCurrentPatternAs::<IUIAutomationTextPattern>(UIA_TextPatternId) else {
                return self.replace_native_suffix(destination,old,replacement);
            };
            let Ok(selection)=pattern.GetSelection() else {return false;};
            if selection.Length().ok()!=Some(1) {return false;}
            let Ok(caret)=selection.GetElement(0) else {return false;};
            if caret.GetText(1).ok().map(|s|!s.is_empty()).unwrap_or(true) {return false;}
            let Ok(range)=caret.Clone() else {return false;};
            let count=old.chars().count() as i32;
            if range.MoveEndpointByUnit(TextPatternRangeEndpoint_Start,TextUnit_Character,-count).ok()!=Some(-count)
                || range.GetText(-1).ok().map(|s|s.to_string().replace('\u{00a0}'," "))!=Some(old.replace('\u{00a0}'," ")) {return false;}
            if GetForegroundWindow()!=destination.window || Self::focus_window(destination.window)!=Some(destination.focus_window) || !self.current() {return false;}
            if range.Select().is_err() {return false;}
            if self.insert(destination,replacement) {return true;}
            // Restore the caret if the insertion was blocked after selecting our range.
            if GetForegroundWindow()==destination.window && Self::focus_window(destination.window)==Some(destination.focus_window) {
                if let Some(current)=self.focused(destination.window,destination.focus_window) {
                    if self.automation.as_ref().unwrap().CompareElements(&destination.element,&current).map(|b|b.as_bool()).unwrap_or(false) {let _=caret.Select();}
                }
            }
            false
        }
    }
    unsafe fn replace_native_suffix(&self, destination: &Destination, old: &str, replacement: &str) -> bool {
        self.trace("native correction: get selection");
        let hwnd=destination.focus_window;
        let mut class=[0u16;64];
        let len=GetClassNameW(hwnd,class.as_mut_ptr(),class.len() as i32);
        let class=String::from_utf16_lossy(&class[..len.max(0) as usize]).to_uppercase();
        if class!="EDIT" && !class.starts_with("RICHEDIT") {return false;}
        let mut start=0u32; let mut end=0u32; let mut result=0usize;
        if SendMessageTimeoutW(hwnd,0xB0,&mut start as *mut _ as usize,&mut end as *mut _ as isize,SMTO_ABORTIFHUNG,200,&mut result)==0 || start!=end {return false;}
        self.trace("native correction: get text");
        if SendMessageTimeoutW(hwnd,WM_GETTEXTLENGTH,0,0,SMTO_ABORTIFHUNG,200,&mut result)==0 || result>65536 {return false;}
        let mut content=vec![0u16;result+1];
        if SendMessageTimeoutW(hwnd,WM_GETTEXT,content.len(),content.as_mut_ptr() as isize,SMTO_ABORTIFHUNG,200,&mut result)==0 {return false;}
        let old:Vec<u16>=old.encode_utf16().collect(); let end=end as usize;
        if end>result || end<old.len() || content[end-old.len()..end]!=old {return false;}
        self.trace("native correction: verify target again");
        if !self.insert(destination,"") {return false;}
        self.trace("native correction: select suffix");
        if SendMessageTimeoutW(hwnd,0xB1,end-old.len(),end as isize,SMTO_ABORTIFHUNG,200,&mut result)==0 {return false;}
        self.trace("native correction: insert replacement");
        if self.insert(destination,replacement) {return true;}
        if GetForegroundWindow()==destination.window && Self::focus_window(destination.window)==Some(hwnd) {
            SendMessageTimeoutW(hwnd,0xB1,end,end as isize,SMTO_ABORTIFHUNG,200,&mut result);
        }
        false
    }
    pub fn diagnostic(&self) -> String {
        unsafe {
            let element = self.automation.as_ref().and_then(|a| a.GetFocusedElement().ok());
            let mut info:GUITHREADINFO=std::mem::zeroed();info.cbSize=std::mem::size_of::<GUITHREADINFO>() as u32;
            GetGUIThreadInfo(GetWindowThreadProcessId(GetForegroundWindow(),std::ptr::null_mut()),&mut info);
            let classes:Vec<_>=Self::child_windows(GetForegroundWindow()).into_iter().take(40).map(|hwnd| {
                let mut class=[0u16;128];let len=GetClassNameW(hwnd,class.as_mut_ptr(),128);
                (hwnd as usize,String::from_utf16_lossy(&class[..len.max(0) as usize]))
            }).collect();
            format!("foreground={} focus_hwnd={} caret_hwnd={} flags={} listener={} focus_pid={:?} control={:?} native_children={:?}", GetForegroundWindow() as usize,
                info.hwndFocus as usize,info.hwndCaret as usize,info.flags,self.listener.is_some(),
                element.as_ref().and_then(|e| e.CurrentProcessId().ok()),
                element.as_ref().and_then(|e| e.CurrentControlType().ok()).map(|c| c.0),classes)
        }
    }
    pub fn new() -> Self {
        unsafe {
            let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
            let automation:Option<IUIAutomation>=CoCreateInstance(&CUIAutomation, None, CLSCTX_INPROC_SERVER).ok();
            // Keep the accessibility client attached so lazy browser/WebView trees
            // stay available. The callback reads/stores no field names or contents.
            let handler:IUIAutomationFocusChangedEventHandler=FocusListener.into();
            let listener=automation.as_ref().and_then(|a|a.AddFocusChangedEventHandler(None,&handler).ok().map(|_|handler));
            if let Some(extended)=automation.as_ref().and_then(|a|a.cast::<IUIAutomation2>().ok()) {
                let _=extended.SetConnectionTimeout(500);let _=extended.SetTransactionTimeout(500);
            }
            Self { automation, listener, session:None }
        }
    }
    unsafe fn focus_window(window: HWND) -> Option<HWND> {
        let thread = GetWindowThreadProcessId(window, std::ptr::null_mut());
        let mut info: GUITHREADINFO = std::mem::zeroed();
        info.cbSize = std::mem::size_of::<GUITHREADINFO>() as u32;
        if thread == 0 || GetGUIThreadInfo(thread, &mut info) == 0 || info.hwndFocus.is_null() { return None; }
        Some(info.hwndFocus)
    }
    unsafe fn belongs_to(&self, element: &IUIAutomationElement, window: HWND) -> bool {
        // WebView/Electron inputs can belong to a renderer PID different from their host.
        // Validate native window ancestry instead of rejecting cross-process providers.
        let Some(automation) = self.automation.as_ref() else { return false; };
        let Ok(walker) = automation.RawViewWalker() else { return false; };
        let mut current = element.clone();
        for _ in 0..64 {
            if let Ok(handle) = current.CurrentNativeWindowHandle() {
                let native = handle.0 as HWND;
                if !native.is_null() && GetAncestor(native, GA_ROOT) == window { return true; }
            }
            let Ok(parent) = walker.GetParentElement(&current) else { return false; };
            current = parent;
        }
        false
    }
    unsafe fn editable(element: &IUIAutomationElement) -> bool {
        if !element.CurrentIsEnabled().map(|b| b.as_bool()).unwrap_or(false)
            || element.CurrentIsPassword().map(|b| b.as_bool()).unwrap_or(true) { return false; }
        if let Ok(value) = element.GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId) {
            return value.CurrentIsReadOnly().map(|b| !b.as_bool()).unwrap_or(false);
        }
        // Contenteditable, RichEdit and WinUI controls do not all expose ValuePattern.
        if let Ok(pattern) = element.GetCurrentPatternAs::<IUIAutomationTextPattern>(UIA_TextPatternId) {
            if let Ok(range) = pattern.DocumentRange() {
                if let Ok(mut value) = range.GetAttributeValue(UIA_IsReadOnlyAttributeId) {
                    let read_only = VariantToBoolean(&value).ok().map(|b| b.as_bool());
                    let _ = VariantClear(&mut value);
                    if let Some(read_only) = read_only { return !read_only; }
                }
            }
        }
        element.CurrentControlType().ok() == Some(UIA_EditControlTypeId)
            || element.GetCurrentPatternAs::<IUIAutomationTextEditPattern>(UIA_TextEditPatternId).is_ok()
    }
    unsafe fn focused(&self, window: HWND, focus_window: HWND) -> Option<IUIAutomationElement> {
        let automation = self.automation.as_ref()?;
        if let Ok(element) = automation.GetFocusedElement() {
            if Self::editable(&element) && self.belongs_to(&element, window)
                && element.CurrentHasKeyboardFocus().map(|b| b.as_bool()).unwrap_or(false) { return Some(element); }
        }
        if let Some(element)=self.legacy_focused(window,focus_window) {return Some(element);}
        if focus_window!=window {if let Some(element)=self.legacy_focused(window,window) {return Some(element);}}
        // WinUI composition can keep Windows focus on an InputSite while the
        // actual web editor is exposed by a descendant Chromium render widget.
        for child in Self::child_windows(window) {
            let mut class=[0u16;96];let len=GetClassNameW(child,class.as_mut_ptr(),96);
            let class=String::from_utf16_lossy(&class[..len.max(0) as usize]);
            if class.contains("Chrome_RenderWidgetHost") || class.contains("Chrome_WidgetWin") {
                if let Some(element)=self.legacy_focused(window,child) {return Some(element);}
            }
        }
        // A native caret is a stronger location hint than a stale global focus
        // object. Resolve its containing editor without changing focus.
        let mut info:GUITHREADINFO=std::mem::zeroed();info.cbSize=std::mem::size_of::<GUITHREADINFO>() as u32;
        let thread=GetWindowThreadProcessId(window,std::ptr::null_mut());
        if GetGUIThreadInfo(thread,&mut info)!=0 && !info.hwndCaret.is_null() && info.flags & GUI_INMENUMODE==0 {
            let mut point=POINT{x:info.rcCaret.left+1,y:(info.rcCaret.top+info.rcCaret.bottom)/2};
            if windows_sys::Win32::Graphics::Gdi::ClientToScreen(info.hwndCaret,&mut point)!=0 {
                if let Ok(mut element)=automation.ElementFromPoint(windows::Win32::Foundation::POINT{x:point.x,y:point.y}) {
                    let walker=automation.RawViewWalker().ok()?;
                    for _ in 0..12 {
                        if Self::editable(&element) && self.belongs_to(&element,window) {return Some(element);}
                        let Ok(parent)=walker.GetParentElement(&element) else {break;};element=parent;
                    }
                }
            }
        }
        // Ask the keyboard-input subtree directly. This also wakes WebView's
        // accessibility provider when it initially exposes only its host pane.
        let mut value=VARIANT::default();
        (*value.Anonymous.Anonymous).vt=VT_BOOL;
        (*value.Anonymous.Anonymous).Anonymous.boolVal=windows::Win32::Foundation::VARIANT_BOOL(-1);
        let condition=automation.CreatePropertyCondition(UIA_HasKeyboardFocusPropertyId,&value).ok()?;
        for native in [focus_window,window] {
            if let Ok(root)=automation.ElementFromHandle(windows::Win32::Foundation::HWND(native)) {
                if let Ok(elements)=root.FindAll(TreeScope_Subtree,&condition) {
                    for i in 0..elements.Length().unwrap_or(0).min(16) {
                        if let Ok(element)=elements.GetElement(i) {
                            if Self::editable(&element) && self.belongs_to(&element,window) {return Some(element);}
                        }
                    }
                }
            }
        }
        None
    }
    unsafe fn legacy_focused(&self, window: HWND, focus_window: HWND) -> Option<IUIAutomationElement> {
        let automation=self.automation.as_ref()?;
        // Firefox/Zen's UIA bridge can report a menu while the real DOM editor
        // has focus. Resolve MSAA focus from the actual keyboard input window.
        let mut raw = std::ptr::null_mut();
        AccessibleObjectFromWindow(windows::Win32::Foundation::HWND(focus_window), 0xFFFFFFFC,
            &IAccessible::IID, &mut raw).ok()?;
        if raw.is_null() { return None; }
        let mut accessible = IAccessible::from_raw(raw);
        for _ in 0..16 {
            let mut focus = accessible.accFocus().ok()?;
            let inner = &focus.Anonymous.Anonymous;
            let next = if inner.vt == VT_DISPATCH {
                inner.Anonymous.pdispVal.as_ref().and_then(|dispatch| dispatch.cast::<IAccessible>().ok())
            } else { None };
            let child = if inner.vt == VT_I4 { Some(inner.Anonymous.lVal) } else { None };
            let _ = VariantClear(&mut focus);
            if let Some(next) = next { accessible = next; continue; }
            let element = automation.ElementFromIAccessible(&accessible, child?).ok()?;
            return if Self::editable(&element) && self.belongs_to(&element, window) { Some(element) } else { None };
        }
        None
    }
    pub fn capture(&self, expected: HWND) -> Option<Destination> {
        unsafe {
            let window = GetForegroundWindow();
            if window.is_null() || (!expected.is_null() && window != expected) { return None; }
            let mut pid = 0;
            GetWindowThreadProcessId(window, &mut pid);
            if pid == std::process::id() { return None; }
            let focus_window = Self::focus_window(window)?;
            let element = self.focused(window, focus_window)?;
            if !self.belongs_to(&element, window) || !Self::editable(&element) { return None; }
            if GetForegroundWindow() != window || Self::focus_window(window)? != focus_window { return None; }
            let mut class=[0u16;64];let length=GetClassNameW(focus_window,class.as_mut_ptr(),64);
            let class=String::from_utf16_lossy(&class[..length.max(0) as usize]).to_uppercase();
            let native_edit=(class=="EDIT" || class.starts_with("RICHEDIT"))
                && element.CurrentNativeWindowHandle().map(|h|h.0==focus_window).unwrap_or(false);
            Some(Destination { window, pid, focus_window, element, native_edit })
        }
    }
    pub fn insert(&self, destination: &Destination, text: &str) -> bool {
        self.trace("insert: verify native focus");
        if !self.current() {return false;}
        unsafe {
            for key in [VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN] {
                if GetAsyncKeyState(key as i32) < 0 { return false; }
            }
            let expected = destination.window;
            if GetForegroundWindow() != expected { return false; }
            let mut pid = 0;
            GetWindowThreadProcessId(expected, &mut pid);
            if pid != destination.pid || Self::focus_window(expected) != Some(destination.focus_window) { return false; }
            if destination.native_edit {
                // This control was verified at capture. Native focus identifies a
                // specific edit HWND, so no global accessibility re-query is needed.
                let style=GetWindowLongPtrW(destination.focus_window,GWL_STYLE) as u32;
                if style & (0x20|0x800|WS_DISABLED)!=0 {return false;} // ES_PASSWORD / ES_READONLY
            } else {
                let Some(automation) = self.automation.as_ref() else { return false; };
                self.trace("insert: resolve focused editor");
                let Some(focused) = self.focused(expected, destination.focus_window) else { return false; };
                self.trace("insert: compare editor identity");
                if !automation.CompareElements(&destination.element, &focused).map(|b| b.as_bool()).unwrap_or(false)
                    || !Self::editable(&focused) { return false; }
            }
            if text.is_empty() { return self.current(); }
            self.trace("insert: send unicode input");
            let mut inputs = Vec::with_capacity(text.len() * 2);
            for unit in text.encode_utf16() {
                if unit < 32 { continue; } // Never submit a message or synthesize shortcuts.
                for flags in [KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP] {
                    inputs.push(INPUT { r#type: INPUT_KEYBOARD, Anonymous: INPUT_0 { ki: KEYBDINPUT {
                        wVk: 0, wScan: unit, dwFlags: flags, time: 0, dwExtraInfo: 0x4C464C4F,
                    } } });
                }
            }
            // Recheck after potentially slow provider calls. Do not activate or refocus windows.
            if GetForegroundWindow() != expected || Self::focus_window(expected) != Some(destination.focus_window)
                || !self.current() { return false; }
            SendInput(inputs.len() as u32, inputs.as_ptr(), std::mem::size_of::<INPUT>() as i32) == inputs.len() as u32
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{io::{BufRead, BufReader, Write}, process::{Command, Stdio}, time::Duration};
    #[test]
    #[ignore = "Requires an unlocked interactive Windows desktop"]
    fn recording_destination_survives_late_results_without_retargeting() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
        let mut child = Command::new(root.join(".venv/Scripts/python.exe"))
            .arg(root.join("tests/typing_fixture.py")).stdin(Stdio::piped()).stdout(Stdio::piped()).spawn().unwrap();
        let mut input = child.stdin.take().unwrap();
        let mut output = BufReader::new(child.stdout.take().unwrap());
        fn receive(output: &mut impl BufRead) -> serde_json::Value {
            let mut line = String::new(); output.read_line(&mut line).unwrap(); serde_json::from_str(&line).unwrap()
        }
        fn command(input: &mut impl Write, output: &mut impl BufRead, cmd: &str) -> serde_json::Value {
            writeln!(input, "{cmd}").unwrap(); input.flush().unwrap();
            std::thread::sleep(Duration::from_millis(100)); receive(output)
        }
        let windows = receive(&mut output);
        let window = windows["window"].as_u64().unwrap() as HWND;
        let other = windows["other"].as_u64().unwrap() as HWND;
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let mut typer = Typer::new();
            assert_eq!(command(&mut input, &mut output, "focus 0")["focused"], true);
            let destination = typer.capture(window).unwrap_or_else(|| panic!("initial editable destination: {}", typer.diagnostic()));
            let delayed=Typer::origin(window).unwrap();
            assert!(typer.resolve(&delayed).is_some(),"unchanged origin can resolve after provider initialization");
            user_activity();
            assert!(typer.resolve(&delayed).is_none(),"deferred resolution must not bind after another click or key");
            assert!(typer.capture(other).is_none(), "F12 window mismatch must fail closed");
            assert!(typer.insert(&destination, "hello"));
            assert_eq!(command(&mut input, &mut output, "focus 1")["focused"], true);
            assert!(!typer.insert(&destination, " WRONG FIELD"));
            assert_eq!(command(&mut input, &mut output, "focus 2")["focused"], true);
            assert!(!typer.insert(&destination, " WRONG APP"));
            assert_eq!(command(&mut input, &mut output, "read"), serde_json::json!(["Existing: hello", "Existing: ", "Other: "]));
            assert_eq!(command(&mut input, &mut output, "focus 0")["focused"], true);
            assert!(typer.insert(&destination, " resumed"));
            assert_eq!(command(&mut input, &mut output, "read"), serde_json::json!(["Existing: hello resumed", "Existing: ", "Other: "]));
            assert!(!typer.replace_suffix(&destination,"different text","must not erase"));
            assert!(typer.replace_suffix(&destination,"hello resumed","hello corrected"));
            assert_eq!(command(&mut input, &mut output, "read"), serde_json::json!(["Existing: hello corrected", "Existing: ", "Other: "]));
            let active=std::sync::Arc::new(AtomicU64::new(10));
            typer.session=Some((active.clone(),10));active.store(11,Ordering::Release);
            assert!(!typer.insert(&destination," obsolete session"),"late work must not type after cancellation");
            command(&mut input, &mut output, "home");
            assert!(!typer.replace_suffix(&destination,"hello corrected","must not erase at moved caret"));
            assert_eq!(command(&mut input, &mut output, "read"), serde_json::json!(["Existing: hello corrected", "Existing: ", "Other: "]));
        }));
        writeln!(input, "quit").ok(); input.flush().ok(); child.wait().ok();
        if let Err(error) = result { std::panic::resume_unwind(error); }
    }
}

pub enum InputEvent {
    Captured{session:u64,locked:bool,same:bool,diagnostic:String},
    Delivered{session:u64,text:String,success:bool,corrected:bool,locked:bool},
}
enum InputRequest {
    Capture{session:u64,origin:Option<Origin>},
    Deliver{session:u64,text:String,correction:Option<(String,String)>},
}
pub struct InputWorker {
    requests:std::sync::mpsc::Sender<InputRequest>,
    events:std::sync::mpsc::Receiver<InputEvent>,
    active:std::sync::Arc<AtomicU64>,
}
impl InputWorker {
    pub fn new()->Self {
        let (tx,requests)=std::sync::mpsc::channel();let (events,rx)=std::sync::mpsc::channel();
        let active=std::sync::Arc::new(AtomicU64::new(0));let current=active.clone();
        std::thread::spawn(move|| {
            let mut typer=Typer::new();let mut destination=None;let mut origin=None;
            while let Ok(request)=requests.recv() {
                let session=match &request {InputRequest::Capture{session,..}|InputRequest::Deliver{session,..}=>*session};
                if current.load(Ordering::Acquire)!=session {continue;}
                typer.session=Some((current.clone(),session));
                match request {
                    InputRequest::Capture{origin:requested,..}=> {
                        origin=requested;
                        let next=origin.as_ref().and_then(|o|typer.resolve(o));
                        let same=destination.as_ref().zip(next.as_ref()).map(|(a,b)|typer.same_destination(a,b)).unwrap_or(false);
                        destination=next;let locked=destination.is_some();
                        let diagnostic=if locked {String::new()} else {typer.diagnostic()};
                        let _=events.send(InputEvent::Captured{session,locked,same,diagnostic});
                    }
                    InputRequest::Deliver{text,correction,..}=> {
                        if destination.is_none() {destination=origin.as_ref().and_then(|o|typer.resolve(o));}
                        let corrected=destination.as_ref().zip(correction.as_ref()).map(|(target,(old,new))|typer.replace_suffix(target,old,new)).unwrap_or(false);
                        let success=corrected || destination.as_ref().map(|target|typer.insert(target,&text)).unwrap_or(false);
                        let _=events.send(InputEvent::Delivered{session,text,success,corrected,locked:destination.is_some()});
                    }
                }
            }
        });
        Self{requests:tx,events:rx,active}
    }
    pub fn begin(&self,session:u64,expected:HWND)->bool {
        let origin=Typer::origin(expected);let valid=origin.is_some();
        self.invalidate(session);
        let _=self.requests.send(InputRequest::Capture{session,origin});valid
    }
    pub fn invalidate(&self,session:u64) {self.active.store(session,Ordering::Release);}
    pub fn is_active(&self,session:u64)->bool {self.active.load(Ordering::Acquire)==session}
    pub fn deliver(&self,session:u64,text:String,correction:Option<(String,String)>)->bool {
        self.requests.send(InputRequest::Deliver{session,text,correction}).is_ok()
    }
    pub fn poll(&self)->Option<InputEvent> {self.events.try_recv().ok()}
}
