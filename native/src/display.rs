use std::{collections::VecDeque, mem::zeroed, time::Instant};
use windows_sys::Win32::{Foundation::*, Graphics::Gdi::*, UI::{HiDpi::GetDpiForWindow, WindowsAndMessaging::*}};

pub fn wide(s: &str) -> Vec<u16> { s.encode_utf16().chain(Some(0)).collect() }
pub const WIDTH: i32 = 144;
pub const HEIGHT: i32 = 44;
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

struct Edge { index: usize, position: f32, strength: f32, distance:f32 }

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
    body_shades:Vec<f32>,
    rim_shades:Vec<f32>,
    edges:Vec<Edge>,
    labels:Vec<Vec<u8>>,
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
    pub pipeline:crate::pipeline::Pipeline,
    processing_glow:f32,glow_breath:f32,pop_level:f32,pop_from:f32,delivered_at:Option<Instant>,
    pub language: String,
    pub wave: VecDeque<f32>,
    pub position:String,
    picker:bool,
    pub selected:usize,
    pub hovered:Option<usize>,
    logical_width:i32,
    logical_height:i32,
    labels:Vec<Vec<u8>>,
    selected_position:f32,
    pub picker_turn:f32,
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
    rim_at: Instant,
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
fn delivery_pop(age:f32,from:f32)->f32 {
    if age<0.13 {from+(1.-from)*smooth(age/0.13)}
    else {1.-smooth((age-0.13)/0.46)}
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
fn picker_option(x:f32,y:f32,panel:f32,content:f32)->Option<usize> {
    if content<0.15 {return None;}
    let hw=15.+(78.-9.-15.)*panel;
    (capsule_distance(x-78.,y-22.,hw,13.,hw.min(13.))<=0.).then_some(0)
}
// Sampling premultiplied gray and alpha together avoids dark fringes on text.
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
        let mut result=Self { cached_surface:None,sampling_x:Vec::new(),edge_profile:[0.;PROFILE*2],hwnd,title:String::new(),subtitle:String::new(),preview:String::new(),recording:false,pipeline:crate::pipeline::Pipeline::default(),processing_glow:0.,glow_breath:0.5,pop_level:0.,pop_from:0.,delivered_at:None,
            language:"EN".into(),wave:VecDeque::from(vec![0.;20]),position:"bottom_center".into(),visible:false,closing:false,animation:now,cue_at:now,rim_at:now,
            end_started:None,origin:(0,0),dpi:0,width:0,height:0,rw:0,rh:0,sample_scale:2.,dc,
            panel_progress:0.,content_progress:0.,transition_panel:0.,transition_content:0.,
            picker:false,selected:0,hovered:None,logical_width:WIDTH,logical_height:HEIGHT,labels:Vec::new(),
            selected_position:0.,picker_turn:0.,hover_levels:[0.;3],mic_level:0.,
            bitmap:std::ptr::null_mut(),old_bitmap:std::ptr::null_mut(),pixels:std::ptr::null_mut(),frame:Vec::new(),content:Vec::new(),
            background:Vec::new(),controls_dirty:true,body_shades:Vec::new(),rim_shades:Vec::new(),edges:Vec::new(),shine:[0.;PROFILE],
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
        self.frame=vec![0;count]; self.content=if self.picker {vec![0;count]}else{Vec::new()}; self.controls_dirty=true; self.background=vec![0;count]; self.body_shades=vec![0.;count];
        self.rim_shades=vec![0.;count]; self.edges.clear();
        let inset=9.;
        let hw=self.logical_width as f32/2.-inset; let hh=self.logical_height as f32/2.-inset;
        let radius=hh;
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
            if distance.abs()<7. { self.edges.push(Edge { index,position:perimeter(px-self.logical_width as f32/2.,py-self.logical_height as f32/2.,hw,hh,radius),strength:edge_strength(distance),distance }); }
        } }
        self.write_dimensions();
        self.labels.clear();
        if self.picker {
            for text in ["English","Indonesia"] {self.labels.push(text_mask(text,(94.*self.sample_scale) as i32,(20.*self.sample_scale) as i32,(12.*self.sample_scale) as i32,true));}
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
        std::mem::swap(&mut self.body_shades,&mut other.body_shades);
        std::mem::swap(&mut self.rim_shades,&mut other.rim_shades);
        std::mem::swap(&mut self.edges,&mut other.edges);
        std::mem::swap(&mut self.labels,&mut other.labels);
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
        if self.visible {
            ShowWindow(self.hwnd,SW_HIDE);self.visible=false;self.closing=false;
            self.panel_progress=0.;self.content_progress=0.;
        }
        self.picker=picker;self.hovered=None;
        let dpi=GetDpiForWindow(self.hwnd).max(96);
        let width=if picker {156}else{WIDTH};
        if let Some(mut cached)=self.cached_surface.take() {
            if cached.dpi==dpi && cached.logical_width==width {
                self.exchange_surface(&mut cached);self.cached_surface=Some(cached);self.controls_dirty=true;self.write_dimensions();return;
            }
        }
        let mut cached=Box::new(SurfaceCache::default());self.exchange_surface(&mut cached);
        let screen=GetDC(std::ptr::null_mut());self.dc=CreateCompatibleDC(screen);ReleaseDC(std::ptr::null_mut(),screen);
        self.logical_width=width;self.logical_height=HEIGHT;
        self.configure(dpi);self.cached_surface=Some(cached);
    }
    pub fn text_delivered(&mut self) {self.pop_from=self.pop_level;self.delivered_at=Some(Instant::now());}
    pub fn click_step(&self,_x:i32)->i32 {1}
    pub fn drag_step(&self)->i32 {(24.*self.dpi as f32/96.).round().max(1.) as i32}
    pub fn option_at(&self,x:i32,y:i32)->Option<usize> {
        if !self.picker || !self.visible || self.closing {return None;}
        picker_option(x as f32*96./self.dpi as f32,y as f32*96./self.dpi as f32,self.panel_progress,self.content_progress)
    }
    pub fn spin(&mut self,steps:i32) {
        self.picker_turn+=steps as f32;
        self.selected=(self.picker_turn.round() as i32).rem_euclid(2) as usize;
    }
    pub fn select_language(&mut self,index:usize) {if self.selected!=index {self.spin(1);}}
    fn render_options(&mut self,age:f32,dt:f32) {
        let before=self.selected_position;
        self.selected_position+=(self.picker_turn-self.selected_position)*(1.-(-dt/0.14).exp());
        if (self.selected_position-self.picker_turn).abs()<0.0005 {self.selected_position=self.picker_turn;}
        let labels_dirty=self.controls_dirty||before!=self.selected_position;
        self.controls_dirty=false;
        if labels_dirty {self.content.fill(0);}
        let turn=self.selected_position*std::f32::consts::PI;
        let orbit=age*1.1+turn;
        let mut balls=[(0.,0.,0.,0.,0.);2];
        for (index,ball) in balls.iter_mut().enumerate() {
            let phase=index as f32*std::f32::consts::PI;
            let emphasis=0.5+0.5*(turn+phase).cos();
            let (depth,x)=(orbit+phase).sin_cos();
            *ball=(30.+x*4.1,22.-depth*0.45,3.5+emphasis*1.1+depth*0.55,166.+emphasis*38.+depth*25.,depth);
        }
        let foreground=if balls[0].4>=balls[1].4 {0}else{1};
        for y in (13.*self.sample_scale) as i32..(31.*self.sample_scale) as i32 {
            let start=(y*self.rw+(20.*self.sample_scale) as i32) as usize;
            self.content[start..start+(20.*self.sample_scale) as usize].fill(0);
        }
        for index in [1-foreground,foreground] {
            let (cx,cy,radius,shade,_)=balls[index];
            let left=((cx-radius)*self.sample_scale-1.).floor() as i32;
            let right=((cx+radius)*self.sample_scale+1.).ceil() as i32;
            let top=((cy-radius)*self.sample_scale-1.).floor() as i32;
            let bottom=((cy+radius)*self.sample_scale+1.).ceil() as i32;
            let outer=(radius+0.5/self.sample_scale).powi(2);
            for y in top..bottom {for x in left..right {
                let dx=(x as f32+0.5)/self.sample_scale-cx;
                let dy=(y as f32+0.5)/self.sample_scale-cy;
                let squared=dx*dx+dy*dy;
                if squared>=outer {continue;}
                let alpha=(0.5-(squared.sqrt()-radius)*self.sample_scale).clamp(0.,1.);
                let light=(-dx-dy)*3.+(1.-squared/(radius*radius)).max(0.)*16.;
                let target=(y*self.rw+x) as usize;
                self.content[target]=over(self.content[target],(shade+light).clamp(0.,255.),alpha);
            }}
        }
        if !labels_dirty {return;}
        let base=self.selected_position.floor();let fraction=self.selected_position-base;
        let w=(94.*self.sample_scale) as i32;let h=(20.*self.sample_scale) as i32;
        for (turn,opacity,offset) in [(base,1.-fraction,-fraction*11.),(base+1.,fraction,(1.-fraction)*11.)] {
            if opacity<0.001 {continue;}
            let label=(turn as i32).rem_euclid(2) as usize;
            let ox=(44.*self.sample_scale).round() as i32;let oy=((12.+offset)*self.sample_scale).round() as i32;
            for y in 0..h {for x in 0..w {let px=ox+x;let py=oy+y;
                if py<(10.*self.sample_scale) as i32||py>=(34.*self.sample_scale) as i32||px<0||px>=self.rw {continue;}
                let index=(py*self.rw+px) as usize;
                self.content[index]=over(self.content[index],232.,self.labels[label][(y*w+x) as usize] as f32/255.*opacity);
            }}
        }
    }
    fn body_pixel(&self,index:usize,distance:f32)->u32 {
        let mask=(0.5-distance*self.sample_scale).clamp(0.,1.);
        let rim=(1.0-distance.abs()).clamp(0.,1.);
        let shade=self.body_shades[index]*(1.-rim*0.65)+self.rim_shades[index]*rim*0.65;
        premultiplied(shade,(0.82+rim*0.14)*mask)
    }
    // Translate canonical pixels at 1:1 scale: labels and card corners never stretch.
    fn composite_region(&mut self,bounds:(f32,f32,f32,f32),offset:(f32,f32),opacity:f32) {
        if opacity<=0. {return;}
        let scale=self.sample_scale;
        let (sl,sr,st,sb)=(bounds.0*scale,bounds.1*scale,bounds.2*scale,bounds.3*scale);
        let (dx,dy)=(offset.0*scale,offset.1*scale);
        let left=(sl+dx).floor().max(0.) as i32;
        let right=(sr+dx).ceil().min(self.rw as f32) as i32;
        let top=(st+dy).floor().max(0.) as i32;
        let bottom=(sb+dy).ceil().min(self.rh as f32) as i32;
        self.sampling_x.clear();
        for x in left..right {
            let px=x as f32-dx;
            self.sampling_x.push(if px<sl || px>=sr-1. || px<0. || px>=self.rw as f32-1. {(usize::MAX,0.)}else{(px as usize,px-px.floor())});
        }
        for y in top..bottom {
            let py=y as f32-dy;
            if py<st || py>=sb-1. || py<0. || py>=self.rh as f32-1. {continue;}
            let y0=py.floor() as usize;let fy=py-y0 as f32;
            for x in left..right {
                let destination=(y*self.rw+x) as usize;
                if self.frame[destination]>>24==0 {continue;}
                let (x0,fx)=self.sampling_x[(x-left) as usize];
                if x0==usize::MAX {continue;}
                let index=y0*self.rw as usize+x0;
                let pixels=[self.content[index],self.content[index+1],self.content[index+self.rw as usize],self.content[index+self.rw as usize+1]];
                if pixels.iter().all(|pixel|pixel>>24==0) {continue;}
                let interpolate=|shift:u32| {
                    let a=((pixels[0]>>shift)&255) as f32;let b=((pixels[1]>>shift)&255) as f32;
                    let c=((pixels[2]>>shift)&255) as f32;let d=((pixels[3]>>shift)&255) as f32;
                    let top=a+(b-a)*fx;top+(c+(d-c)*fx-top)*fy
                };
                self.frame[destination]=blend_content(self.frame[destination],interpolate(0),interpolate(24),opacity);
            }
        }
    }
    fn composite_options(&mut self) {
        self.composite_region((18.,140.,10.,34.),(0.,4.*(1.-self.content_progress)),self.content_progress);
    }
    fn write_dimensions(&self) {
        let value=format!("{{\"width\":{},\"height\":{},\"logical_width\":{},\"logical_height\":{},\"supersampling\":2,\"dpi\":{}}}",self.width,self.height,self.logical_width,self.logical_height,self.dpi);
        if let Some(path)=&self.preview_path { let _=std::fs::write(path.with_extension("json"),&value); }
        if let Some(dir)=&self.frame_dir { let _=std::fs::create_dir_all(dir); let _=std::fs::write(dir.join("dimensions.json"),value); }
    }
    pub unsafe fn cue(&mut self,_text:&str) {
        if self.recording { self.delivered_at=None;self.pop_level=0.;self.pop_from=0.;self.pipeline.set(crate::pipeline::Stage::Listening);self.cue_at=Instant::now();self.end_started=None;self.levels.fill(0.); }
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
            if !self.visible {self.rim_at=Instant::now();}
            self.transition_panel=if self.visible {self.panel_progress}else{0.};
            self.transition_content=if self.visible {self.content_progress}else{0.};
            self.animation=Instant::now();self.last_frame=Instant::now();self.visible=true;self.closing=false;self.selected_position=self.selected as f32;self.picker_turn=self.selected_position;self.controls_dirty=true;
        }
        self.animate();
    }
    pub unsafe fn redraw(&self) {}
    pub unsafe fn hide(&mut self) { if self.visible&&!self.closing {
        self.transition_panel=self.panel_progress;self.transition_content=self.content_progress;
        self.closing=true;self.animation=Instant::now();
    } }
    pub unsafe fn hide_for_capture(&mut self) {
        ShowWindow(self.hwnd,SW_HIDE);self.visible=false;self.closing=false;
        self.panel_progress=0.;self.content_progress=0.;self.transition_panel=0.;self.transition_content=0.;
    }
    pub fn is_visible(&self)->bool { self.visible }
    fn render_shell(&mut self,progress:f32,phase:f32) {
        let profile=|position:f32| { let d=position-phase; let d=if d<0. {d+1.}else{d};self.shine[((d*PROFILE as f32) as usize).min(PROFILE-1)] };
        let inset=9.;
        let growth=if self.picker {0.}else{(self.pop_level+self.processing_glow*0.16).min(1.)};
        let hw=15.+(self.logical_width as f32/2.-inset-15.)*progress+growth*3.;let hh=self.logical_height as f32/2.-inset+growth*1.4;let radius=hw.min(hh);
        if progress<0.999||growth>0.001 {
            self.frame.fill(0);
            let cx=self.logical_width as f32/2.;let inverse_scale=1./self.sample_scale;
            for y in 0..self.rh {
                let py=(y as f32+0.5)*inverse_scale-self.logical_height as f32/2.;
                let Some(extent)=row_extent(py,hw+7.,hh+7.,radius+7.) else {continue;};
                let left=((cx-extent)*self.sample_scale).floor().max(0.) as i32;
                let right=((cx+extent)*self.sample_scale).ceil().min(self.rw as f32) as i32;
                let row=(y*self.rw) as usize;
                self.frame[row+left as usize..row+right as usize].copy_from_slice(&self.background[row+left as usize..row+right as usize]);
                let inner=row_extent(py,hw-7.,hh-7.,radius-7.);
                let (inner_left,inner_right)=inner.map(|extent|(((cx-extent)*self.sample_scale).ceil() as i32,((cx+extent)*self.sample_scale).floor() as i32)).unwrap_or((right,right));
                // Interior shades are width-independent. Recompute only the
                // contour band where shape alpha and the moving rim can change.
                for range in [left..inner_left.min(right),inner_right.max(left)..right] {
                for x in range {
                    let px=(x as f32+0.5)*inverse_scale-cx;
                    let distance=capsule_distance(px,py,hw,hh,radius);let index=row+x as usize;
                    let mut pixel=self.body_pixel(index,distance);
                    let arc=if distance.abs()<7. {profile(perimeter(px,py,hw,hh,radius))}else{0.};
                    if distance.abs()<4.5 && pixel>>24>0 {
                        let alpha=(pixel>>24) as f32/255.;let shade=(pixel&255) as f32/alpha;
                        pixel=premultiplied(shade+arc*self.strength(distance),alpha);
                    }
                    self.frame[index]=self.glow_pixel(pixel,distance,arc);
                }
                }
            }
        } else {
            self.frame.copy_from_slice(&self.background);
            for edge in &self.edges {
                let pixel=self.frame[edge.index];let alpha=(pixel>>24) as f32/255.;let arc=profile(edge.position);
                let lit=if alpha>0. {premultiplied((pixel&255) as f32/alpha+arc*edge.strength,alpha)}else{pixel};
                self.frame[edge.index]=self.glow_pixel(lit,edge.distance,arc);
            }
        }
    }
    fn glow_pixel(&self,pixel:u32,distance:f32,arc:f32)->u32 {
        let strength=self.processing_glow+self.pop_level*0.65;
        if strength<0.001||distance.abs()>7. {return pixel;}
        let breath=self.glow_breath;
        let hotspot=arc.sqrt();
        let width=if distance<0. {4.4+breath+self.pop_level*0.15}else{2.8+breath*0.7+self.pop_level*0.2+hotspot*0.8};
        let limit=if distance<0. {7.}else{6.};
        let falloff=(-(distance/width).powi(2)).exp()*smooth((limit-distance.abs())/1.2);
        let intensity=(strength*(0.22+hotspot*0.58)).min(0.72);
        over(pixel,245.,falloff*intensity)
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
        let microphone=self.wave.back().copied().unwrap_or(0.);
        self.mic_level+=(microphone-self.mic_level)*(1.-(-dt/if microphone>self.mic_level {0.045}else{0.15}).exp());
        if self.picker && self.content_progress>0. {self.render_options(cue_age,dt);}
        let activity=self.pipeline.visual(now).0;
        self.pop_level=self.delivered_at.map(|at|delivery_pop(now.duration_since(at).as_secs_f32(),self.pop_from)).unwrap_or(0.);
        let busy=matches!(self.pipeline.stage,crate::pipeline::Stage::Recognizing|crate::pipeline::Stage::Decoded|crate::pipeline::Stage::Parsing);
        let breath=0.5+0.5*(self.rim_at.elapsed().as_secs_f32()*3.4).sin();self.glow_breath=breath;
        let glow=if self.picker {0.06+self.mic_level*0.9}else{(if self.recording {self.mic_level*0.24}else{0.})+if busy {0.12+0.10*breath}else{0.}};
        self.processing_glow+=(glow-self.processing_glow)*(1.-(-dt/if glow>self.processing_glow {0.08}else{0.20}).exp());
        self.render_shell(progress,(self.rim_at.elapsed().as_secs_f32()*0.36).fract());
        let spread=0.12+0.88*progress;
        // Smoothstep has zero slope at both ends; exponential settling continues
        // across the handoff instead of switching between max(intro, microphone).
        let handoff=smooth((cue_age-0.12)/0.85);
        let settle=1.-(-dt/0.085).exp();
        if self.picker {
            if self.content_progress>0. {self.composite_options();}
        } else {
        for i in 0..20 {
            let center_distance=((i as f32-9.5)/9.5).abs();let reveal=if self.closing {smooth((self.transition_panel-center_distance*0.42)/0.58)}else{smooth((progress-center_distance*0.42)/0.58)};
            let intro=if handoff<1. {0.20+0.28*((cue_age*6.-center_distance*3.).sin()*0.5+0.5)}else{0.};
            let target=if self.recording {intro*(1.-handoff)+self.wave[i]*handoff}else{self.levels[i]};
            self.levels[i]+=(target-self.levels[i])*settle;
            if (target-self.levels[i]).abs()<0.0005 {self.levels[i]=target;}
            let height=((3.+self.levels[i]*16.)*(0.86+progress*0.14)*(0.25+reveal*0.75)).max(1.8);
            let cx=self.logical_width as f32/2.+(i as f32-9.5)*4.6*spread;let cy=self.logical_height as f32/2.;let r=1.3;
            let min_x=((cx-r-1.)*self.sample_scale).floor().max(0.) as i32;
            let max_x=((cx+r+1.)*self.sample_scale).ceil().min(self.rw as f32) as i32;
            let min_y=((cy-height/2.-1.)*self.sample_scale).floor().max(0.) as i32;
            let max_y=((cy+height/2.+1.)*self.sample_scale).ceil().min(self.rh as f32) as i32;
            for y in min_y..max_y { for x in min_x..max_x {
                let distance=capsule_distance((x as f32+0.5)/self.sample_scale-cx,(y as f32+0.5)/self.sample_scale-cy,r,height/2.,r.min(height/2.));
                let alpha=(0.5-distance*self.sample_scale).clamp(0.,1.);
                let vertical=(y-min_y) as f32/(max_y-min_y).max(1) as f32;
                let shade=174.+(1.-center_distance)*53.+(1.-vertical)*24.;let index=(y*self.rw+x) as usize;
                let body_alpha=(self.frame[index]>>24) as f32/255.;
                self.frame[index]=over(self.frame[index],shade,alpha*0.98*reveal*body_alpha);
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
            let stage=if self.closing {"close"}else if self.recording&&t<1. {"open"}else if !self.recording {"ending"}else{activity.name()};
            let faded:Vec<u32>=output.iter().map(|p|{let a=((p>>24) as f32*progress).round() as u32;let c=((p&255) as f32*progress).round() as u32;c|c<<8|c<<16|a<<24}).collect();
            let _=std::fs::write(dir.join(format!("{:04}-{stage}.bgra",self.frame_index)),std::slice::from_raw_parts(faded.as_ptr() as *const u8,faded.len()*4));self.frame_index+=1;
        }
        if self.last_preview.elapsed().as_secs_f32()>0.15 {
            if let Some(path)=&self.preview_path {let _=std::fs::write(path,std::slice::from_raw_parts(self.pixels as *const u8,output.len()*4));}
            self.last_preview=now;
        }
    }
    pub fn amplitude(&mut self,level:f32) {let target=(((level-0.0015).max(0.)*45.).ln_1p()/2.674).clamp(0.,1.);let before=self.wave.back().copied().unwrap_or(0.);self.wave.pop_front();self.wave.push_back(before*0.55+target*0.45);}
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
    fn delivery_pop_rises_gently_is_continuous_and_fades_completely() {
        assert_eq!(delivery_pop(0.,0.),0.);assert_eq!(delivery_pop(0.13,0.),1.);
        assert!(delivery_pop(0.055,0.)<0.5);assert!(delivery_pop(0.26,0.)>0.7);
        assert!(delivery_pop(0.48,0.)<0.2);
        assert_eq!(delivery_pop(0.60,0.),0.);assert_eq!(delivery_pop(0.,0.65),0.65);
    }
    #[test]
    fn moving_glow_has_a_broader_hotspot_bleeds_inward_and_reacts_to_mic() {
        unsafe {
            let mut display=Display::new(std::ptr::null_mut());display.processing_glow=0.6;
            let base=premultiplied(25.,0.82);
            assert!(display.glow_pixel(base,3.,1.)&255>display.glow_pixel(base,3.,0.)&255);
            assert!(display.glow_pixel(base,-3.,1.)&255>base&255);
            assert_eq!(display.glow_pixel(0,6.,1.),0);
            display.set_picker(true);display.visible=true;display.recording=true;display.wave.iter_mut().for_each(|v|*v=1.);
            for _ in 0..30 {display.last_frame=Instant::now()-std::time::Duration::from_millis(16);display.animate();}
            let loud=display.processing_glow;
            display.wave.iter_mut().for_each(|v|*v=0.);
            for _ in 0..90 {display.last_frame=Instant::now()-std::time::Duration::from_millis(16);display.animate();}
            assert!(loud>0.6&&display.processing_glow<0.09);
        }
    }
    #[test]
    fn silent_microphone_bars_do_not_animate_for_processing_or_after_stop() {
        unsafe {
            let mut display=Display::new(std::ptr::null_mut());display.configure(96);display.visible=true;display.recording=true;
            display.animation=Instant::now()-std::time::Duration::from_secs(3);display.cue_at=display.animation;
            display.transition_panel=1.;display.transition_content=1.;
            for stage in [crate::pipeline::Stage::Listening,crate::pipeline::Stage::Recognizing,crate::pipeline::Stage::Parsing,crate::pipeline::Stage::Applied] {
                display.pipeline.set(stage);
                for _ in 0..20 {display.last_frame=Instant::now()-std::time::Duration::from_millis(16);display.animate();}
                assert!(display.levels.iter().all(|value|*value==0.),"Silent bars moved for {stage:?}");
            }
            for _ in 0..20 {display.amplitude(0.0008);}
            assert!(display.wave.iter().all(|value|*value==0.),"The visual noise floor must not look like speech");
            for _ in 0..20 {display.amplitude(0.03);}
            for _ in 0..30 {display.last_frame=Instant::now()-std::time::Duration::from_millis(16);display.animate();}
            assert!(display.levels.iter().any(|value|*value>0.1),"Real microphone levels should still move bars");
            display.recording=false;display.cue("");let frozen=display.levels;
            for _ in 0..20 {display.last_frame=Instant::now()-std::time::Duration::from_millis(16);display.animate();}
            assert_eq!(display.levels,frozen,"Stop should hold the wave shape for the panel close");
        }
    }
    #[test]
    fn pipeline_feedback_renders_ordered_nonblank_frames() {
        unsafe {
            let mut display=Display::new(std::ptr::null_mut());display.configure(96);display.visible=true;display.recording=true;
            display.animation=Instant::now()-std::time::Duration::from_secs(1);display.transition_panel=1.;display.transition_content=1.;
            display.pipeline.set(crate::pipeline::Stage::Recognizing);
            let frames=std::env::var_os("DICTATELY_ACTIVITY_FRAMES").map(std::path::PathBuf::from);
            if let Some(path)=&frames {std::fs::create_dir_all(path).unwrap();std::fs::write(path.join("dimensions.json"),format!("{{\"width\":{},\"height\":{}}}",display.width,display.height)).unwrap();}
            let mut seen=std::collections::HashSet::new();
            for frame in 0..100 {
                if frame==25 {display.pipeline.set(crate::pipeline::Stage::Decoded);display.pipeline.set(crate::pipeline::Stage::Parsing);}
                if frame==62 {display.pipeline.set(crate::pipeline::Stage::Applied);display.text_delivered();}
                std::thread::sleep(std::time::Duration::from_millis(8));display.animate();
                let stage=display.pipeline.visual(Instant::now()).0.name();seen.insert(stage);
                let pixels=std::slice::from_raw_parts(display.pixels,(display.width*display.height) as usize);
                assert!(pixels.iter().filter(|pixel|**pixel>>24>0).count()>1000);
                if let Some(path)=&frames {std::fs::write(path.join(format!("{frame:03}-{stage}.bgra")),std::slice::from_raw_parts(pixels.as_ptr().cast::<u8>(),pixels.len()*4)).unwrap();}
            }
            assert!(seen.contains("recognizing")&&seen.contains("decoded")&&seen.contains("parsing")&&seen.contains("applied"));
            if let Some(path)=&frames {
                display.recording=false;display.cue("");
                for frame in 0..12 {std::thread::sleep(std::time::Duration::from_millis(16));display.animate();
                    std::fs::write(path.join(format!("hold-{frame:02}.bgra")),std::slice::from_raw_parts(display.pixels.cast::<u8>(),(display.width*display.height*4) as usize)).unwrap();
                }
                display.hide();
                for frame in 0..20 {std::thread::sleep(std::time::Duration::from_millis(16));display.animate();
                    std::fs::write(path.join(format!("close-{frame:02}.bgra")),std::slice::from_raw_parts(display.pixels.cast::<u8>(),(display.width*display.height*4) as usize)).unwrap();
                }
            }
        }
    }

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
                    let inset=9.;
                    let hw=15.+(display.logical_width as f32/2.-inset-15.)*progress;
                    let hh=display.logical_height as f32/2.-inset;let radius=hw.min(hh);
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
    fn compact_picker_scrolls_in_both_directions_and_keeps_its_bounds() {
        unsafe {
            let mut display=Display::new(std::ptr::null_mut());display.set_picker(true);display.configure(96);
            assert_eq!((display.logical_width,display.logical_height),(156,44));
            assert_eq!(picker_option(78.,22.,1.,1.),Some(0));assert_eq!(picker_option(0.,0.,1.,1.),None);
            display.spin(1);assert_eq!(display.selected,1);display.spin(1);assert_eq!(display.selected,0);
            display.spin(-1);assert_eq!(display.selected,1);
            for _ in 0..100 {display.render_options(0.,0.016);}
            assert!((display.selected_position-display.picker_turn).abs()<0.001);
            assert!(display.content.iter().filter(|p|**p>>24>0).count()>150);
            let settled=display.content.clone();display.render_options(0.8,0.016);
            assert_ne!(settled,display.content,"The orbits should move after selection settles");
            for (index,pixel) in settled.iter().enumerate() {
                if index%display.rw as usize>=(44.*display.sample_scale) as usize {assert_eq!(*pixel,display.content[index],"Settled label pixels should stay cached");}
            }
            if let Some(path)=std::env::var_os("DICTATELY_PICKER_FRAMES") {
                let path=std::path::PathBuf::from(path);std::fs::create_dir_all(&path).unwrap();
                for frame in 0..8 {
                    display.render_options(frame as f32*0.65,0.016);
                    display.render_shell(1.,0.2);display.content_progress=1.;display.panel_progress=1.;display.composite_options();
                    std::fs::write(path.join(format!("{frame}.bgra")),std::slice::from_raw_parts(display.frame.as_ptr().cast::<u8>(),display.frame.len()*4)).unwrap();
                }
            }
            if let Some(path)=std::env::var_os("DICTATELY_PICKER_PREVIEW") {
                display.render_shell(1.,0.2);display.content_progress=1.;display.panel_progress=1.;display.composite_options();
                std::fs::write(path,std::slice::from_raw_parts(display.frame.as_ptr().cast::<u8>(),display.frame.len()*4)).unwrap();
            }
        }
    }
    #[test]
    fn stopping_keeps_the_rim_clock_running() {
        unsafe {
            let mut display=Display::new(std::ptr::null_mut());
            display.rim_at=Instant::now()-std::time::Duration::from_millis(830);
            let before=display.rim_at;
            display.recording=false;display.cue("");
            assert_eq!(display.rim_at,before);
            assert!(display.end_started.is_some());
            display.cue("");assert_eq!(display.rim_at,before);
        }
    }
}
