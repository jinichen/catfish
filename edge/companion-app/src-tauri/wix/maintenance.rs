//! Standalone std-only GUI helper embedded in MSI; runs even after installed files are gone.
#![cfg_attr(windows, windows_subsystem = "windows")]

#[cfg(windows)]
fn main() {
    use std::fs::OpenOptions;
    use std::io::Write;
    use std::os::windows::process::CommandExt;
    use std::process::{Command, Stdio};
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if args.len() != 2 { std::process::exit(2); }
    let Some(local) = std::env::var_os("LOCALAPPDATA") else { std::process::exit(2) };
    let root = std::path::PathBuf::from(local).join("CatfishMaintenance");
    std::fs::create_dir_all(&root).unwrap();
    let mut log = OpenOptions::new().create(true).append(true).open(root.join("lifecycle.log")).unwrap();
    let _ = writeln!(log, "MSI maintenance {:?}", args[0]);
    let shell = std::path::PathBuf::from(std::env::var_os("SystemRoot").unwrap())
        .join("System32/WindowsPowerShell/v1.0/powershell.exe");
    let script = format!("try {{\n{}\n}} catch {{ Write-Output $_; exit 1 }}", include_str!("windows-maintenance.ps1"));
    let mut child = Command::new(shell)
        .args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"])
        .arg(script)
        .env("CATFISH_MAINTENANCE_MODE", &args[0])
        .env("CATFISH_INSTALL_DIR", &args[1])
        .env("CATFISH_EXCLUDE_PID", "0")
        .creation_flags(0x0800_0000)
        .stdin(Stdio::null()).stdout(Stdio::from(log.try_clone().unwrap())).stderr(Stdio::from(log.try_clone().unwrap()))
        .spawn().unwrap();
    let started = std::time::Instant::now();
    loop {
        if let Some(status) = child.try_wait().unwrap() { std::process::exit(status.code().unwrap_or(1)); }
        if started.elapsed().as_secs() >= 60 {
            let _ = child.kill();
            let _ = child.wait();
            let _ = writeln!(log, "Maintenance timed out after 60 seconds; not reported as success");
            std::process::exit(1);
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
}

#[cfg(not(windows))]
fn main() { panic!("Windows MSI helper only"); }
