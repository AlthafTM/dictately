use std::{env, path::PathBuf, process::Command};
fn main() {
    println!("cargo:rerun-if-changed=transcribr.rc");
    println!("cargo:rerun-if-changed=../assets/transcribr.ico");
    let msvc = env::var("TARGET").unwrap().contains("msvc");
    let output = PathBuf::from(env::var("OUT_DIR").unwrap()).join(if msvc {"transcribr.res"} else {"transcribr-resource.o"});
    let status = if msvc {
        Command::new("rc.exe").args(["/nologo", "/fo"]).arg(&output).arg("transcribr.rc").status()
    } else {
        Command::new("windres").args(["-i", "transcribr.rc", "-O", "coff", "-o"]).arg(&output).status()
    }.expect("Resource compiler missing: use Developer PowerShell (MSVC) or install windres (GNU)");
    assert!(status.success(), "Could not embed application icon");
    println!("cargo:rustc-link-arg={}", output.display());
}
