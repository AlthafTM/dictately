use std::{collections::VecDeque, mem::zeroed, time::Instant};
use windows_sys::Win32::{Foundation::*, Graphics::Gdi::*, UI::{HiDpi::GetDpiForWindow, WindowsAndMessaging::*}};

pub fn wide(s: &str) -> Vec<u16> { s.encode_utf16().chain(Some(0)).collect() }
pub const WIDTH: i32 = 136;
pub const HEIGHT: i32 = 36;
const SS: i32 = 2;
const PROFILE: usize = 1024;

pub fn valid_position(position:&str)->bool {
    matches!(position,"bottom_left"|"bottom_center"|"bottom_right"|"top_left"|"top_center"|"top_right")
}
fn overlay_origin(work:RECT,width:i32,height:i32,margin:i32,position:&str)->(i32,i32) {
    let left=work.left+margin;let right=(work.right-width-margin).max(work.left);
    let top=work.top+margin;let bottom=(work.bottom-height-margin).max(work.top);
    let x=if position.ends_with("_left") {left.min(right)} else if position.ends_with("_right") {right} else {work.left+(work.right-work.left-width).max(0)/2};
    let y=if position.starts_with("top_") {top.min(bottom)} else {bottom};
    (x,y)
}

struct Edge { index: usize, position: f32, strength: f32 }

#[derive(Default)]
struct SurfaceCache {
    logical_width:i32,
    logical_height:i32,
    dpi:u32,
    width:i32,
    height:i32,
    rw:i32,
    rh:i32,
    sample_scale:f32,
    dc:HDC,
    bitmap:HBITMAP,
    old_bitmap:HGDIOBJ,
    pixels:*mut u32,
    frame:Vec<u32>,
    content:Vec<u32>,
    background:Vec<u32>,
    picker_background:Vec<u32>,
    body_shades:Vec<f32>,
    rim_shades:Vec<f32>,
    edges:Vec<Edge>,
    labels:Vec<Vec<u8>>,
    options:Vec<Vec<(usize,f32,f32)>>,
    rail:Vec<(usize,f32,f32)>,
}
impl Drop for SurfaceCache {fn drop(&mut self) {unsafe { if !self.dc.is_null() {SelectObject(self.dc,self.old_bitmap);DeleteObject(self.bitmap);DeleteDC(self.dc);} }}}

