//! Current-user single instance and legacy cleanup before any watchdog/bootstrap starts.
use fs2::FileExt;
use std::{fs::{File, OpenOptions}, path::Path};

pub fn lock(path: &Path) -> std::io::Result<Option<File>> {
    let file = OpenOptions::new().read(true).write(true).create(true).truncate(false).open(path)?;
    match file.try_lock_exclusive() {
        Ok(()) => Ok(Some(file)),
        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => Ok(None),
        Err(error) => Err(error),
    }
}

#[cfg(windows)]
pub fn initialize() -> anyhow::Result<Option<File>> {
    use std::io::Write;
    use std::process::Stdio;
    let root = std::path::PathBuf::from(std::env::var_os("LOCALAPPDATA")
        .ok_or_else(|| anyhow::anyhow!("缺少 LOCALAPPDATA"))?).join("CatfishMaintenance");
    std::fs::create_dir_all(&root)?;
    let Some(guard) = lock(&root.join("companion.lock"))? else {
        std::fs::write(root.join("activate"), b"show")?;
        return Ok(None);
    };
    let executable = std::env::current_exe()?;
    let script = format!("try {{\n{}\n}} catch {{ Write-Output $_; exit 1 }}", include_str!("../../wix/windows-maintenance.ps1"));
    let shell = std::path::PathBuf::from(std::env::var_os("SystemRoot")
        .ok_or_else(|| anyhow::anyhow!("缺少 SystemRoot"))?)
        .join("System32/WindowsPowerShell/v1.0/powershell.exe");
    let mut log = OpenOptions::new().create(true).append(true).open(root.join("lifecycle.log"))?;
    let mut child = super::process::background_command(shell)
        .args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"])
        .arg(script)
        .env("CATFISH_MAINTENANCE_MODE", "startup")
        .env("CATFISH_INSTALL_DIR", executable.parent().unwrap())
        .env("CATFISH_EXCLUDE_PID", std::process::id().to_string())
        .stdout(Stdio::from(log.try_clone()?)).stderr(Stdio::from(log.try_clone()?))
        .spawn()?;
    let started = std::time::Instant::now();
    let status = loop {
        if let Some(status) = child.try_wait()? { break status; }
        if started.elapsed().as_secs() >= 60 {
            let _ = child.kill();
            let _ = child.wait();
            writeln!(log, "Startup maintenance timed out after 60 seconds")?;
            anyhow::bail!("旧运行环境检查超时，请查看 {}", root.join("lifecycle.log").display());
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
    };
    anyhow::ensure!(status.success(), "旧运行环境清理失败，请查看 {}", root.join("lifecycle.log").display());
    Ok(Some(guard))
}

#[cfg(windows)]
pub fn listen_for_activation(app: tauri::AppHandle) {
    use tauri::Manager;
    let Some(local) = std::env::var_os("LOCALAPPDATA") else { return };
    let path = std::path::PathBuf::from(local).join("CatfishMaintenance/activate");
    tauri::async_runtime::spawn(async move {
        loop {
            tokio::time::sleep(std::time::Duration::from_millis(500)).await;
            if path.is_file() && std::fs::remove_file(&path).is_ok() {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.unminimize();
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn excludes_second_instance_and_releases_after_exit() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lock");
        let first = lock(&path).unwrap().unwrap();
        assert!(lock(&path).unwrap().is_none());
        drop(first);
        assert!(lock(&path).unwrap().is_some());
    }
}
