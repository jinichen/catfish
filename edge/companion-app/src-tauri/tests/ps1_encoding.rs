//! 9/24 (1.0.14 Windows 现场): install-catfish-email.ps1 加了几句中文, 文件是
//! 无 BOM 的 UTF-8。Windows PowerShell 5.1 按系统 ANSI 代码页 (中文系统 = GBK)
//! 读无 BOM 脚本, 中文把字符串的收尾引号「吃掉」, 整个脚本解析失败, 一行都没跑。
//! 以前的中文碰巧没撞上, 所以一直没暴露。
//!
//! 规则: Companion 在 Windows 上会执行的 .ps1 只能是纯 ASCII, 或者带 UTF-8 BOM。
//! 这里在 mac/linux 的 cargo test 里就拦住, 不用等到 Windows 现场。

use std::path::{Path, PathBuf};

fn ps1_files(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            ps1_files(&path, out);
        } else if path.extension().is_some_and(|e| e.eq_ignore_ascii_case("ps1")) {
            out.push(path);
        }
    }
}

#[test]
fn windows_powershell_scripts_are_ascii_or_have_bom() {
    let manifest = Path::new(env!("CARGO_MANIFEST_DIR"));
    let mut files = Vec::new();
    // resources/windows: 装进 MSI、首启时执行; wix: MSI 维护脚本; ../scripts: 本机打包/测试脚本
    for dir in ["resources", "wix", "../scripts"] {
        ps1_files(&manifest.join(dir), &mut files);
    }
    assert!(
        files.iter().any(|p| p.ends_with("install-catfish-email.ps1")),
        "没扫到 install-catfish-email.ps1, 扫描目录写错了: {files:?}"
    );

    let bad: Vec<String> = files
        .iter()
        .filter(|path| {
            let bytes = std::fs::read(path).expect("read ps1");
            !bytes.starts_with(&[0xEF, 0xBB, 0xBF]) && !bytes.is_ascii()
        })
        .map(|p| p.display().to_string())
        .collect();
    assert!(
        bad.is_empty(),
        "这些 .ps1 含非 ASCII 字符但没有 UTF-8 BOM, Windows PowerShell 5.1 会按 GBK 读坏: {bad:#?}"
    );
}