pub struct Display {
    cached_surface:Option<Box<SurfaceCache>>,
    sampling_x:Vec<(usize,f32)>,
    edge_profile:[f32;PROFILE*2],
    pub hwnd: HWND,
    pub title: String,
    pub subtitle: String,
    pub preview: String,
    pub recording: bool,
    pub language: String,
    pub wave: VecDeque<f32>,
    pub position:String,
    picker:bool,
    pub selected:usize,
    pub hovered:Option<usize>,
    logical_width:i32,
    logical_height:i32,
    labels:Vec<Vec<u8>>,
    options:Vec<Vec<(usize,f32,f32)>>,
    rail:Vec<(usize,f32,f32)>,
    selected_position:f32,
    hover_levels:[f32;3],
    mic_level:f32,
    visible: bool,
    closing: bool,
    animation: Instant,
    panel_progress:f32,
    content_progress:f32,
    transition_panel:f32,
    transition_content:f32,
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
    content: Vec<u32>,
    background: Vec<u32>,
    picker_background:Vec<u32>,
    controls_dirty:bool,
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
    metrics:Option<(std::path::PathBuf,Vec<[f64;4]>)>,
    metrics_started:Instant,
}
fn smooth(v: f32) -> f32 { let v=v.clamp(0.,1.); v*v*(3.-2.*v) }
fn capsule_distance(x: f32, y: f32, hw: f32, hh: f32, radius: f32) -> f32 {
    let qx=x.abs()-(hw-radius); let qy=y.abs()-(hh-radius);
    if qx<=0. || qy<=0. {qx.max(qy)-radius}
    else {(qx*qx+qy*qy).sqrt()-radius}
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
fn content_phase(age:f32,closing:bool,from:f32)->f32 {
    if closing {from*(1.-smooth(age/0.20))}
    else {from+(1.-from)*smooth((age-0.055)/0.31)}
}
fn content_transform(panel:f32,content:f32)->(f32,f32,f32) {
    ((panel*(0.94+0.06*content)).max(0.001),0.94+0.06*content,6.*(1.-content))
}
fn picker_option(x:f32,y:f32,panel:f32,content:f32)->Option<usize> {
    if content<0.1 {return None;}
    let (sx,sy,rise)=content_transform(panel,content);
    let x=(x-190.)/sx+190.;let y=(y-56.-rise)/sy+56.;
    (0..3).find(|i| {let left=20.+*i as f32*114.;x>=left&&x<=left+112.&&y>=56.&&y<=92.})
}
// Content remains a complete layer while the shell changes width. Sampling
// premultiplied gray and alpha together avoids dark fringes around moving text.
fn blend_content(destination:u32,gray:f32,alpha:f32,opacity:f32)->u32 {
    let a=alpha*opacity/255.;
    let c=(gray*opacity+(destination&255) as f32*(1.-a)).round().clamp(0.,255.) as u32;
    let alpha=(alpha*opacity+(destination>>24) as f32*(1.-a)).round().clamp(0.,255.) as u32;
    c|c<<8|c<<16|alpha<<24
}
// Position follows the capsule perimeter, rather than sliding across its fill.
fn perimeter(x:f32,y:f32,hw:f32,hh:f32,r:f32)->f32 {
    let sx=(hw-r).max(0.);let sy=(hh-r).max(0.);let pi=std::f32::consts::PI;
    let distance=if x>sx && y < -sy {2.*sx+((y+sy).atan2(x-sx)+pi/2.)*r}
        else if x>sx && y>sy {2.*sx+pi*r/2.+2.*sy+(y-sy).atan2(x-sx)*r}
        else if x>sx {2.*sx+pi*r/2.+y+sy}
        else if x < -sx && y>sy {4.*sx+pi*r+2.*sy+((y-sy).atan2(x+sx)-pi/2.)*r}
        else if x < -sx && y < -sy {4.*sx+1.5*pi*r+4.*sy+((y+sy).atan2(x+sx)+pi)*r}
        else if x < -sx {4.*sx+1.5*pi*r+2.*sy+sy-y}
        else if y<0. {x+sx}else{2.*sx+pi*r+2.*sy+sx-x};
    distance/(4.*sx+4.*sy+2.*pi*r).max(1.)
}
fn edge_strength(distance: f32) -> f32 {
    let rim=(-(distance/0.65).powi(2)).exp()*112.;
    let inside=if distance<0. { (distance/1.25).exp()*18. } else { 0. };
    rim+inside
}
// Horizontal span at a row of a rounded rectangle, including inset/outset
// contours. One square root per row replaces one distance evaluation per pixel.
fn row_extent(y:f32,hw:f32,hh:f32,radius:f32)->Option<f32> {
    if hw<0. || hh<0. || y.abs()>hh {return None;}
    let radius=radius.max(0.);let corner=(y.abs()-(hh-radius)).max(0.);
    Some(hw-radius+(radius*radius-corner*corner).max(0.).sqrt())
}
unsafe fn text_mask(text:&str,width:i32,height:i32,font_size:i32,center:bool)->Vec<u8> {
    let screen=GetDC(std::ptr::null_mut());let dc=CreateCompatibleDC(screen);ReleaseDC(std::ptr::null_mut(),screen);
    let mut info:BITMAPINFO=zeroed();info.bmiHeader.biSize=std::mem::size_of::<BITMAPINFOHEADER>() as u32;
    info.bmiHeader.biWidth=width;info.bmiHeader.biHeight=-height;info.bmiHeader.biPlanes=1;info.bmiHeader.biBitCount=32;
    let mut bits=std::ptr::null_mut();let bitmap=CreateDIBSection(dc,&info,DIB_RGB_COLORS,&mut bits,std::ptr::null_mut(),0);
    if bitmap.is_null() {DeleteDC(dc);return vec![0;(width*height) as usize];}
    let old=SelectObject(dc,bitmap);let pixels=std::slice::from_raw_parts_mut(bits as *mut u32,(width*height) as usize);pixels.fill(0);
    let font=CreateFontW(-font_size,0,0,0,400,0,0,0,DEFAULT_CHARSET as u32,0,0,ANTIALIASED_QUALITY as u32,0,wide("Segoe UI").as_ptr());
    let old_font=SelectObject(dc,font);SetBkMode(dc,TRANSPARENT as i32);SetTextColor(dc,0xffffff);
    let mut rect=RECT{left:0,top:0,right:width,bottom:height};let label=wide(text);
    DrawTextW(dc,label.as_ptr(),label.len() as i32-1,&mut rect,(if center {DT_CENTER}else{DT_LEFT})|DT_VCENTER|DT_SINGLELINE);
    GdiFlush();let result=pixels.iter().map(|pixel|(pixel&255) as u8).collect();
    SelectObject(dc,old_font);DeleteObject(font);SelectObject(dc,old);DeleteObject(bitmap);DeleteDC(dc);result
}
impl Display {
    pub unsafe fn new(hwnd: HWND) -> Self {
        let screen=GetDC(std::ptr::null_mut()); let dc=CreateCompatibleDC(screen); ReleaseDC(std::ptr::null_mut(),screen);
        let now=Instant::now();
        let mut result=Self { cached_surface:None,sampling_x:Vec::new(),edge_profile:[0.;PROFILE*2],hwnd,title:String::new(),subtitle:String::new(),preview:String::new(),recording:false,
            language:"EN".into(),wave:VecDeque::from(vec![0.;20]),position:"bottom_center".into(),visible:false,closing:false,animation:now,cue_at:now,
            end_started:None,origin:(0,0),dpi:0,width:0,height:0,rw:0,rh:0,sample_scale:2.,dc,
            panel_progress:0.,content_progress:0.,transition_panel:0.,transition_content:0.,
            picker:false,selected:0,hovered:None,logical_width:WIDTH,logical_height:HEIGHT,labels:Vec::new(),options:Vec::new(),
            rail:Vec::new(),selected_position:0.,hover_levels:[0.;3],mic_level:0.,
            bitmap:std::ptr::null_mut(),old_bitmap:std::ptr::null_mut(),pixels:std::ptr::null_mut(),frame:Vec::new(),content:Vec::new(),
            background:Vec::new(),picker_background:Vec::new(),controls_dirty:true,body_shades:Vec::new(),rim_shades:Vec::new(),edges:Vec::new(),shine:[0.;PROFILE],
            levels:[0.;20],last_frame:now,preview_path:std::env::var_os("LOCALFLOW_PREVIEW_DIR")
                .map(|p|std::path::PathBuf::from(p).join("glass-frame.bgra")),last_preview:now,
            frame_dir:std::env::var_os("TRANSCRIBER_ANIMATION_FRAMES").map(std::path::PathBuf::from),frame_index:0,
            metrics:std::env::var_os("DICTATELY_FRAME_METRICS").map(|path|(path.into(),Vec::new())),metrics_started:now };
        for (i,value) in result.shine.iter_mut().enumerate() {
            let d=(i as f32/PROFILE as f32).min(1.-i as f32/PROFILE as f32);
            *value=(-(d/0.055).powi(2)).exp();
        }
        for index in 0..PROFILE {
            let distance=index as f32*4.5/(PROFILE-1) as f32;
            result.edge_profile[index]=edge_strength(-distance)+if index==0 {18.}else{0.};
            result.edge_profile[index+PROFILE]=edge_strength(distance);
        }
        result.configure(GetDpiForWindow(hwnd).max(96)); result
    }
    unsafe fn configure(&mut self,dpi:u32) {
        if self.dpi==dpi { return; }
        self.dpi=dpi; let scale=dpi as f32/96.;
        self.width=(self.logical_width as f32*scale).round() as i32; self.height=(self.logical_height as f32*scale).round() as i32;
        self.rw=self.width*SS; self.rh=self.height*SS; self.sample_scale=self.rw as f32/self.logical_width as f32;
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
        self.frame=vec![0;count]; self.content=if self.picker {vec![0;count]}else{Vec::new()}; self.picker_background=if self.picker {vec![0;count]}else{Vec::new()}; self.controls_dirty=true; self.background=vec![0;count]; self.body_shades=vec![0.;count];
        self.rim_shades=vec![0.;count]; self.edges.clear();
        let hw=self.logical_width as f32/2.-1.; let hh=self.logical_height as f32/2.-1.;
        let radius=if self.picker {28.}else{hh};
        for y in 0..self.rh { for x in 0..self.rw {
            let px=(x as f32+0.5)/self.sample_scale; let py=(y as f32+0.5)/self.sample_scale;
            let nx=(px/self.logical_width as f32-0.5)*2.; let ny=py/self.logical_height as f32;
            let distance=capsule_distance(px-self.logical_width as f32/2.,py-self.logical_height as f32/2.,hw,hh,radius);
            let sheen=(-((nx+0.4)/0.9).powi(2)-((ny-0.1)/0.29).powi(2)).exp();
            let lower=(-((nx-0.65)/0.75).powi(2)-((ny-0.91)/0.2).powi(2)).exp();
            let base=16.+(1.-ny)*9.+sheen*28.+lower*12.; let highlight=93.+(1.-ny)*92.;
            let index=(y*self.rw+x) as usize;
            self.body_shades[index]=base; self.rim_shades[index]=highlight;
            self.background[index]=self.body_pixel(index,distance);
            if distance.abs()<4.5 { self.edges.push(Edge { index,position:perimeter(px-self.logical_width as f32/2.,py-self.logical_height as f32/2.,hw,hh,radius),strength:edge_strength(distance) }); }
        } }
        self.write_dimensions();
        self.labels.clear();
        self.options.clear();
        self.rail.clear();
        if self.picker {
            for (index,text) in ["Language","English","Indonesia","Both"].iter().enumerate() {self.labels.push(text_mask(text,(112.*self.sample_scale) as i32,(22.*self.sample_scale) as i32,(12.*self.sample_scale) as i32,index>0));}
            for y in (51.*self.sample_scale) as i32..(97.*self.sample_scale) as i32 {
                for x in (15.*self.sample_scale) as i32..(365.*self.sample_scale) as i32 {
                    let px=(x as f32+0.5)/self.sample_scale;let py=(y as f32+0.5)/self.sample_scale;
                    let d=capsule_distance(px-190.,py-74.,174.,22.,22.);
                    let mask=(0.5-d*self.sample_scale).clamp(0.,1.);
                    if mask>0. {let rim=(1.-d.abs()).clamp(0.,1.);self.rail.push(((y*self.rw+x) as usize,mask,19.+rim*31.));}
                }
            }
            for option in 0..3 {
                let mut pixels=Vec::new();let left=20.+114.*option as f32;
                for y in (55.*self.sample_scale) as i32..(93.*self.sample_scale) as i32 {
                    for x in ((left-1.)*self.sample_scale) as i32..((left+113.)*self.sample_scale) as i32 {
                        let px=(x as f32+0.5)/self.sample_scale;let py=(y as f32+0.5)/self.sample_scale;
                        let distance=capsule_distance(px-(left+56.),py-74.,56.,18.,18.);
                        let mask=(0.5-distance*self.sample_scale).clamp(0.,1.);
                        if mask>0. {pixels.push(((y*self.rw+x) as usize,mask,(1.-(py-56.)/36.).clamp(0.,1.)));}
                    }
                }
                self.options.push(pixels);
            }
        }
    }
    fn exchange_surface(&mut self,other:&mut SurfaceCache) {
        std::mem::swap(&mut self.logical_width,&mut other.logical_width);
        std::mem::swap(&mut self.logical_height,&mut other.logical_height);
        std::mem::swap(&mut self.dpi,&mut other.dpi);
        std::mem::swap(&mut self.width,&mut other.width);
        std::mem::swap(&mut self.height,&mut other.height);
        std::mem::swap(&mut self.rw,&mut other.rw);
        std::mem::swap(&mut self.rh,&mut other.rh);
        std::mem::swap(&mut self.sample_scale,&mut other.sample_scale);
        std::mem::swap(&mut self.dc,&mut other.dc);
        std::mem::swap(&mut self.bitmap,&mut other.bitmap);
        std::mem::swap(&mut self.old_bitmap,&mut other.old_bitmap);
        std::mem::swap(&mut self.pixels,&mut other.pixels);
        std::mem::swap(&mut self.frame,&mut other.frame);
        std::mem::swap(&mut self.content,&mut other.content);
        std::mem::swap(&mut self.background,&mut other.background);
        std::mem::swap(&mut self.picker_background,&mut other.picker_background);
        std::mem::swap(&mut self.body_shades,&mut other.body_shades);
        std::mem::swap(&mut self.rim_shades,&mut other.rim_shades);
        std::mem::swap(&mut self.edges,&mut other.edges);
        std::mem::swap(&mut self.labels,&mut other.labels);
        std::mem::swap(&mut self.options,&mut other.options);
        std::mem::swap(&mut self.rail,&mut other.rail);
    }
    fn strength(&self,distance:f32)->f32 {
        let position=(distance.abs()*(PROFILE-1) as f32/4.5).clamp(0.,(PROFILE-1) as f32);
        let index=position as usize;let fraction=position-index as f32;
        let offset=if distance<0. {0}else{PROFILE};
        self.edge_profile[index+offset]+(self.edge_profile[(index+1).min(PROFILE-1)+offset]-self.edge_profile[index+offset])*fraction
    }
    pub unsafe fn prewarm_picker(&mut self) {
        self.set_picker(true);self.render_options(0.,0.);self.set_picker(false);
    }
    pub unsafe fn set_picker(&mut self,picker:bool) {
        self.hovered=None;self.hover_levels.fill(0.);self.mic_level=0.;
        if self.picker==picker {return;}
        self.picker=picker;self.hovered=None;
        let dpi=GetDpiForWindow(self.hwnd).max(96);
        let width=if picker {380}else{WIDTH};
        if let Some(mut cached)=self.cached_surface.take() {
            if cached.dpi==dpi && cached.logical_width==width {
                self.exchange_surface(&mut cached);self.cached_surface=Some(cached);self.controls_dirty=true;self.write_dimensions();return;
            }
        }
        let mut cached=Box::new(SurfaceCache::default());self.exchange_surface(&mut cached);
        let screen=GetDC(std::ptr::null_mut());self.dc=CreateCompatibleDC(screen);ReleaseDC(std::ptr::null_mut(),screen);
        self.logical_width=width;self.logical_height=if picker {112}else{HEIGHT};
        self.configure(dpi);self.cached_surface=Some(cached);
    }
    pub fn option_at(&self,x:i32,y:i32)->Option<usize> {
        if !self.picker || !self.visible || self.closing {return None;}
        picker_option(x as f32*96./self.dpi as f32,y as f32*96./self.dpi as f32,self.panel_progress,self.content_progress)
    }
    fn render_options(&mut self,age:f32,dt:f32) {
        let before=self.selected_position;let hover_before=self.hover_levels;
        let settle=1.-(-dt/0.065).exp();
        self.selected_position+=(self.selected as f32-self.selected_position)*settle;
        for option in 0..3 {self.hover_levels[option]+=((if self.hovered==Some(option) {1.}else{0.})-self.hover_levels[option])*settle;}
        if (self.selected_position-self.selected as f32).abs()<0.0005 {self.selected_position=self.selected as f32;}
        for option in 0..3 {let target=if self.hovered==Some(option) {1.}else{0.};if (self.hover_levels[option]-target).abs()<0.0005 {self.hover_levels[option]=target;}}
        self.controls_dirty|=before!=self.selected_position || hover_before!=self.hover_levels;
        if self.controls_dirty {
        self.content.fill(0);
        for &(index,mask,shade) in &self.rail {
            self.content[index]=over(self.content[index],shade,mask);
        }
        for (option,pixels) in self.options.iter().enumerate() {
            for &(index,mask,vertical) in pixels {
                let _=vertical;
                self.content[index]=over(self.content[index],75.,mask*self.hover_levels[option]*0.32);
            }
        }
        let left=20.+114.*self.selected_position;
        for y in (55.*self.sample_scale) as i32..(93.*self.sample_scale) as i32 {for x in ((left-1.)*self.sample_scale) as i32..((left+113.)*self.sample_scale) as i32 {
            let px=(x as f32+0.5)/self.sample_scale;let py=(y as f32+0.5)/self.sample_scale;
            let d=capsule_distance(px-(left+56.),py-74.,56.,18.,18.);
            let mask=(0.5-d*self.sample_scale).clamp(0.,1.);let index=(y*self.rw+x) as usize;
            let shine=(1.-(py-56.)/36.).clamp(0.,1.);
            self.content[index]=over(self.content[index],204.+shine*35.,mask);
        }}
        let w=(112.*self.sample_scale) as i32;let h=(22.*self.sample_scale) as i32;
        for label in 0..4 {
            let left=if label==0 {24.}else{20.+114.*(label-1) as f32};
            let top=if label==0 {15.}else{63.};
            let ox=(left*self.sample_scale).round() as i32;let oy=(top*self.sample_scale).round() as i32;
            let proximity=if label==0 {0.}else{(1.-(self.selected_position-(label-1) as f32).abs()).clamp(0.,1.)};
            let shade=220.*(1.-proximity)+22.*proximity;
            for y in 0..h {for x in 0..w {
                let px=ox+x;let py=oy+y;if px<0 || px>=self.rw || py<0 || py>=self.rh {continue;}
                let index=(py*self.rw+px) as usize;
                self.content[index]=over(self.content[index],shade,self.labels[label][(y*w+x) as usize] as f32/255.);
            }}
        }
        self.picker_background.copy_from_slice(&self.background);
        for (destination,source) in self.picker_background.iter_mut().zip(&self.content) {
            if source>>24>0 {*destination=blend_content(*destination,(source&255) as f32,(source>>24) as f32,1.);}
        }
        self.controls_dirty=false;
        }
        // Only the five microphone bars change once selection and hover settle.
        for y in (14.*self.sample_scale) as i32..(40.*self.sample_scale) as i32 {
            let start=(y*self.rw) as usize;
            self.content[start+(328.*self.sample_scale) as usize..start+(353.*self.sample_scale) as usize].fill(0);
        }
        let level=self.wave.back().copied().unwrap_or(0.);
        self.mic_level+=(level-self.mic_level)*(1.-(-dt/0.055).exp());
        for i in 0..5 {
            let height=4.+(self.mic_level*15.+2.)*(0.5+0.5*(age*7.+i as f32).sin());let cx=330.+i as f32*5.;
            for y in ((27.-height/2.-1.)*self.sample_scale) as i32..((27.+height/2.+1.)*self.sample_scale) as i32 {
                for x in ((cx-2.)*self.sample_scale) as i32..((cx+2.)*self.sample_scale) as i32 {
                    if x<0||y<0||x>=self.rw||y>=self.rh {continue;}
                    let d=capsule_distance((x as f32+0.5)/self.sample_scale-cx,(y as f32+0.5)/self.sample_scale-27.,1.1,height/2.,1.1);
                    let index=(y*self.rw+x) as usize;
                    self.content[index]=over(self.content[index],215.,(0.5-d*self.sample_scale).clamp(0.,1.));
                }
            }
        }
    }
    fn body_pixel(&self,index:usize,distance:f32)->u32 {
        let mask=(0.5-distance*self.sample_scale).clamp(0.,1.);
        let rim=(1.0-distance.abs()).clamp(0.,1.);
        let shade=self.body_shades[index]*(1.-rim*0.65)+self.rim_shades[index]*rim*0.65;
        premultiplied(shade,(0.82+rim*0.14)*mask)
    }
    fn composite_options(&mut self) {
        let opacity=self.content_progress;
        if opacity<=0. {return;}
        if self.panel_progress>=0.9999 && opacity>=0.9999 {
            for y in (14.*self.sample_scale) as i32..(40.*self.sample_scale) as i32 {
                for x in (328.*self.sample_scale) as i32..(353.*self.sample_scale) as i32 {
                    let index=(y*self.rw+x) as usize;let source=self.content[index];
                    if source>>24>0 {self.frame[index]=blend_content(self.frame[index],(source&255) as f32,(source>>24) as f32,1.);}
                }
            }
            return;
        }
        let (sx,sy,rise)=content_transform(self.panel_progress,opacity);
        let cx=self.rw as f32/2.;let cy=self.rh as f32/2.;
        let rise=rise*self.sample_scale;
        // Inverse mapping avoids gaps as the rail and glyphs widen together.
        let left=(cx+(15.*self.sample_scale-cx)*sx).floor().max(0.) as i32;
        let right=(cx+(365.*self.sample_scale-cx)*sx).ceil().min(self.rw as f32) as i32;
        let top=(cy+(14.*self.sample_scale-cy)*sy+rise).floor().max(0.) as i32;
        let bottom=(cy+(98.*self.sample_scale-cy)*sy+rise).ceil().min(self.rh as f32) as i32;
        self.sampling_x.clear();
        let inverse_x=1./sx;let inverse_y=1./sy;
        for x in left..right {
            let px=(x as f32+0.5-cx)*inverse_x+cx-0.5;
            self.sampling_x.push(if px<0. || px>=self.rw as f32-1. {(usize::MAX,0.)}else{(px as usize,px-px.floor())});
        }
        for y in top..bottom {
            let py=(y as f32+0.5-cy-rise)*inverse_y+cy-0.5;
            if py<0. || py>=self.rh as f32-1. {continue;}
            let y0=py.floor() as i32;let fy=py-y0 as f32;
            for x in left..right {
                let (x0,fx)=self.sampling_x[(x-left) as usize];
                if x0==usize::MAX {continue;}
                let index=y0 as usize*self.rw as usize+x0;
                let pixels=[self.content[index],self.content[index+1],self.content[index+self.rw as usize],self.content[index+self.rw as usize+1]];
                if pixels.iter().all(|pixel|pixel>>24==0) {continue;}
                let interpolate=|shift:u32| {
                    let a=((pixels[0]>>shift)&255) as f32;let b=((pixels[1]>>shift)&255) as f32;
                    let c=((pixels[2]>>shift)&255) as f32;let d=((pixels[3]>>shift)&255) as f32;
                    let top=a+(b-a)*fx;top+(c+(d-c)*fx-top)*fy
                };
                let gray=interpolate(0);let alpha=interpolate(24);
                if alpha>0. {
                    let destination=(y*self.rw+x) as usize;
                    self.frame[destination]=blend_content(self.frame[destination],gray,alpha,opacity);
                }
            }
        }
    }
    fn write_dimensions(&self) {
        let value=format!("{{\"width\":{},\"height\":{},\"logical_width\":{},\"logical_height\":{},\"supersampling\":2,\"dpi\":{}}}",self.width,self.height,self.logical_width,self.logical_height,self.dpi);
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
        self.origin=overlay_origin(work,self.width,self.height,(22.*self.dpi as f32/96.).round() as i32,&self.position);
        if !self.visible||self.closing {
            self.transition_panel=if self.visible {self.panel_progress}else{0.};
            self.transition_content=if self.visible {self.content_progress}else{0.};
            self.animation=Instant::now();self.last_frame=Instant::now();self.visible=true;self.closing=false;self.selected_position=self.selected as f32;self.controls_dirty=true;
        }
        self.animate();
    }
    pub unsafe fn redraw(&self) {}
    pub unsafe fn hide(&mut self) { if self.visible&&!self.closing {
        self.transition_panel=self.panel_progress;self.transition_content=self.content_progress;
        self.closing=true;self.animation=Instant::now();
    } }
    pub fn is_visible(&self)->bool { self.visible }
    fn render_shell(&mut self,progress:f32,phase:f32) {
        let profile=|position:f32| { let d=position-phase; let d=if d<0. {d+1.}else{d};self.shine[((d*PROFILE as f32) as usize).min(PROFILE-1)] };
        let hw=15.+(self.logical_width as f32/2.-16.)*progress;let hh=self.logical_height as f32/2.-1.;let radius=hw.min(if self.picker {28.}else{hh});
        if progress<0.999 {
            self.frame.fill(0);
            let cx=self.logical_width as f32/2.;let inverse_scale=1./self.sample_scale;
            for y in 0..self.rh {
                let py=(y as f32+0.5)*inverse_scale-self.logical_height as f32/2.;
                let Some(extent)=row_extent(py,hw+inverse_scale,hh+inverse_scale,radius+inverse_scale) else {continue;};
                let left=((cx-extent)*self.sample_scale).floor().max(0.) as i32;
                let right=((cx+extent)*self.sample_scale).ceil().min(self.rw as f32) as i32;
                let row=(y*self.rw) as usize;
                self.frame[row+left as usize..row+right as usize].copy_from_slice(&self.background[row+left as usize..row+right as usize]);
                let inner=row_extent(py,hw-5.,hh-5.,radius-5.);
                let (inner_left,inner_right)=inner.map(|extent|(((cx-extent)*self.sample_scale).ceil() as i32,((cx+extent)*self.sample_scale).floor() as i32)).unwrap_or((right,right));
                // Interior shades are width-independent. Recompute only the
                // contour band where shape alpha and the moving rim can change.
                for range in [left..inner_left.min(right),inner_right.max(left)..right] {
                for x in range {
                    let px=(x as f32+0.5)*inverse_scale-cx;
                    let distance=capsule_distance(px,py,hw,hh,radius);let index=row+x as usize;
                    let mut pixel=self.body_pixel(index,distance);
                    if distance.abs()<4.5 && pixel>>24>0 {
                        let alpha=(pixel>>24) as f32/255.;let shade=(pixel&255) as f32/alpha;
                        pixel=premultiplied(shade+profile(perimeter(px,py,hw,hh,radius))*self.strength(distance),alpha);
                    }
                    self.frame[index]=pixel;
                }
                }
            }
        } else {
            self.frame.copy_from_slice(if self.picker && progress>=0.9999 && self.content_progress>=0.9999 {&self.picker_background}else{&self.background});
            for edge in &self.edges {
                let pixel=self.frame[edge.index];let alpha=(pixel>>24) as f32/255.;
                if alpha>0. { self.frame[edge.index]=premultiplied((pixel&255) as f32/alpha+profile(edge.position)*edge.strength,alpha); }
            }
        }
    }
    pub unsafe fn animate(&mut self) {
        if !self.visible||self.pixels.is_null() { return; }
        let now=Instant::now();let interval=now.duration_since(self.last_frame).as_secs_f64();let dt=(interval as f32).min(0.05);self.last_frame=now;
        let age=self.animation.elapsed().as_secs_f32();let t=(age/if self.closing {0.34}else{0.42}).min(1.);
        if self.closing&&t>=1. { ShowWindow(self.hwnd,SW_HIDE);self.visible=false;return; }
        let progress=if self.closing {self.transition_panel*(1.-smooth(t))}
            else {self.transition_panel+(1.-self.transition_panel)*(1.-(1.-t).powi(3))};
        self.panel_progress=progress;
        self.content_progress=content_phase(age,self.closing,self.transition_content);
        let cue_age=self.cue_at.elapsed().as_secs_f32();
        if self.picker && self.content_progress>0. {self.render_options(cue_age,dt);}
        self.render_shell(progress,(cue_age*0.36).fract());
        let ending=self.end_started.map(|at|smooth(at.elapsed().as_secs_f32()/0.55)).unwrap_or(0.);
        let spread=(0.12+0.88*progress)*(1.-ending*0.5);
        // Smoothstep has zero slope at both ends; exponential settling continues
        // across the handoff instead of switching between max(intro, microphone).
        let handoff=smooth((cue_age-0.12)/0.85);
        let settle=1.-(-dt/0.085).exp();
        if self.picker {
            if self.content_progress>0. {self.composite_options();}
        } else {
        for i in 0..20 {
            let center_distance=((i as f32-9.5)/9.5).abs();let reveal=smooth((progress-center_distance*0.42)/0.58);
            let ripple=(cue_age*6.-center_distance*3.).sin()*0.5+0.5;
            let intro=0.20+0.28*ripple;
            let target=if self.recording {intro*(1.-handoff)+self.wave[i]*handoff}else{(0.18+0.22*ripple)*(1.-ending*0.65)};
            self.levels[i]+=(target-self.levels[i])*settle;
            let height=((3.+self.levels[i]*16.)*(0.86+progress*0.14)*(0.25+reveal*0.75)).max(1.8);
            let cx=self.logical_width as f32/2.+(i as f32-9.5)*4.6*spread;let cy=self.logical_height as f32/2.;let r=1.3*(1.-ending*0.2);
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
        }
        // Average premultiplied samples before compositing, preserving clean alpha edges.
        let output=std::slice::from_raw_parts_mut(self.pixels,(self.width*self.height) as usize);
        for y in 0..self.height {for x in 0..self.width {
            let start=(y*SS*self.rw+x*SS) as usize;
            let samples=[self.frame[start],self.frame[start+1],self.frame[start+self.rw as usize],self.frame[start+self.rw as usize+1]];
            let a=samples.iter().map(|p|p>>24).sum::<u32>();let c=samples.iter().map(|p|p&255).sum::<u32>();
            let a=(a+2)/4;let c=(c+2)/4;output[(y*self.width+x) as usize]=c|c<<8|c<<16|a<<24;
        }}
        let direction=if self.position.starts_with("top_") {-1.}else{1.};
        let destination=POINT{x:self.origin.0,y:self.origin.1+(direction*(1.-progress)*12.*self.dpi as f32/96.) as i32};
        let size=SIZE{cx:self.width,cy:self.height};let source=POINT{x:0,y:0};
        let blend=BLENDFUNCTION{BlendOp:AC_SRC_OVER as u8,BlendFlags:0,SourceConstantAlpha:(progress*255.) as u8,AlphaFormat:AC_SRC_ALPHA as u8};
        UpdateLayeredWindow(self.hwnd,std::ptr::null_mut(),&destination,&size,self.dc,&source,0,&blend,ULW_ALPHA);
        if let Some((_,frames))=&mut self.metrics {
            frames.push([now.duration_since(self.metrics_started).as_secs_f64()*1000.,interval*1000.,now.elapsed().as_secs_f64()*1000.,if self.closing {1.}else{0.}]);
        }
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
impl Drop for Display {fn drop(&mut self){
    if let Some((path,frames))=&self.metrics {if let Ok(bytes)=serde_json::to_vec(frames) {let _=std::fs::write(path,bytes);}}
    unsafe{SelectObject(self.dc,self.old_bitmap);DeleteObject(self.bitmap);DeleteDC(self.dc);}
}}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn row_spans_preserve_the_reference_capsule_contour() {
        for hw in [15f32,28.,80.,189.] {
            let radius=hw.min(28.);let hh=55.;
            for y in -60..=60 {for x in -195..=195 {
                let distance=capsule_distance(x as f32,y as f32,hw,hh,radius);
                let inside=row_extent(y as f32,hw-5.,hh-5.,radius-5.).is_some_and(|extent|(x as f32).abs()<=extent);
                assert_eq!(inside,distance<=-5.+0.00001,"{hw} {x} {y} {distance}");
            }}
        }
    }

    #[test]
    fn optimized_shell_preserves_reference_pixels_during_transitions() {
        unsafe {
            let mut display=Display::new(std::ptr::null_mut());
            for picker in [false,true] {for dpi in [96,168] {
                display.set_picker(picker);display.configure(dpi);
                for progress in [0f32,0.08,0.35,0.7,0.98] {
                    display.render_shell(progress,0.27);
                    let hw=15.+(display.logical_width as f32/2.-16.)*progress;
                    let hh=display.logical_height as f32/2.-1.;let radius=hw.min(if picker {28.}else{hh});
                    for y in 0..display.rh {for x in 0..display.rw {
                        let px=(x as f32+0.5)/display.sample_scale-display.logical_width as f32/2.;
                        let py=(y as f32+0.5)/display.sample_scale-display.logical_height as f32/2.;
                        let distance=capsule_distance(px,py,hw,hh,radius);let index=(y*display.rw+x) as usize;
                        let mut expected=display.body_pixel(index,distance);
                        if distance.abs()<4.5 && expected>>24>0 {
                            let alpha=(expected>>24) as f32/255.;
                            let d=(perimeter(px,py,hw,hh,radius)-0.27).rem_euclid(1.);
                            let shine=display.shine[((d*PROFILE as f32) as usize).min(PROFILE-1)];
                            expected=premultiplied((expected&255) as f32/alpha+shine*edge_strength(distance),alpha);
                        }
                        let actual=display.frame[index];
                        for shift in [0,24] {
                            assert!((((actual>>shift)&255) as i32-((expected>>shift)&255) as i32).abs()<=1,"{picker} {dpi} {progress} {x} {y}: {actual:x} {expected:x}");
                        }
                    }}
                }
            }}
        }
    }

    #[test]
    fn cached_surfaces_reuse_gdi_resources_and_keep_rim_precision() {
        unsafe {
            let mut display=Display::new(std::ptr::null_mut());
            let waveform=display.bitmap;display.prewarm_picker();
            assert_eq!(display.bitmap,waveform);
            display.set_picker(true);let picker=display.bitmap;
            assert!(!picker.is_null());assert_ne!(picker,waveform);
            for _ in 0..12 {display.set_picker(false);assert_eq!(display.bitmap,waveform);display.set_picker(true);assert_eq!(display.bitmap,picker);}
            for sample in -45000..=45000 {
                let distance=sample as f32/10000.;
                assert!((display.strength(distance)-edge_strength(distance)).abs()<0.002,"{distance}");
            }
        }
    }

    #[test]
    fn positions_respect_work_area_scaling_and_negative_monitor_coordinates() {
        let area=RECT{left:-1920,top:0,right:0,bottom:1040};
        for (position,expected) in [("bottom_left",(-1898,982)),("bottom_center",(-1028,982)),("bottom_right",(-158,982)),("top_left",(-1898,22)),("top_center",(-1028,22)),("top_right",(-158,22))] {
            assert!(valid_position(position));assert_eq!(overlay_origin(area,136,36,22,position),expected);
        }
        let tiny=RECT{left:0,top:0,right:80,bottom:20};
        assert_eq!(overlay_origin(tiny,136,36,44,"top_right"),(0,0));
        assert!(!valid_position("sideways"));
    }

    #[test]
    fn rounded_geometry_fast_path_matches_distance_formula() {
        for radius in [1.3,18.,28.] {
            for x in -200..201 {for y in -60..61 {
                let qx=(x as f32).abs()-(190.-radius);
                let qy=(y as f32).abs()-(55.-radius);
                let expected=(qx.max(0.).powi(2)+qy.max(0.).powi(2)).sqrt()+qx.max(qy).min(0.)-radius;
                assert!((capsule_distance(x as f32,y as f32,190.,55.,radius)-expected).abs()<0.0001);
            }}
        }
    }

    #[test]
    fn picker_content_settles_and_disappears_before_shell_closes() {
        assert_eq!(content_phase(0.,false,0.),0.);
        assert_eq!(content_phase(0.055,false,0.),0.);
        assert_eq!(content_phase(0.42,false,0.),1.);
        assert_eq!(content_phase(0.20,true,1.),0.);
        let mut previous=0.;
        for frame in 0..43 {
            let current=content_phase(frame as f32/100.,false,0.);
            assert!(current>=previous);previous=current;
        }
        // Toggling during entry continues from the visible content opacity.
        let interrupted=content_phase(0.13,false,0.);
        assert_eq!(content_phase(0.,true,interrupted),interrupted);
        let reversing=content_phase(0.06,true,interrupted);
        assert_eq!(content_phase(0.,false,reversing),reversing);
    }

    #[test]
    fn moving_content_blends_premultiplied_alpha() {
        let background=premultiplied(30.,0.8);
        assert_eq!(blend_content(background,100.,128.,0.),background);
        assert_eq!(blend_content(background,255.,255.,1.),0xffffffff);
        let faded=blend_content(0,100.,128.,0.5);
        assert_eq!(faded&255,50);
        assert_eq!(faded>>24,64);
    }

    #[test]
    fn picker_hit_coordinates_follow_visible_options() {
        // Early in entry English is visually inside the final Indonesia bounds.
        assert_eq!(picker_option(124.9,78.,0.6,0.2),Some(0));
        assert_eq!(picker_option(190.,78.,0.6,0.2),Some(1));
        assert_eq!(picker_option(255.1,78.,0.6,0.2),Some(2));
        assert_eq!(picker_option(190.,78.,0.6,0.05),None);
        assert_eq!(picker_option(190.,30.,1.,1.),None);
        assert_eq!(picker_option(304.,74.,1.,1.),Some(2));
    }
}
