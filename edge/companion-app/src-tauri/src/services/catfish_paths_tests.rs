//! catfish_paths 的测试 (9/23 从 catfish_paths.rs 拆出: 那边加了 edge-runtime /
//! companion-state 的回落之后到了 805 行, 过了 800 行红线)。
//! 用 `#[path]` 挂在原模块下, `super::` 照旧摸得到私有函数。
use super::*;
use tempfile::TempDir;

/// 造一个"能执行"的文件。Unix 上必须真的 chmod +x —— is_executable
/// 查执行位, 只 write 出来的文件不算。
fn touch_exe(dir: &Path, name: &str) -> PathBuf {
    let p = dir.join(name);
    std::fs::write(&p, b"#!/bin/sh\nexit 0\n").unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    p
}

fn exts(v: &[&str]) -> Vec<String> {
    v.iter().map(|s| s.to_string()).collect()
}

// ── find_in_dirs: 核心查找逻辑 ────────────────────────────────

#[test]
fn finds_bare_name_unix_style() {
    let tmp = TempDir::new().unwrap();
    let want = touch_exe(tmp.path(), "catfish-email");
    let got = find_in_dirs("catfish-email", &[tmp.path().to_path_buf()], &[]);
    assert_eq!(got.as_deref(), Some(want.as_path()));
}

#[test]
fn finds_extension_when_bare_name_is_missing() {
    // 这条是本次修复的重点: Windows 上 pip 装出来的是 catfish-email.exe,
    // 无后缀的名字根本不存在, 而原来的代码只找无后缀名 + shell out
    // `which`, 两条都不成立。后缀是参数传进来的, 所以这个纯 Windows 的
    // 行为在 mac 上也测得到。
    //
    // ── 8/10 这条第一版是挂的, 值得留个记录 ──────────────────────
    //
    // 那一版造的是小写 `catfish-email.exe`, 搜的是大写 `.EXE`
    // (真实 PATHEXT 就是大写)。mac 上跑出来:
    //     left:  ".../catfish-email.EXE"   ← 我们拼出来的
    //     right: ".../catfish-email.exe"   ← 磁盘上真实的
    //
    // 挂的原因**不是没找到**: APFS 大小写不敏感, `.EXE` 确实命中了那个
    // 文件; find_in_dirs 返回的是拼接出来的路径而不是磁盘上的真名。功能
    // 上没问题 —— NTFS 同样不敏感, Command::new 照跑 —— 是断言把"找到了"
    // 和"拼写跟我造的一致"混成了一件事。
    //
    // 真正的隐患在第二层: 那一版的结果取决于**文件系统区不区分大小写**。
    // mac 上(断言写对的话)过, Linux CI 上会返 None 直接挂。"换台机器就翻"
    // 的测试比没有还糟。
    //
    // 所以这里造和搜用同一个大小写, 结论跟文件系统无关。
    // 小写文件 + 大写 PATHEXT 那条路能通, 靠的是 Windows 文件系统的性质,
    // 不是我们的代码 —— 不该由我们的单测去断言别人的行为。
    let tmp = TempDir::new().unwrap();
    let want = touch_exe(tmp.path(), "catfish-email.EXE");
    let got = find_in_dirs(
        "catfish-email",
        &[tmp.path().to_path_buf()],
        &exts(&[".COM", ".EXE"]),
    );
    assert_eq!(
        got.as_deref(),
        Some(want.as_path()),
        "带后缀的没找到 —— Windows 上 CLI 就只有这一个名字"
    );
}

#[test]
fn extension_order_is_respected() {
    // PATHEXT 的先后就是 Windows 的解析顺序。两个都在时必须挑排在前面的,
    // 不然行为跟 Windows 自己解析命令的结果对不上。
    let tmp = TempDir::new().unwrap();
    let first = touch_exe(tmp.path(), "catfish-email.COM");
    touch_exe(tmp.path(), "catfish-email.EXE");
    let got = find_in_dirs(
        "catfish-email",
        &[tmp.path().to_path_buf()],
        &exts(&[".COM", ".EXE"]),
    );
    assert_eq!(got.as_deref(), Some(first.as_path()), "后缀顺序没生效");
}

