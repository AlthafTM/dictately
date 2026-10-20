//! Conservative spoken corrections to recently inserted dictation, never arbitrary editor text.
#[derive(Debug, PartialEq)]
pub struct Correction { pub start: usize, pub replacement: String }
fn tokens(text: &str) -> Vec<(usize, String)> {
    let mut result = Vec::new(); let mut start = None;
    for (i,c) in text.char_indices().chain(std::iter::once((text.len(),' '))) {
        if c.is_alphanumeric() || c == '\'' { if start.is_none() { start=Some(i); } }
        else if let Some(begin)=start.take() { result.push((begin,text[begin..i].to_lowercase())); }
    }
    result
}
fn body(text: &str) -> (&str,bool) {
    let lower=text.to_lowercase();
    for prefix in ["correction", "correct that", "i mean", "no i meant", "ralat", "maksud saya", "maksudnya"] {
        if lower.starts_with(prefix) && lower[prefix.len()..].chars().next().map(|c| !c.is_alphanumeric()).unwrap_or(true) {
            return (text[prefix.len()..].trim_start_matches(|c:char| c.is_whitespace() || ",:.-".contains(c)),true);
        }
    }
    (text,false)
}
pub fn might_correct(recent: &str, next: &str) -> bool {
    let (next, explicit)=body(next);
    if explicit { return true; }
    let before=tokens(recent); let after=tokens(next);
    after.len()>=2 && before.iter().enumerate().skip(before.len().saturating_sub(40)).any(|(i,_)|
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
pub fn plan(recent: &str, next: &str) -> Option<Correction> {
    let (body,explicit)=body(next.trim());
    let before=tokens(recent); let after=tokens(body);
    if before.is_empty() || after.is_empty() || after.len()>40 { return None; }
    // A marked one-word correction replaces only the last dictated word.
    if explicit && after.len()==1 {
        return Some(Correction {start:before.last()?.0,replacement:format!("{} ",body.trim())});
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
    best.map(|(start,_)|Correction{start,replacement:format!("{} ",body.trim())})
}
#[cfg(test)] mod tests {
    use super::*;
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
