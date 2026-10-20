use std::{collections::VecDeque, mem::zeroed, time::Instant};
use windows_sys::Win32::{Foundation::*, Graphics::Gdi::*, UI::{HiDpi::GetDpiForWindow, WindowsAndMessaging::*}};

pub fn wide(s: &str) -> Vec<u16> { s.encode_utf16().chain(Some(0)).collect() }
pub const WIDTH: i32 = 136;
pub const HEIGHT: i32 = 36;
const SS: i32 = 2;
const PROFILE: usize = 1024;

struct Edge { index: usize, position: f32, strength: f32 }
pub struct Display {
    pub hwnd: HWND,
    pub title: String,
    pub subtitle: String,
    pub preview: String,
    pub recording: bool,
    pub language: String,
    pub wave: VecDeque<f32>,
    visible: bool,
    closing: bool,
    animation: Instant,
    cue_at: Instant,
    end_started: Option<Instant>,
    origin: (i32, i32),
    dpi: u32,
    width: i32,
    height: i32,
    rw: i32,
    rh: i32,
    sample_scale: f32,
    dc: HDC,
    bitmap: HBITMAP,
    old_bitmap: HGDIOBJ,
    pixels: *mut u32,
    frame: Vec<u32>,
    background: Vec<u32>,
    body_shades: Vec<f32>,
    rim_shades: Vec<f32>,
    edges: Vec<Edge>,
    shine: [f32; PROFILE],
    levels: [f32; 20],
    last_frame: Instant,
    preview_path: Option<std::path::PathBuf>,
    last_preview: Instant,
    frame_dir: Option<std::path::PathBuf>,
    frame_index: usize,
}
fn smooth(v: f32) -> f32 { let v=v.clamp(0.,1.); v*v*(3.-2.*v) }
fn capsule_distance(x: f32, y: f32, hw: f32, hh: f32, radius: f32) -> f32 {
    let qx=x.abs()-(hw-radius); let qy=y.abs()-(hh-radius);
    (qx.max(0.).powi(2)+qy.max(0.).powi(2)).sqrt()+qx.max(qy).min(0.)-radius
}
fn premultiplied(shade: f32, alpha: f32) -> u32 {
    let a=(alpha.clamp(0.,1.)*255.).round() as u32;
    let c=(shade.clamp(0.,255.)*alpha.clamp(0.,1.)).round() as u32;
    c|c<<8|c<<16|a<<24
}
fn over(destination: u32, shade: f32, alpha: f32) -> u32 {
    let a=alpha+(destination>>24) as f32/255.*(1.-alpha);
    let c=(shade*alpha+(destination&255) as f32*(1.-alpha)).round().clamp(0.,255.) as u32;
    c|c<<8|c<<16|((a*255.).round().clamp(0.,255.) as u32)<<24
}
// Position follows the capsule perimeter, rather than sliding across its fill.
fn perimeter(x: f32,y: f32,hw: f32,r: f32) -> f32 {
    let side=(hw-r).max(0.); let straight=2.*side; let pi=std::f32::consts::PI;
    let distance=if x>side { straight+(y.atan2(x-side)+pi/2.)*r }
        else if x < -side { let mut angle=y.atan2(x+side); if angle<0. { angle+=2.*pi; }
            2.*straight+pi*r+(angle-pi/2.)*r }
        else if y<0. { x+side } else { straight+pi*r+side-x };
    distance/(2.*straight+2.*pi*r).max(1.)
}
fn edge_strength(distance: f32) -> f32 {
    let rim=(-(distance/0.65).powi(2)).exp()*112.;
    let inside=if distance<0. { (distance/1.25).exp()*18. } else { 0. };
    rim+inside
}
impl Display {
    pub unsafe fn new(hwnd: HWND) -> Self {
        let screen=GetDC(std::ptr::null_mut()); let dc=CreateCompatibleDC(screen); ReleaseDC(std::ptr::null_mut(),screen);
        let now=Instant::now();
        let mut result=Self { hwnd,title:String::new(),subtitle:String::new(),preview:String::new(),recording:false,
            language:"EN".into(),wave:VecDeque::from(vec![0.;20]),visible:false,closing:false,animation:now,cue_at:now,
            end_started:None,origin:(0,0),dpi:0,width:0,height:0,rw:0,rh:0,sample_scale:2.,dc,
            bitmap:std::ptr::null_mut(),old_bitmap:std::ptr::null_mut(),pixels:std::ptr::null_mut(),frame:Vec::new(),
            background:Vec::new(),body_shades:Vec::new(),rim_shades:Vec::new(),edges:Vec::new(),shine:[0.;PROFILE],
            levels:[0.;20],last_frame:now,preview_path:std::env::var_os("LOCALFLOW_PREVIEW_DIR")
                .map(|p|std::path::PathBuf::from(p).join("glass-frame.bgra")),last_preview:now,
            frame_dir:std::env::var_os("TRANSCRIBER_ANIMATION_FRAMES").map(std::path::PathBuf::from),frame_index:0 };
        for (i,value) in result.shine.iter_mut().enumerate() {
            let d=(i as f32/PROFILE as f32).min(1.-i as f32/PROFILE as f32);
            *value=(-(d/0.055).powi(2)).exp();
        }
        result.configure(GetDpiForWindow(hwnd).max(96)); result
    }
    unsafe fn configure(&mut self,dpi:u32) {
        if self.dpi==dpi { return; }
        self.dpi=dpi; let scale=dpi as f32/96.;
        self.width=(WIDTH as f32*scale).round() as i32; self.height=(HEIGHT as f32*scale).round() as i32;
        self.rw=self.width*SS; self.rh=self.height*SS; self.sample_scale=self.rw as f32/WIDTH as f32;
        if !self.bitmap.is_null() { SelectObject(self.dc,self.old_bitmap); DeleteObject(self.bitmap); }
        let mut info:BITMAPINFO=zeroed(); info.bmiHeader.biSize=std::mem::size_of::<BITMAPINFOHEADER>() as u32;
        info.bmiHeader.biWidth=self.width; info.bmiHeader.biHeight=-self.height;
        info.bmiHeader.biPlanes=1; info.bmiHeader.biBitCount=32;
        let mut bits=std::ptr::null_mut();
        self.bitmap=CreateDIBSection(self.dc,&info,DIB_RGB_COLORS,&mut bits,std::ptr::null_mut(),0);
        self.old_bitmap=SelectObject(self.dc,self.bitmap); self.pixels=bits as _;
        // Alpha supplies the rounded shape at every animation width; no fixed region to blur/clip it.
        SetWindowRgn(self.hwnd,std::ptr::null_mut(),0);
        let count=(self.rw*self.rh) as usize;
        self.frame=vec![0;count]; self.background=vec![0;count]; self.body_shades=vec![0.;count];
        self.rim_shades=vec![0.;count]; self.edges.clear();
        let hw=WIDTH as f32/2.-1.; let hh=HEIGHT as f32/2.-1.;
        for y in 0..self.rh { for x in 0..self.rw {
            let px=(x as f32+0.5)/self.sample_scale; let py=(y as f32+0.5)/self.sample_scale;
            let nx=(px/WIDTH as f32-0.5)*2.; let ny=py/HEIGHT as f32;
            let distance=capsule_distance(px-WIDTH as f32/2.,py-HEIGHT as f32/2.,hw,hh,hh);
            let sheen=(-((nx+0.4)/0.9).powi(2)-((ny-0.1)/0.29).powi(2)).exp();
            let lower=(-((nx-0.65)/0.75).powi(2)-((ny-0.91)/0.2).powi(2)).exp();
            let base=16.+(1.-ny)*9.+sheen*28.+lower*12.; let highlight=93.+(1.-ny)*92.;
            let index=(y*self.rw+x) as usize;
            self.body_shades[index]=base; self.rim_shades[index]=highlight;
            self.background[index]=self.body_pixel(index,distance);
            if distance.abs()<4.5 { self.edges.push(Edge { index,position:perimeter(px-WIDTH as f32/2.,py-HEIGHT as f32/2.,hw,hh),strength:edge_strength(distance) }); }
        } }
        self.write_dimensions();
    }
    fn body_pixel(&self,index:usize,distance:f32)->u32 {
        let mask=(0.5-distance*self.sample_scale).clamp(0.,1.);
        let rim=(1.0-distance.abs()).clamp(0.,1.);
        let shade=self.body_shades[index]*(1.-rim*0.65)+self.rim_shades[index]*rim*0.65;
        premultiplied(shade,(0.82+rim*0.14)*mask)
    }
    fn write_dimensions(&self) {
        let value=format!("{{\"width\":{},\"height\":{},\"logical_width\":{},\"logical_height\":{},\"supersampling\":2,\"dpi\":{}}}",self.width,self.height,WIDTH,HEIGHT,self.dpi);
        if let Some(path)=&self.preview_path { let _=std::fs::write(path.with_extension("json"),&value); }
        if let Some(dir)=&self.frame_dir { let _=std::fs::create_dir_all(dir); let _=std::fs::write(dir.join("dimensions.json"),value); }
    }
    pub unsafe fn cue(&mut self,_text:&str) {
        if self.recording { self.cue_at=Instant::now();self.end_started=None;self.levels.fill(0.); }
        else if self.end_started.is_none() { let now=Instant::now();self.cue_at=now;self.end_started=Some(now); }
    }
    pub unsafe fn show(&mut self,title:&str,subtitle:&str) {
        self.title=title.into();self.subtitle=subtitle.into();
        let monitor=MonitorFromWindow(GetForegroundWindow(),MONITOR_DEFAULTTONEAREST);
        let mut info:MONITORINFO=zeroed();info.cbSize=std::mem::size_of::<MONITORINFO>() as u32;
        if GetMonitorInfoW(monitor,&mut info)==0 { return; }
        let work=info.rcWork;
        // Move while hidden first so GetDpiForWindow reports the destination monitor.
        if !self.visible { SetWindowPos(self.hwnd,std::ptr::null_mut(),work.left+(work.right-work.left)/2,work.bottom-80,0,0,SWP_NOSIZE|SWP_NOZORDER|SWP_NOACTIVATE); }
        self.configure(GetDpiForWindow(self.hwnd).max(96));
        self.origin=(work.left+(work.right-work.left-self.width)/2,work.bottom-self.height-(22.*self.dpi as f32/96.).round() as i32);
        if !self.visible||self.closing { self.animation=Instant::now();self.last_frame=Instant::now();self.visible=true;self.closing=false; }
        self.animate();
    }
    pub unsafe fn redraw(&self) {}
    pub unsafe fn hide(&mut self) { if self.visible&&!self.closing { self.closing=true;self.animation=Instant::now(); } }
    pub fn is_visible(&self)->bool { self.visible }
    pub unsafe fn animate(&mut self) {
        if !self.visible||self.pixels.is_null() { return; }
        let now=Instant::now();let dt=now.duration_since(self.last_frame).as_secs_f32().min(0.05);self.last_frame=now;
        let age=self.animation.elapsed().as_secs_f32();let t=(age/if self.closing {0.34}else{0.42}).min(1.);
        if self.closing&&t>=1. { ShowWindow(self.hwnd,SW_HIDE);self.visible=false;return; }
        let progress=if self.closing {1.-smooth(t)}else{1.-(1.-t).powi(3)};
        let cue_age=self.cue_at.elapsed().as_secs_f32();
        let phase=(cue_age*0.36).fract();
        let profile=|position:f32| { let d=position-phase; let d=if d<0. {d+1.}else{d};self.shine[((d*PROFILE as f32) as usize).min(PROFILE-1)] };
        let hw=15.+(WIDTH as f32/2.-16.)*progress;let hh=HEIGHT as f32/2.-1.;let radius=hw.min(hh);
        if progress<0.999 {
            for y in 0..self.rh { for x in 0..self.rw {
                let px=(x as f32+0.5)/self.sample_scale-WIDTH as f32/2.;let py=(y as f32+0.5)/self.sample_scale-HEIGHT as f32/2.;
                let distance=capsule_distance(px,py,hw,hh,radius);let index=(y*self.rw+x) as usize;
                let mut pixel=self.body_pixel(index,distance);
                if distance.abs()<4.5 && pixel>>24>0 {
                    let alpha=(pixel>>24) as f32/255.;let shade=(pixel&255) as f32/alpha;
                    pixel=premultiplied(shade+profile(perimeter(px,py,hw,radius))*edge_strength(distance),alpha);
                }
                self.frame[index]=pixel;
            } }
        } else {
            self.frame.copy_from_slice(&self.background);
            for edge in &self.edges {
                let pixel=self.frame[edge.index];let alpha=(pixel>>24) as f32/255.;
                if alpha>0. { self.frame[edge.index]=premultiplied((pixel&255) as f32/alpha+profile(edge.position)*edge.strength,alpha); }
            }
        }
        let ending=self.end_started.map(|at|smooth(at.elapsed().as_secs_f32()/0.55)).unwrap_or(0.);
        let spread=(0.12+0.88*progress)*(1.-ending*0.5);
        // Smoothstep has zero slope at both ends; exponential settling continues
        // across the handoff instead of switching between max(intro, microphone).
        let handoff=smooth((cue_age-0.12)/0.85);
        let settle=1.-(-dt/0.085).exp();
        for i in 0..20 {
            let center_distance=((i as f32-9.5)/9.5).abs();let reveal=smooth((progress-center_distance*0.42)/0.58);
            let ripple=(cue_age*6.-center_distance*3.).sin()*0.5+0.5;
            let intro=0.20+0.28*ripple;
            let target=if self.recording {intro*(1.-handoff)+self.wave[i]*handoff}else{(0.18+0.22*ripple)*(1.-ending*0.65)};
            self.levels[i]+=(target-self.levels[i])*settle;
            let height=((3.+self.levels[i]*16.)*(0.86+progress*0.14)*(0.25+reveal*0.75)).max(1.8);
            let cx=WIDTH as f32/2.+(i as f32-9.5)*4.6*spread;let cy=HEIGHT as f32/2.;let r=1.3*(1.-ending*0.2);
            let min_x=((cx-r-1.)*self.sample_scale).floor().max(0.) as i32;
            let max_x=((cx+r+1.)*self.sample_scale).ceil().min(self.rw as f32) as i32;
            let min_y=((cy-height/2.-1.)*self.sample_scale).floor().max(0.) as i32;
            let max_y=((cy+height/2.+1.)*self.sample_scale).ceil().min(self.rh as f32) as i32;
            for y in min_y..max_y { for x in min_x..max_x {
                let distance=capsule_distance((x as f32+0.5)/self.sample_scale-cx,(y as f32+0.5)/self.sample_scale-cy,r,height/2.,r.min(height/2.));
                let alpha=(0.5-distance*self.sample_scale).clamp(0.,1.);
                let vertical=(y-min_y) as f32/(max_y-min_y).max(1) as f32;
                let shade=174.+(1.-center_distance)*53.+(1.-vertical)*24.;let index=(y*self.rw+x) as usize;
                let body_alpha=(self.frame[index]>>24) as f32/255.;let fade=1.-ending*smooth((center_distance-0.2)/0.7);
                self.frame[index]=over(self.frame[index],shade,alpha*0.98*reveal*fade*body_alpha);
            } }
        }
        // Average premultiplied samples before compositing, preserving clean alpha edges.
        let output=std::slice::from_raw_parts_mut(self.pixels,(self.width*self.height) as usize);
        for y in 0..self.height {for x in 0..self.width {
            let start=(y*SS*self.rw+x*SS) as usize;
            let samples=[self.frame[start],self.frame[start+1],self.frame[start+self.rw as usize],self.frame[start+self.rw as usize+1]];
            let a=samples.iter().map(|p|p>>24).sum::<u32>();let c=samples.iter().map(|p|p&255).sum::<u32>();
            let a=(a+2)/4;let c=(c+2)/4;output[(y*self.width+x) as usize]=c|c<<8|c<<16|a<<24;
        }}
        let destination=POINT{x:self.origin.0,y:self.origin.1+((1.-progress)*12.*self.dpi as f32/96.) as i32};
        let size=SIZE{cx:self.width,cy:self.height};let source=POINT{x:0,y:0};
        let blend=BLENDFUNCTION{BlendOp:AC_SRC_OVER as u8,BlendFlags:0,SourceConstantAlpha:(progress*255.) as u8,AlphaFormat:AC_SRC_ALPHA as u8};
        UpdateLayeredWindow(self.hwnd,std::ptr::null_mut(),&destination,&size,self.dc,&source,0,&blend,ULW_ALPHA);
        if age<0.05 { SetWindowPos(self.hwnd,HWND_TOPMOST,0,0,0,0,SWP_NOACTIVATE|SWP_SHOWWINDOW|SWP_NOMOVE|SWP_NOSIZE); }
        if let Some(dir)=&self.frame_dir {
            let stage=if self.closing {"close"}else if self.recording&&t<1. {"open"}else if !self.recording {"ending"}else{"recording"};
            let faded:Vec<u32>=output.iter().map(|p|{let a=((p>>24) as f32*progress).round() as u32;let c=((p&255) as f32*progress).round() as u32;c|c<<8|c<<16|a<<24}).collect();
            let _=std::fs::write(dir.join(format!("{:04}-{stage}.bgra",self.frame_index)),std::slice::from_raw_parts(faded.as_ptr() as *const u8,faded.len()*4));self.frame_index+=1;
        }
        if self.last_preview.elapsed().as_secs_f32()>0.15 {
            if let Some(path)=&self.preview_path {let _=std::fs::write(path,std::slice::from_raw_parts(self.pixels as *const u8,output.len()*4));}
            self.last_preview=now;
        }
    }
    pub fn amplitude(&mut self,level:f32) {let target=((level*45.).ln_1p()/2.674).clamp(0.,1.);let before=self.wave.back().copied().unwrap_or(0.);self.wave.pop_front();self.wave.push_back(before*0.55+target*0.45);}
    pub unsafe fn paint(&self) {let mut ps:PAINTSTRUCT=zeroed();BeginPaint(self.hwnd,&mut ps);EndPaint(self.hwnd,&ps);}
}
impl Drop for Display {fn drop(&mut self){unsafe{SelectObject(self.dc,self.old_bitmap);DeleteObject(self.bitmap);DeleteDC(self.dc);}}}
