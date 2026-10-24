//! Conservative spoken corrections to recently inserted dictation, never arbitrary editor text.
#[derive(Debug, PartialEq)]
pub struct Correction { pub start: usize, pub end:usize, pub replacement: String }
fn tokens(text: &str) -> Vec<(usize, String)> {
    let mut result = Vec::new(); let mut start = None;
    for (i,c) in text.char_indices().chain(std::iter::once((text.len(),' '))) {
        if c.is_alphanumeric() || c == '\'' { if start.is_none() { start=Some(i); } }
        else if let Some(begin)=start.take() { result.push((begin,text[begin..i].to_lowercase())); }
    }
    result
}
pub fn body(text: &str) -> (&str,bool) {
    let lower=text.to_ascii_lowercase();
    for prefix in ["correction", "correct that", "i mean", "no i meant", "ralat", "maksud saya", "maksudnya"] {
        if lower.starts_with(prefix) && lower[prefix.len()..].chars().next().map(|c| !c.is_alphanumeric()).unwrap_or(true) {
            return (text[prefix.len()..].trim_start_matches(|c:char| c.is_whitespace() || ",:.-".contains(c)),true);
        }
    }
    (text,false)
}
pub fn might_correct(recent: &str, next: &str) -> bool {
    let (next, explicit)=body(next);
    if explicit||named(next).is_some() { return true; }
    let before=tokens(recent); let after=tokens(next);
    after.len()>=2 && before.iter().enumerate().any(|(i,_)|
        before.get(i+1).is_some() && before[i].1==after[0].1 && before[i+1].1==after[1].1)
}
fn distance(a: &[&str], b: &[&str]) -> usize {
    let mut row: Vec<usize>=(0..=b.len()).collect();
    for (i,left) in a.iter().enumerate() {
        let mut diagonal=row[0]; row[0]=i+1;
        for (j,right) in b.iter().enumerate() {
            let old=row[j+1]; row[j+1]=(row[j]+1).min(old+1).min(diagonal+usize::from(left!=right)); diagonal=old;
        }
    }
    row[b.len()]
}
pub fn explicit(text:&str)->bool {body(text.trim()).1||named(text.trim()).is_some()}
fn named(text:&str)->Option<(&str,&str)> {
    let lower=text.to_ascii_lowercase();
    for (prefix,join) in [("replace "," with "),("change "," to "),("ganti "," dengan "),("ubah "," menjadi ")] {
        if lower.starts_with(prefix) {if let Some(at)=lower.find(join) {return Some((text[prefix.len()..at].trim_matches(|c:char|c.is_whitespace()||c=='"'),text[at+join.len()..].trim()));}}
    }
    None
}
pub fn plan(recent: &str, next: &str) -> Option<Correction> {
    if let Some((old,new))=named(next.trim()) {
        let needle=tokens(old);let hay=tokens(recent);
        if needle.is_empty()||new.is_empty()||needle.len()>40 {return None;}
        let matches:Vec<_>=hay.windows(needle.len()).enumerate().filter(|(_,span)|span.iter().zip(&needle).all(|(a,b)|a.1==b.1)).map(|(i,_)|i).collect();
        if matches.len()!=1 {return None;}
        let i=matches[0];let last=i+needle.len()-1;
        let end=hay[last].0+recent[hay[last].0..].chars().take_while(|c|c.is_alphanumeric()||*c=='\'').map(char::len_utf8).sum::<usize>();
        return Some(Correction{start:hay[i].0,end,replacement:new.trim_end_matches('.').to_string()});
    }
    let (body,explicit)=body(next.trim());
    let before=tokens(recent); let after=tokens(body);
    if before.is_empty() || after.is_empty() || after.len()>40 { return None; }
    // A marked one-word correction replaces only the last dictated word.
    if explicit && after.len()==1 {
        return Some(Correction {start:before.last()?.0,end:recent.len(),replacement:format!("{} ",body.trim())});
    }
    if after.len()<4 && !explicit { return None; }
    let after_words:Vec<_>=after.iter().map(|(_,w)|w.as_str()).collect();
    let before_words:Vec<_>=before.iter().map(|(_,w)|w.as_str()).collect();
    let mut best:Option<(usize,usize)>=None;
    for i in before.len().saturating_sub(after.len()+2)..before.len() {
        let source=&before_words[i..];
        if source.len().abs_diff(after.len())>2 || source.len()<3 { continue; }
        let edits=distance(&source,&after_words);
        if edits==0 { continue; } // Ordinary intentional repetition is preserved.
        let common=source.iter().zip(&after_words).filter(|(a,b)|a==b).count();
        let anchored=source.get(0)==after_words.get(0) && source.get(1)==after_words.get(1);
        if edits>if explicit { 3 } else { (after.len()/4).max(1) }
            || common<2 || (!explicit && !anchored) { continue; }
        if best.map(|(_,score)|edits<score).unwrap_or(true) { best=Some((before[i].0,edits)); }
    }
    if let Some((start,_))=best {return Some(Correction{start,end:recent.len(),replacement:format!("{} ",body.trim())});}
    let mut candidates=Vec::new();
    for i in 0..before.len() {
        if before.get(i).map(|v|v.1.as_str())!=after_words.first().copied()||before.get(i+1).map(|v|v.1.as_str())!=after_words.get(1).copied() {continue;}
        let end=i+after.len();if end>before.len() {continue;}
        let edits=distance(&before_words[i..end],&after_words);
        if edits==0||edits>(after.len()/5).max(1) {continue;}
        let stop=if end==before.len() {recent.len()}else{before[end].0};
        candidates.push(Correction{start:before[i].0,end:stop,replacement:format!("{} ",body.trim())});
    }
    if candidates.len()==1 {candidates.pop()}else{None}
}
#[cfg(test)] mod tests {
    use super::*;
    #[test] fn explicit_range_repairs_preserve_later_text_and_reject_ambiguity() {
        let text="Send the blue report tomorrow. Then call Alice. ";let fix=plan(text,"replace blue with green").unwrap();
        let mut updated=text.to_string();updated.replace_range(fix.start..fix.end,&fix.replacement);
        assert_eq!(updated,"Send the green report tomorrow. Then call Alice. ");
        assert!(plan("blue blue ","replace blue with green").is_none());assert!(explicit("replace blue with green"));
        assert!(plan("hello ","correction").is_none());assert!(explicit("correction"));
        let old="I want to sand the report. Please call Alice. ";let fix=plan(old,"I want to send the report.").unwrap();
        let mut updated=old.to_string();updated.replace_range(fix.start..fix.end,&fix.replacement);assert_eq!(updated,"I want to send the report. Please call Alice. ");
    }
    #[test] fn repeat_repairs_typo() {
        let old="Earlier sentence. I want to sand the report tomorrow. ";
        let fix=plan(old,"I want to send the report tomorrow.").unwrap();
        assert_eq!(&old[fix.start..],"I want to sand the report tomorrow. ");
        assert_eq!(fix.replacement,"I want to send the report tomorrow. ");
        assert!(might_correct(old,"I want to send"));
    }
    #[test] fn indonesia_and_explicit_correction() {
        assert!(plan("Saya ingin kirim laporan hari rabu. ","Saya ingin kirim laporan hari sabtu.").is_some());
        assert_eq!(plan("Meeting on Monday. ","Correction, Tuesday.").unwrap().replacement,"Tuesday. ");
        assert!(plan("Saya pergi hari Senin. ","Ralat, Selasa.").is_some());
    }
    #[test] fn followup_and_repetition_are_not_edits() {
        for next in ["Please include the latest numbers.","I want to send the report tomorrow.","Tomorrow is good."] {
            assert!(plan("I want to send the report tomorrow. ",next).is_none(),"{next}");
        }
        assert!(plan("", "Correction, Tuesday.").is_none());
    }
}
