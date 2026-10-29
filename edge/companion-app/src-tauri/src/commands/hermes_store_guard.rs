//! 10/29: hermes 登记了自己管理的 Python, 却没建好依赖环境 → 把登记挪开, 网关回到 venv 那套。
//!
//! # 病
//!
//! 鸿波 Windows 机 10/26 12:18 在管理员窗口里跑过一次 `hermes pm repair`。它下载了 hermes 自己
//! 管理的 Python (`hermes\tools\python-3.14.7+…`), 在 `hermes\tools\facts.json` 里登记「以后用这份」,
//! 但依赖环境那一步没做完 —— `hermes\installs\<key>\facts.json` 一条都没有。
//!
//! 从那以后 hermes 的 `get_python_path()` 按登记返回这份 Python: `gateway start`、开机自启脚本
//! (Companion 每次启动用 hermes 自己的 `_write_task_script()` 重写的) 全用它。而它启动时
//! `pm.environments.activate_dependencies` 找不到已提交的依赖环境, 直接
//! `no dependency environment is committed for this install` 退出 —— 网关起来就退, 还没开始写
//! gateway.log。10/28 起小鲶不回话, 手动把 tools\facts.json 改名后 `gateway start` 立刻好了。
//!
//! # 做法
//!
//! 用 hermes 自己的函数判断: `resolve_store_python()` 有登记、`committed_venv()` 却是 None
//! (或记录读不了) —— 这时那份 Python **必然**启动失败 (`_require_own_dependencies` 无条件 raise),
//! 所以挪开不会误伤能用的环境。登记文件改名为 `facts.json.uncommitted-<时间>` (不删, 改回原名即可撤销),
//! hermes 就退回调用它的 venv Python, 跟 10/26 之前一样。之后真正跑完的 `pm repair` / 升级会重新写登记。
//!
//! 只读 hermes 的程序状态文件, 不碰用户数据。

use serde::Deserialize;

/// 在 hermes 的 venv 里跑 (cwd = hermes-agent, 所以 `pm` 包可导入)。只用 hermes 自己的函数。
const GUARD_PY: &str = r#"
import json, time
from pathlib import Path
out = {"store_python": None, "set_aside": None, "reason": None, "error": None}
try:
    import hermes_cli
    from hermes_cli._launchers import resolve_store_python
    from pm.environments import committed_venv, store_root
    root = Path(hermes_cli.__file__).resolve().parent.parent
    store_py = resolve_store_python(root)
    if store_py is not None:
        try:
            committed = committed_venv(root)
            reason = None if committed is not None else "no dependency environment is committed"
        except RuntimeError as e:
            committed, reason = None, str(e)
        if committed is None:
            out["store_python"] = str(store_py)
            out["reason"] = reason
            facts = store_root(root) / "facts.json"
            dest = facts.with_name("facts.json.uncommitted-" + time.strftime("%Y%m%d-%H%M%S"))
            facts.rename(dest)
            out["set_aside"] = str(dest)
except Exception as e:
    out["error"] = f"{type(e).__name__}: {e}"
print("CATFISH_STORE_GUARD " + json.dumps(out, ensure_ascii=False))
"#;

#[derive(Debug, Deserialize, PartialEq)]
pub(crate) struct GuardReport {
    pub store_python: Option<String>,
    pub set_aside: Option<String>,
    pub reason: Option<String>,
    pub error: Option<String>,
}

/// 从脚本输出里认出结果行 (hermes 导入时可能先 print 别的)。
pub(crate) fn parse_report(stdout: &str) -> Option<GuardReport> {
    stdout
        .lines()
        .rev()
        .find_map(|l| l.strip_prefix("CATFISH_STORE_GUARD "))
        .and_then(|json| serde_json::from_str(json).ok())
}

/// 网关启动 / 重启 / 重写自启脚本之前调。返回 true = 刚挪开了一份坏登记 (调用方据此决定要不要重启)。
/// 失败只记日志, 不挡后面的步骤。
pub(crate) fn set_aside_uncommitted_store_python(when: &str) -> bool {
    let Some(home) = crate::services::catfish_paths::hermes_home() else { return false };
    let install_dir = home.join("hermes-agent");
    let py = crate::services::catfish_paths::hermes_venv_python(&install_dir);
    if !py.is_file() {
        return false;
    }
    let out = match crate::services::process::python_command(&py)
        .args(["-c", GUARD_PY])
        .env("HERMES_HOME", &home)
        .env_remove("PYTHONPATH")
        .env_remove("PYTHONHOME")
        .current_dir(&install_dir)
        .output()
    {
        Ok(out) => out,
        Err(e) => {
            log::warn!("[hermes-store] {when}: 起不了 hermes 的 venv Python ({e}), 没检查它登记的 Python");
            return false;
        }
    };
    let stdout = String::from_utf8_lossy(&out.stdout);
    let Some(report) = parse_report(&stdout) else {
        log::warn!(
            "[hermes-store] {when}: 检查 hermes 登记的 Python 没出结果 ({}): {}",
            out.status,
            String::from_utf8_lossy(&out.stderr).trim()
        );
        return false;
    };
    log_report(when, &report)
}

fn log_report(when: &str, r: &GuardReport) -> bool {
    match (&r.store_python, &r.set_aside, &r.error) {
        (Some(py), Some(dest), _) => {
            log::warn!(
                "[hermes-store] {when}: hermes 登记的 Python {py} 没有建好依赖环境 ({}), 用它起网关必然退出; \
                 已把登记改名为 {dest}, 网关改用 hermes-agent\\venv 的 Python。要撤销: 把它改回 facts.json",
                r.reason.as_deref().unwrap_or("?")
            );
            true
        }
        (Some(py), None, Some(err)) => {
            log::error!(
                "[hermes-store] {when}: hermes 登记的 Python {py} 没有建好依赖环境, 网关起不来; 想把登记改名没成 ({err})。\
                 手动 (管理员 PowerShell): Rename-Item \"$env:LOCALAPPDATA\\hermes\\tools\\facts.json\" facts.json.bak"
            );
            false
        }
        (None, _, Some(err)) => {
            log::warn!("[hermes-store] {when}: 检查 hermes 登记的 Python 出错: {err}");
            false
        }
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 认出结果行_前面混着别的输出() {
        let out = "some hermes banner\n\
                   CATFISH_STORE_GUARD {\"store_python\": \"C:\\\\h\\\\tools\\\\python-3.14.7\\\\python.exe\", \
                   \"set_aside\": \"C:\\\\h\\\\tools\\\\facts.json.uncommitted-20261029-120000\", \
                   \"reason\": \"no dependency environment is committed\", \"error\": null}\n";
        let r = parse_report(out).unwrap();
        assert_eq!(r.store_python.as_deref(), Some("C:\\h\\tools\\python-3.14.7\\python.exe"));
        assert!(log_report("测试", &r));
    }

    #[test]
    fn 没登记或已提交_什么都不做() {
        let r = parse_report("CATFISH_STORE_GUARD {\"store_python\": null, \"set_aside\": null, \"reason\": null, \"error\": null}").unwrap();
        assert!(!log_report("测试", &r));
        assert!(parse_report("Traceback (most recent call last)").is_none());
    }

    #[test]
    fn 改名失败_不算挪开() {
        let r = parse_report(
            "CATFISH_STORE_GUARD {\"store_python\": \"C:\\\\p.exe\", \"set_aside\": null, \"reason\": \"x\", \
             \"error\": \"PermissionError: [WinError 5] 拒绝访问\"}",
        )
        .unwrap();
        assert!(!log_report("测试", &r));
    }
}
