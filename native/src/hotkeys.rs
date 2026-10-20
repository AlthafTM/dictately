use std::sync::atomic::{AtomicU32, Ordering};
use windows_sys::Win32::{Foundation::CloseHandle,System::Threading::{OpenEventW,WaitForSingleObject},UI::Input::KeyboardAndMouse::*};
use crate::display::wide;

pub static DICTATION:AtomicU32=AtomicU32::new(0x7b);
pub static LANGUAGE:AtomicU32=AtomicU32::new(0x7a);
static HELD:AtomicU32=AtomicU32::new(0);

pub fn valid(value:u32)->bool {
    let key=value&255;
    value<=0xfff && (8..=254).contains(&key) && !matches!(key,16..=18|27|91|92|160..=165)
}
pub fn pair(dictation:u32,language:u32)->bool {valid(dictation)&&valid(language)&&dictation!=language}
pub fn modifier(key:u32,down:bool)->bool {
    let bit=match key {160=>1,161=>2,162=>4,163=>8,164=>16,165=>32,91=>64,92=>128,_=>return false};
    if down {HELD.fetch_or(bit,Ordering::Relaxed);} else {HELD.fetch_and(!bit,Ordering::Relaxed);}
    true
}
pub fn modifiers()->u32 {
    let held=HELD.load(Ordering::Relaxed);
    (if held&12!=0 {1}else{0}) | (if held&3!=0 {2}else{0}) | (if held&48!=0 {4}else{0}) | (if held&192!=0 {8}else{0})
}
pub unsafe fn initialize() {
    for key in [160,161,162,163,164,165,91,92] {modifier(key,GetAsyncKeyState(key as i32)<0);}
}
pub unsafe fn capture_active()->bool {
    let handle=OpenEventW(0x00100000,0,wide("Local\\Transcribr.ShortcutCapture").as_ptr());
    if handle.is_null() {return false;}
    let active=WaitForSingleObject(handle,0)==0;CloseHandle(handle);active
}
pub fn name(value:u32)->String {
    let mut parts=Vec::new();
    for (bit,title) in [(1,"Ctrl"),(2,"Shift"),(4,"Alt"),(8,"Win")] {if value>>8&bit!=0 {parts.push(title.to_string());}}
    let key=value&255;
    parts.push(if (112..=135).contains(&key) {format!("F{}",key-111)}
        else if (48..=90).contains(&key) {(key as u8 as char).to_string()}
        else {match key {8=>"Backspace",9=>"Tab",13=>"Enter",32=>"Space",33=>"Page Up",34=>"Page Down",35=>"End",36=>"Home",37=>"Left",38=>"Up",39=>"Right",40=>"Down",45=>"Insert",46=>"Delete",_=>"Key"}.into()});
    parts.join("+")
}

#[cfg(test)] mod tests {
    use super::*;
    #[test] fn validates_pairs_and_names() {
        assert!(pair(123,122));assert!(pair(0x141,0x242));
        for value in [0,27,16,162,0x1000] {assert!(!valid(value));}
        assert!(!pair(123,123));assert_eq!(name(0x341),"Ctrl+Shift+A");
    }
}
