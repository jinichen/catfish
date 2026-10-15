use std::path::{Path, PathBuf};

/// 本机能用的浏览器 (mac / Windows / Linux 全平台), 按优先级 (10/16): 首次启动时 chrome_runtime 依次试, 起不来 (比如被安全软件
/// 拦了, 一启动就退) 就换下一个, 试成功的记下来下次直接用。
/// 设了 `CATFISH_CHROME_BIN` 就只用它 —— 显式指定的不替员工换。
///
/// BL-WIN3 (5/8): Windows 候选 —— Per-user 安装 (%LOCALAPPDATA%\Google), 标准 Program Files
/// (双架构), Edge 最后 (Win11 自带, Chromium 内核)。通过环境变量查 (LOCALAPPDATA / ProgramFiles
/// / ProgramFiles(x86)) 免硬编盘符。
pub fn browser_candidates() -> Vec<PathBuf> {
    // 1. env 强制覆盖 (员工 / 开发者用)
    if let Ok(custom) = std::env::var("CATFISH_CHROME_BIN") {
        let p = PathBuf::from(custom);
        if p.is_file() {
            return vec![p];
        }
    }
    let mut found: Vec<PathBuf> = Vec::new();

    // Prefer the shipped browser on Windows: it is tested with this release.
    #[cfg(windows)]
    if let Some(root) = std::env::var_os("LOCALAPPDATA") {
        if let Some(browser) = bundled_chromium(&PathBuf::from(root).join("ms-playwright")) {
            found.push(browser);
        }
    }

    // 2. 平台候选清单
    let mut candidates: Vec<PathBuf> = vec![
        // macOS
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome".into(),
        "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta".into(),
        "/Applications/Google Chrome Dev.app/Contents/MacOS/Google Chrome Dev".into(),
        "/Applications/Chromium.app/Contents/MacOS/Chromium".into(),
    ];
    if let Some(home) = super::catfish_paths::home_dir() {
        // mac per-user: ~/Applications/Google Chrome.app/...
        candidates.push(home.join("Applications/Google Chrome.app/Contents/MacOS/Google Chrome"));
    }

    // Windows
    if let Ok(local_appdata) = std::env::var("LOCALAPPDATA") {
        // Per-user 安装 (现在 Windows 主流)
        candidates.push(PathBuf::from(&local_appdata).join("Google/Chrome/Application/chrome.exe"));
        candidates.push(PathBuf::from(&local_appdata).join("Google/Chrome SxS/Application/chrome.exe")); // Canary
    }
    if let Ok(pf) = std::env::var("ProgramFiles") {
        candidates.push(PathBuf::from(&pf).join("Google/Chrome/Application/chrome.exe"));
        // Edge 作为 fallback (Win11 自带 Chromium 内核)
        candidates.push(PathBuf::from(&pf).join("Microsoft/Edge/Application/msedge.exe"));
    }
    if let Ok(pf86) = std::env::var("ProgramFiles(x86)") {
        candidates.push(PathBuf::from(&pf86).join("Google/Chrome/Application/chrome.exe"));
        candidates.push(PathBuf::from(&pf86).join("Microsoft/Edge/Application/msedge.exe"));
    }
    // Hard-coded fallback (env 没设的极端情况)
    candidates.push("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe".into());
    candidates.push("C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe".into());

    // Linux (开发用, 非主目标)
    candidates.push("/usr/bin/google-chrome".into());
    candidates.push("/usr/bin/google-chrome-stable".into());
    candidates.push("/usr/bin/chromium".into());
    candidates.push("/usr/bin/chromium-browser".into());

    // macOS 上 Edge 也是 Chromium 内核, 放最后兜底
    candidates.push("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge".into());

    for c in candidates.into_iter().filter(|p| p.is_file()) {
        if !found.contains(&c) {
            found.push(c);
        }
    }
    found
}

/// Edge 跟 Chromium / Chrome 不能共用配置目录 (版本对不上会拒开或写坏), 启动方据此分开放。
pub fn is_edge(binary: &Path) -> bool {
    // 按 / 和 \ 都切: Windows 路径在单测 (跑在 mac / Linux) 里也得认得出
    let full = binary.to_string_lossy().to_lowercase();
    let name = full.rsplit(['/', '\\']).next().unwrap_or_default();
    name.starts_with("msedge") || name == "microsoft edge"
}


#[cfg(any(windows, test))]
fn bundled_chromium(root: &Path) -> Option<PathBuf> {
    let mut builds: Vec<_> = std::fs::read_dir(root).ok()?.flatten().filter_map(|entry| {
        let name = entry.file_name().to_string_lossy().into_owned();
        let revision: u32 = name.strip_prefix("chromium-")?.parse().ok()?;
        Some((revision, entry.path()))
    }).collect();
    builds.sort_by(|a, b| b.0.cmp(&a.0));
    builds.into_iter().find_map(|(_, dir)| {
        ["chrome-win64/chrome.exe", "chrome-win/chrome.exe"].iter()
            .map(|suffix| dir.join(suffix)).find(|path| path.is_file())
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn shipped_browser_uses_newest_complete_build() {
        let root = tempfile::tempdir().unwrap();
        let browser = root.path().join("chromium-1200/chrome-win64/chrome.exe");
        std::fs::create_dir_all(browser.parent().unwrap()).unwrap();
        std::fs::write(&browser, b"fixture").unwrap();
        std::fs::create_dir_all(root.path().join("chromium-1300")).unwrap();
        std::fs::create_dir_all(root.path().join("chromium_headless_shell-9999")).unwrap();
        assert_eq!(bundled_chromium(root.path()), Some(browser));
    }

    #[test]
    fn edge_is_recognized_on_both_platforms() {
        assert!(is_edge(Path::new(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")));
        assert!(is_edge(Path::new("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")));
        assert!(!is_edge(Path::new(r"C:\x\ms-playwright\chromium-1248\chrome-win64\chrome.exe")));
        assert!(!is_edge(Path::new("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")));
    }
}