#[test]
fn bare_name_beats_extensions() {
    // Unix 上装的是无后缀名。万一同目录还有个同名带后缀的, 也得挑无后缀那个
    // —— exts 是"补充尝试", 不该盖过原名。
    let tmp = TempDir::new().unwrap();
    let bare = touch_exe(tmp.path(), "catfish-email");
    touch_exe(tmp.path(), "catfish-email.COM");
    let got = find_in_dirs(
        "catfish-email",
        &[tmp.path().to_path_buf()],
        &exts(&[".COM", ".EXE"]),
    );
    assert_eq!(got.as_deref(), Some(bare.as_path()));
}

#[test]
fn returns_none_when_nothing_matches() {
    let tmp = TempDir::new().unwrap();
    touch_exe(tmp.path(), "something-else");
    assert!(find_in_dirs("catfish-email", &[tmp.path().to_path_buf()], &exts(&[".EXE"])).is_none());
}

#[test]
fn earlier_dir_wins() {
    let a = TempDir::new().unwrap();
    let b = TempDir::new().unwrap();
    let first = touch_exe(a.path(), "catfish-email");
    touch_exe(b.path(), "catfish-email");
    let got = find_in_dirs(
        "catfish-email",
        &[a.path().to_path_buf(), b.path().to_path_buf()],
        &[],
    );
    assert_eq!(got.as_deref(), Some(first.as_path()), "候选顺序没生效");
}

#[test]
fn skips_empty_dir_entries() {
    // PATH 里的空段按 POSIX 是 cwd。不能从 cwd 捡二进制。
    let tmp = TempDir::new().unwrap();
    touch_exe(tmp.path(), "catfish-email");
    let dirs = [PathBuf::from(""), tmp.path().to_path_buf()];
    let got = find_in_dirs("catfish-email", &dirs, &[]);
    assert_eq!(got.as_deref(), Some(tmp.path().join("catfish-email").as_path()));
}

#[cfg(unix)]
#[test]
fn non_executable_file_is_not_picked() {
    let tmp = TempDir::new().unwrap();
    std::fs::write(tmp.path().join("catfish-email"), b"not executable").unwrap();
    assert!(
        find_in_dirs("catfish-email", &[tmp.path().to_path_buf()], &[]).is_none(),
        "没有执行位的同名文件被选中了 —— spawn 时才会报 Permission denied"
    );
}

// ── 环境相关 (要拿 env 锁, 见 util/test_env.rs) ────────────────

/// 改一个环境变量, drop 时**恢复原值**。
///
/// 第一版这几条测试是直接 `remove_var` 收尾的。问题是 HERMES_HOME 在
/// 开发机上完全可能本来就设着 (hermes 装在非默认位置) —— 无脑 remove
/// 等于把它对整个测试进程抹掉, 而后面 curator_config / curator_state /
/// memory_provider_config / identity_bundle / hermes_plugin /
/// codex_backend 六处都读这个变量, 会静静掉回 ~/.hermes。
///
/// 顺带解决 panic 泄漏: 断言失败时 remove_var 那一行根本不会执行,
/// 变量就留给后面所有测试了。Drop 在 unwind 时照跑。
///
/// 这个仓库为跨测试的 env 污染写过 util/test_env.rs 整整一篇, 不该再添一个。
struct EnvGuard {
    key: &'static str,
    old: Option<std::ffi::OsString>,
}

impl EnvGuard {
    fn set(key: &'static str, val: impl AsRef<std::ffi::OsStr>) -> Self {
        let old = std::env::var_os(key);
        std::env::set_var(key, val);
        Self { key, old }
    }

    fn unset(key: &'static str) -> Self {
        let old = std::env::var_os(key);
        std::env::remove_var(key);
        Self { key, old }
    }
}

impl Drop for EnvGuard {
    fn drop(&mut self) {
        match &self.old {
            Some(v) => std::env::set_var(self.key, v),
            None => std::env::remove_var(self.key),
        }
    }
}

#[test]
fn catfish_email_bin_honors_explicit_env() {
    let _guard = crate::util::test_env::env_lock();
    let tmp = TempDir::new().unwrap();
    let bin = touch_exe(tmp.path(), "catfish-email");
    let _e = EnvGuard::set("CATFISH_EMAIL_BIN", &bin);
    assert_eq!(catfish_email_bin().as_deref(), Some(bin.as_path()));
}

