use std::time::{Duration,Instant};
use windows_sys::Win32::{Foundation::*,UI::WindowsAndMessaging::*};

pub struct Watch {pub launched:Instant,seen:bool,hidden_since:Option<Instant>}
impl Watch {
    pub fn new(now:Instant)->Self {Self{launched:now,seen:false,hidden_since:None}}
    pub fn complete(&mut self,now:Instant,visible:bool)->bool {
        if visible {self.seen=true;self.hidden_since=None;return false;}
        if !self.seen {return now.duration_since(self.launched)>=Duration::from_secs(10);}
        now.duration_since(*self.hidden_since.get_or_insert(now))>=Duration::from_millis(250)
    }
}
pub unsafe fn selection_visible()->bool {
    unsafe extern "system" fn visit(hwnd:HWND,data:LPARAM)->i32 {
        if IsWindowVisible(hwnd)!=0 {
            let mut class=[0u16;80];let count=GetClassNameW(hwnd,class.as_mut_ptr(),80);
            let class=String::from_utf16_lossy(&class[..count.max(0) as usize]);
            if matches!(class.as_str(),"SnipOverlayRootWindow"|"ScreenClippingHost") {*(data as *mut bool)=true;return 0;}
        }
        1
    }
    let mut visible=false;EnumWindows(Some(visit),&mut visible as *mut bool as LPARAM);visible
}
#[cfg(test)]mod tests {
    use super::*;
    #[test]fn waits_for_selection_then_a_stable_close_and_handles_failed_launch() {
        let now=Instant::now();let mut watch=Watch::new(now);
        assert!(!watch.complete(now+Duration::from_secs(2),false));
        assert!(!watch.complete(now+Duration::from_secs(3),true));
        assert!(!watch.complete(now+Duration::from_secs(4),false));
        assert!(!watch.complete(now+Duration::from_millis(4100),true));
        assert!(!watch.complete(now+Duration::from_secs(5),false));
        assert!(watch.complete(now+Duration::from_millis(5250),false));
        assert!(Watch::new(now).complete(now+Duration::from_secs(10),false));
    }
}
