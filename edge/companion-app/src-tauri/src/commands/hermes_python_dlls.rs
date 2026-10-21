//! Windows 解内嵌 Python 时, 安全软件拦下 VC 运行库 DLL 的处理 (10/21)。
//!
//! # 病
//!
//! 10/21 一台 Windows (yujz) 首次设置失败:
//!
//! ```text
//! 解压内嵌 Python 失败 (exit code: 1)
//! vcruntime140.dll: Can't create '\\?\C:\Users\…\hermes\.catfish-python-stage-13300\vcruntime140.dll': No such file or directory
//! vcruntime140_1.dll: Can't create '…\vcruntime140_1.dll': No such file or directory
//! tar: Error exit delayed from previous errors
//! ```
//!
//! 暂存目录是先建好再解的 (ensure_embedded_python), 包里 2132 个文件其余 2130 个都写进去了,
//! "目录不存在" 说不通; 失败的恰好是两个跟系统运行库同名的 DLL —— 安全软件 (360 / 火绒 /
//! Defender 之类) 的 "DLL 劫持防护" 拦的正是往用户目录写这种名字。拦下时 tar 拿到的错误码
//! 被翻成了 "No such file or directory"。10/16 也有台机器自带 Chromium 一启动就被安全软件掐掉。
//!
//! # 做法
//!
//! - 只有这两个 DLL 没写进去、而 `System32` 里本来就有它们 (绝大多数 Windows 都有) →
//!   继续装。Python 找不到自己目录里的 vcruntime 时, Windows 加载器会去 System32 找;
//!   后面 `python_is_wanted` 真跑一次解释器, 起不来照样报错, 不会带病往下走。
//! - System32 也没有 → 报一句人看得懂的: 被安全软件拦了, 把哪个目录加信任 / 或装 VC++ 运行库。
//! - 失败里混着别的文件 → 不是这回事, 原样报 tar 的错, 不吞。

use std::path::{Path, PathBuf};

/// Python 依赖的、会被安全软件当成"劫持"拦下的 VC 运行库。小写比对。
const RUNTIME_DLLS: [&str; 2] = ["vcruntime140.dll", "vcruntime140_1.dll"];

/// tar 的 stderr 里, 写不进去的文件**全是** VC 运行库时, 返回这些文件名; 否则 None。
///
/// 认两种行: `<名字>: Can't create '<路径>': …` (每个失败文件一行) 和 tar 最后那句
/// `Error exit delayed from previous errors`。出现任何别的错误行都返回 None —— 那是
/// 另一种失败, 不能被这条路吞掉。
pub(crate) fn blocked_runtime_dlls(stderr: &str) -> Option<Vec<String>> {
    let mut blocked: Vec<String> = Vec::new();
    for line in stderr.lines().map(str::trim).filter(|l| !l.is_empty()) {
        if line.contains("Error exit delayed from previous errors") {
            continue;
        }
        let name = line.split(": Can't create ").next().filter(|_| line.contains(": Can't create "))?;
        let file = Path::new(name.trim()).file_name()?.to_string_lossy().to_lowercase();
        if !RUNTIME_DLLS.contains(&file.as_str()) {
            return None;
        }
        if !blocked.contains(&file) {
            blocked.push(file);
        }
    }
    (!blocked.is_empty()).then_some(blocked)
}

/// 这些 DLL 在 `system_dir` 里是不是都有。
pub(crate) fn all_present_in(system_dir: &Path, dlls: &[String]) -> bool {
    dlls.iter().all(|d| system_dir.join(d).is_file())
}

/// Windows 的 System32 (SystemRoot / WINDIR 环境变量, 都没有就 C:\Windows)。
pub(crate) fn system32() -> PathBuf {
    std::env::var_os("SystemRoot")
        .or_else(|| std::env::var_os("WINDIR"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(r"C:\Windows"))
        .join("System32")
}

/// System32 也没有时给员工 / IT 看的话。`hermes_home` 是要加信任的目录。
pub(crate) fn blocked_message(dlls: &[String], hermes_home: &Path, tar_stderr: &str) -> String {
    format!(
        "解压内嵌 Python 时 {} 写不进去 —— 多半被安全软件 (360 / 火绒 / Windows Defender 等) 的\
         「DLL 劫持防护」拦了, 而这台电脑的系统目录里也没有这两个 VC 运行库可用。\n\
         二选一后点「重新尝试」:\n\
         · 在安全软件里把 {} 加入信任目录 (或查看它的拦截记录, 恢复并信任这两个文件)\n\
         · 安装微软 Visual C++ 2015-2022 运行库 (x64)\n\n\
         原始错误:\n{}",
        dlls.join("、"),
        hermes_home.display(),
        tar_stderr.trim()
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    const YUJZ: &str = "vcruntime140.dll: Can't create '\\\\?\\C:\\Users\\yujz\\AppData\\Local\\hermes\\.catfish-python-stage-13300\\vcruntime140.dll': No such file or directory\n\
vcruntime140_1.dll: Can't create '\\\\?\\C:\\Users\\yujz\\AppData\\Local\\hermes\\.catfish-python-stage-13300\\vcruntime140_1.dll': No such file or directory\n\
tar: Error exit delayed from previous errors\n";

    #[test]
    fn 认出_yujz_那次_只有两个运行库被拦() {
        assert_eq!(
            blocked_runtime_dlls(YUJZ),
            Some(vec!["vcruntime140.dll".to_string(), "vcruntime140_1.dll".to_string()])
        );
    }

    #[test]
    fn 混着别的文件写不进去_不是这回事() {
        let s = format!("{YUJZ}python314.dll: Can't create 'x\\python314.dll': Permission denied\n");
        assert_eq!(blocked_runtime_dlls(&s), None);
    }

    #[test]
    fn 别的_tar_错误_不吞() {
        assert_eq!(blocked_runtime_dlls("tar: Error opening archive: Unrecognized archive format\n"), None);
        assert_eq!(blocked_runtime_dlls(""), None);
        assert_eq!(blocked_runtime_dlls("tar: Error exit delayed from previous errors\n"), None);
    }

    #[test]
    fn 大小写和带目录前缀都认() {
        let s = "DLLs/VCRUNTIME140.DLL: Can't create 'x': No such file or directory\n";
        assert_eq!(blocked_runtime_dlls(s), Some(vec!["vcruntime140.dll".to_string()]));
    }

    #[test]
    fn 系统目录里齐不齐() {
        let dir = tempfile::tempdir().unwrap();
        let dlls = vec!["vcruntime140.dll".to_string(), "vcruntime140_1.dll".to_string()];
        assert!(!all_present_in(dir.path(), &dlls));
        std::fs::write(dir.path().join("vcruntime140.dll"), b"x").unwrap();
        assert!(!all_present_in(dir.path(), &dlls), "缺一个也不算齐");
        std::fs::write(dir.path().join("vcruntime140_1.dll"), b"x").unwrap();
        assert!(all_present_in(dir.path(), &dlls));
    }

    #[test]
    fn 报错说清楚加信任的目录和两条出路() {
        let m = blocked_message(&["vcruntime140.dll".into()], Path::new(r"C:\Users\yujz\AppData\Local\hermes"), YUJZ);
        assert!(m.contains("安全软件") && m.contains(r"C:\Users\yujz\AppData\Local\hermes"));
        assert!(m.contains("Visual C++") && m.contains("重新尝试") && m.contains("原始错误"));
    }
}
