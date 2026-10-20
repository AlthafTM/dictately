use std::time::{Duration,Instant};
use windows_sys::Win32::{Foundation::*,System::Threading::*,UI::WindowsAndMessaging::*};

const PERIOD:Duration=Duration::from_nanos(16_666_667);

fn next_deadline(previous:Instant,now:Instant)->Instant {
    let next=previous+PERIOD;
    if next>now {next} else {
        let skipped=now.duration_since(next).as_nanos()/PERIOD.as_nanos()+1;
        next+Duration::from_nanos((skipped*PERIOD.as_nanos()) as u64)
    }
}

/// One-shot deadlines keep animation independent of coarse WM_TIMER polling.
/// No timer, thread, or frequent wakeup runs while the panel is hidden.
pub struct FrameClock {timer:HANDLE,deadline:Option<Instant>}
impl FrameClock {
    pub unsafe fn new()->Option<Self> {
        let mut timer=CreateWaitableTimerExW(std::ptr::null(),std::ptr::null(),CREATE_WAITABLE_TIMER_HIGH_RESOLUTION,TIMER_MODIFY_STATE|SYNCHRONIZATION_SYNCHRONIZE);
        if timer.is_null() {timer=CreateWaitableTimerExW(std::ptr::null(),std::ptr::null(),0,TIMER_MODIFY_STATE|SYNCHRONIZATION_SYNCHRONIZE);}
        if timer.is_null() {None}else{Some(Self{timer,deadline:None})}
    }
    pub unsafe fn wait(&mut self,visible:bool)->bool {
        if !visible {
            if self.deadline.take().is_some() {CancelWaitableTimer(self.timer);}
            MsgWaitForMultipleObjectsEx(0,std::ptr::null(),INFINITE,QS_ALLINPUT,MWMO_INPUTAVAILABLE);
            return false;
        }
        let now=Instant::now();let due=*self.deadline.get_or_insert(now+PERIOD);
        if due<=now {self.deadline=Some(next_deadline(due,now));return true;}
        let relative=-((due.duration_since(now).as_nanos()/100).max(1) as i64);
        if SetWaitableTimerEx(self.timer,&relative,0,None,std::ptr::null(),std::ptr::null(),0)==0 {
            // Continue servicing input even if Windows refuses a timer arm.
            MsgWaitForMultipleObjectsEx(0,std::ptr::null(),1,QS_ALLINPUT,MWMO_INPUTAVAILABLE);
            return false;
        }
        if MsgWaitForMultipleObjectsEx(1,&self.timer,INFINITE,QS_ALLINPUT,MWMO_INPUTAVAILABLE)==WAIT_OBJECT_0 {
            self.deadline=Some(next_deadline(due,Instant::now()));true
        }else{false}
    }
}
impl Drop for FrameClock {fn drop(&mut self){unsafe{CancelWaitableTimer(self.timer);CloseHandle(self.timer);}}}

#[cfg(test)] mod tests {
    use super::*;
    #[test] fn deadlines_keep_phase_without_catchup_frame_bursts() {
        let start=Instant::now();
        assert_eq!(next_deadline(start,start+Duration::from_millis(3)),start+PERIOD);
        assert_eq!(next_deadline(start,start+Duration::from_millis(40)),start+PERIOD*3);
        assert_eq!(next_deadline(start,start+PERIOD),start+PERIOD*2);
    }
}
