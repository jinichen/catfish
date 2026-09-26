//! 9/26: 已装机器也要拿到 Windows 心跳补丁。
//!
//! `edge/hermes-fork/patch_windows_watchdog.py` 只在打 MSI 时改包里的 hermes 源码。
//! 但 Companion 升级时, hermes 只有在上游 commit 变了才重装 (hermes_install_steps
//! 按 pin 判断), 我们自己的补丁变了不会触发 —— 于是已经装过的 Windows 机器永远是
//! 未打补丁的 shutdown_watchdog.py, gateway.log 每次启动都报
//! `module 'asyncio' has no attribute 'start_unix_server'`。
//!
//! 这里在 Companion 启动时对已安装的源码做同一处替换 (文本与 Python 补丁逐字一致,
//! 幂等)。上游改了这段就不动, 只记日志 —— 跟打包时的 ValueError 同一个口径,
//! 不猜着改别人的代码。
use std::path::Path;

const OLD: &str = r#"        tick_server = await asyncio.start_unix_server(
            _tick_socket_handler, path=str(tick_socket_path)
        )"#;

const NEW: &str = r#"        # CATFISH-WINDOWS-WATCHDOG: preserve file heartbeat / UNKNOWN verdict.
        if os.name != "nt":
            tick_server = await asyncio.start_unix_server(
                _tick_socket_handler, path=str(tick_socket_path)
            )"#;

pub(crate) enum Outcome {
    Patched,
    AlreadyPatched,
    NotInstalled,
    UpstreamChanged,
}

pub(crate) fn patch_source(source: &str) -> Option<String> {
    if source.contains(NEW) {
        return Some(source.to_string());
    }
    (source.matches(OLD).count() == 1).then(|| source.replacen(OLD, NEW, 1))
}

pub(crate) fn ensure(hermes_agent_dir: &Path) -> std::io::Result<Outcome> {
    let path = hermes_agent_dir.join("gateway").join("shutdown_watchdog.py");
    let Ok(source) = std::fs::read_to_string(&path) else {
        return Ok(Outcome::NotInstalled);
    };
    if source.contains(NEW) {
        return Ok(Outcome::AlreadyPatched);
    }
    let Some(patched) = patch_source(&source) else {
        return Ok(Outcome::UpstreamChanged);
    };
    let tmp = path.with_extension("py.catfish-tmp");
    std::fs::write(&tmp, patched)?;
    std::fs::rename(&tmp, &path)?;
    // 旧字节码会被 Python 按 mtime 自动作废, 不用删 __pycache__。
    Ok(Outcome::Patched)
}

/// 返回是否真的改了文件 (上层据此决定要不要重启 hermes)。
pub(crate) fn ensure_logged(hermes_agent_dir: &Path) -> bool {
    match ensure(hermes_agent_dir) {
        Ok(Outcome::Patched) => {
            log::info!("[watchdog] 已给已安装的 Hermes 打上 Windows 心跳补丁");
            true
        }
        Ok(Outcome::AlreadyPatched) | Ok(Outcome::NotInstalled) => false,
        Ok(Outcome::UpstreamChanged) => {
            log::warn!("[watchdog] Hermes 上游 shutdown_watchdog.py 变了, Windows 心跳补丁没打, 需要复核 patch_windows_watchdog.py");
            false
        }
        Err(e) => {
            log::warn!("[watchdog] 写 Windows 心跳补丁失败: {e}");
            false
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn upstream() -> String {
        format!("async def loop_heartbeat_forever():\n    try:\n{OLD}\n    except Exception:\n        tick_server = None\n")
    }

    #[test]
    fn patches_once_and_is_idempotent() {
        let dir = std::env::temp_dir().join(format!("catfish-wd-{}", std::process::id()));
        std::fs::create_dir_all(dir.join("gateway")).unwrap();
        let file = dir.join("gateway").join("shutdown_watchdog.py");
        std::fs::write(&file, upstream()).unwrap();

        assert!(matches!(ensure(&dir).unwrap(), Outcome::Patched));
        let once = std::fs::read_to_string(&file).unwrap();
        assert!(once.contains("if os.name != \"nt\":"));
        assert!(matches!(ensure(&dir).unwrap(), Outcome::AlreadyPatched));
        assert_eq!(once, std::fs::read_to_string(&file).unwrap());
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn leaves_changed_upstream_alone() {
        assert!(patch_source("tick_server = something_else()").is_none());
        assert!(matches!(ensure(Path::new("/nonexistent-catfish")).unwrap(), Outcome::NotInstalled));
    }

    /// 与打包用的 Python 补丁逐字一致, 两边改一边就红。
    #[test]
    fn matches_python_patch_text() {
        let py = include_str!("../../../../hermes-fork/patch_windows_watchdog.py");
        assert!(py.contains(OLD), "OLD 与 patch_windows_watchdog.py 不一致");
        assert!(py.contains(NEW), "NEW 与 patch_windows_watchdog.py 不一致");
    }
}
