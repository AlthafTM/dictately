use std::time::Instant;
#[derive(Clone,Copy,Debug,PartialEq,Eq)]
pub enum Stage {Listening,Recognizing,Decoded,Parsing,Applied,Rejected}
impl Stage {pub fn name(self)->&'static str {match self {Self::Listening=>"listening",Self::Recognizing=>"recognizing",Self::Decoded=>"decoded",Self::Parsing=>"parsing",Self::Applied=>"applied",Self::Rejected=>"rejected"}}}
pub struct Pipeline {pub stage:Stage,result_at:Option<Instant>,at:Instant,parser:bool}
impl Default for Pipeline {fn default()->Self {Self{stage:Stage::Listening,result_at:None,at:Instant::now(),parser:false}}}
impl Pipeline {
    pub fn set(&mut self,stage:Stage) {
        let now=Instant::now();
        if stage==Stage::Listening {self.result_at=None;self.parser=false;}
        if stage==Stage::Recognizing&&self.stage!=stage {self.result_at=None;self.parser=false;}
        if stage==Stage::Decoded {self.result_at=Some(now);self.parser=false;}
        if stage==Stage::Parsing {self.parser=true;}
        if matches!(stage,Stage::Applied|Stage::Rejected)&&self.result_at.is_none() {self.result_at=Some(now);}
        if stage!=self.stage {self.at=now;self.stage=stage;}
    }
    pub fn visual(&self,now:Instant)->(Stage,f32) {
        let Some(start)=self.result_at else{return (self.stage,now.duration_since(self.at).as_secs_f32());};
        let age=now.duration_since(start).as_secs_f32();
        if age<0.16 {return (Stage::Decoded,age);}
        if self.parser&&(age<0.46||self.stage==Stage::Parsing) {return (Stage::Parsing,age-0.16);}
        if matches!(self.stage,Stage::Applied|Stage::Rejected) {
            let scheduled=(start+std::time::Duration::from_secs_f32(if self.parser {0.46}else{0.16})).max(self.at);
            let age=now.saturating_duration_since(scheduled).as_secs_f32();
            if age>=0.34 {return (Stage::Listening,0.);}
            return (self.stage,age);
        }
        (self.stage,now.duration_since(self.at).as_secs_f32())
    }
    // Keep completion feedback visible; this never delays transcription/editing.
    pub fn remaining(&self)->f32 {self.result_at.map(|at|{
        let end=at+std::time::Duration::from_secs_f32(if self.parser {0.8}else{0.5});
        let end=if matches!(self.stage,Stage::Applied|Stage::Rejected) {end.max(self.at+std::time::Duration::from_secs_f32(0.34))}else{end};
        end.saturating_duration_since(Instant::now()).as_secs_f32()
    }).unwrap_or(0.)}
}
#[cfg(test)]mod tests {
    use super::*;use std::time::Duration;
    #[test]fn fast_work_still_has_ordered_feedback_without_holding_work() {
        let mut p=Pipeline::default();p.set(Stage::Decoded);let start=p.result_at.unwrap();p.set(Stage::Parsing);p.set(Stage::Applied);
        assert_eq!(p.stage,Stage::Applied);
        assert_eq!(p.visual(start+Duration::from_millis(70)).0,Stage::Decoded);
        assert_eq!(p.visual(start+Duration::from_millis(250)).0,Stage::Parsing);
        assert_eq!(p.visual(start+Duration::from_millis(600)).0,Stage::Applied);
        assert_eq!(p.visual(start+Duration::from_millis(900)).0,Stage::Listening);
        p.set(Stage::Recognizing);assert_eq!(p.visual(Instant::now()).0,Stage::Recognizing);
    }
    #[test]fn real_waits_continue_until_the_parser_finishes() {
        let mut p=Pipeline::default();p.set(Stage::Decoded);let start=p.result_at.unwrap();p.set(Stage::Parsing);
        assert_eq!(p.visual(start+Duration::from_secs(8)).0,Stage::Parsing);
    }
}