#[test]
fn catfish_email_bin_finds_it_in_hermes_venv() {
    // 复现 install.sh 的落点: <hermes>/hermes-agent/venv/bin/catfish-email
    let _guard = crate::util::test_env::env_lock();
    let tmp = TempDir::new().unwrap();
    let bin_dir = tmp.path().join("hermes-agent").join("venv").join("bin");
    std::fs::create_dir_all(&bin_dir).unwrap();
    let want = touch_exe(&bin_dir, "catfish-email");

    let _e1 = EnvGuard::unset("CATFISH_EMAIL_BIN");
    let _e2 = EnvGuard::set("HERMES_HOME", tmp.path());
    assert_eq!(catfish_email_bin().as_deref(), Some(want.as_path()));
}

#[test]
fn hermes_home_prefers_localappdata_over_dot_hermes() {
    // Windows 是 %LOCALAPPDATA%\hermes, 不是 ~/.hermes。
    // 不加 cfg(windows), 所以这条在 mac 上就能验。
    let _guard = crate::util::test_env::env_lock();
    let tmp = TempDir::new().unwrap();
    let hermes = tmp.path().join("hermes");
    std::fs::create_dir_all(&hermes).unwrap();

    let _e1 = EnvGuard::unset("HERMES_HOME");
    let _e2 = EnvGuard::set("LOCALAPPDATA", tmp.path());
    assert_eq!(hermes_home().as_deref(), Some(hermes.as_path()));
}

#[test]
fn hermes_home_env_wins_over_everything() {
    let _guard = crate::util::test_env::env_lock();
    let tmp = TempDir::new().unwrap();
    let _e = EnvGuard::set("HERMES_HOME", tmp.path());
    assert_eq!(hermes_home().as_deref(), Some(tmp.path()));
}

#[test]
fn env_guard_restores_previous_value() {
    // 守卫本身也要有人守 —— 写错了(比如存旧值那步漏了)的表现是"测试照样
    // 全绿, 但环境被悄悄改了", 跟没写一样。
    let _guard = crate::util::test_env::env_lock();
    const K: &str = "CATFISH_ENVGUARD_SELFTEST";

    std::env::set_var(K, "原值");
    {
        let _e = EnvGuard::set(K, "临时值");
        assert_eq!(std::env::var(K).as_deref(), Ok("临时值"));
    }
    assert_eq!(std::env::var(K).as_deref(), Ok("原值"), "没恢复回原值");

    {
        let _e = EnvGuard::unset(K);
        assert!(std::env::var_os(K).is_none());
    }
    assert_eq!(std::env::var(K).as_deref(), Ok("原值"), "unset 后没恢复");

    std::env::remove_var(K);
    {
        let _e = EnvGuard::set(K, "临时值");
    }
    assert!(
        std::env::var_os(K).is_none(),
        "本来没有的变量, 用完应该还是没有, 而不是留个空值"
    );
}

// ── 回归闸: 别再 shell out 到 which ─────────────────────────────

#[test]
fn email_code_does_not_shell_out_to_which() {
    // 这个 bug 的形态是"在 mac 上跑得好好的, Windows 上静默失效"。
    // 没有 Windows CI, 所以靠源码闸盯住 —— 谁再写回 `which` 会直接红。
    //
    // 只盯邮件这两个文件。commands/calendar.rs 里也有 which, 但那个找的是
    // catfish-calendar —— 一个装在 .app/Contents/Resources 里的 Swift
    // 二进制, macOS 专有, 它的 PATH 兜底在 Windows 上本来就不可达, 不是
    // 同一个问题。
    for (name, src) in [
        ("commands/email.rs", include_str!("../commands/email.rs")),
        ("services/email_scheduler.rs", include_str!("email_scheduler.rs")),
    ] {
        assert!(
            !src.contains("Command::new(\"which\")"),
            "{name} 里又出现了 `which` —— 那在 Windows 上不存在, \
             用 catfish_paths::catfish_email_bin() / find_on_path()"
        );
        assert!(
            !src.contains(".local/bin/catfish-email"),
            "{name} 里又写死了 .local/bin —— 那是 Unix 的路径约定, \
             Windows 上 CLI 在 venv 的 Scripts 目录"
        );
    }
}
