//! Windows: hermes 开机自启的启动脚本跟着 hermes 升级重写 (10/19)。
//!
//! # 病
//!
//! 10/19 鸿波的 Windows 机器, 小鲶每句都报
//! `Failed to initialize OpenAI client: No module named 'pydantic_core._pydantic_core'`。
//! 跑着的网关是 `uv\python\cpython-3.11.10\pythonw.exe -m hermes_cli.main gateway run` ——
//! 而 hermes 的环境 10/15 升到 v0.22 时已经换成 Python 3.14 (`pydantic_core` 只有 cp314 的 .pyd),
//! 3.11 加载不了, 微信那边同理报 "Need aiohttp + cryptography"。
//!
//! 3.11 是哪来的: 老 hermes 当年 `gateway install` 写的开机自启 (计划任务 / 启动文件夹里的
//! `Hermes_Gateway.vbs` + `gateway-service\Hermes_Gateway.*`), 里面写死了当时的解释器。
//! 我们升级 hermes 只换 `hermes-agent` 目录, 从不重写这些启动脚本, 于是**每次登录 Windows**
//! 都按老脚本拉起一个 3.11 的网关; Companion 的 `hermes gateway start/restart` 也走它。
//!
//! # 做法
//!
//! 每次 Companion 启动 (hermes 装好之后) 用新 hermes 自己的代码:
//! - 已经装过自启的, `_write_task_script()` 按当前解释器重写启动脚本 (计划任务 / 启动项指向的
//!   就是这两个文件, 文件名不变, 不用碰 schtasks, 也就不会弹 UAC);
//! - 老版本留下的无后缀启动项 (`gateway_windows_legacy`, hermes 自己只在 `install --force` 时清)
//!   一并删掉; 原来只靠它自启的, 补一个启动文件夹里的新启动项 (hermes 自己的兜底方式)。
//!
//! 有改动 → 重启一次网关, 换掉正在跑的旧解释器。没改动什么都不做。
//!
//! # 10/27: 只有「解释器换了」才算改动, 重启后确认网关真起来
//!
//! 鸿波 Windows 机 10/27 三次打开 Companion (08:58 / 11:43 / 14:51) 三次都报 rewritten=true、
//! 三次都重启网关 —— 每次都把手头的回答掐断, 14:51 那次重启没起来, 网关停了 (日志里只记一行就完了)。
//! 脚本文本有出入不等于要换进程: 本来只为换掉写死的旧解释器 (3.11 那种), 所以只比较
//! `-m hermes_cli.main gateway run` 前面那个解释器路径。
//!
//! 重启 (Windows 上是先停再起, hermes 自己只等新网关 15 秒) 失败, 或者打开 Companion 时网关压根
//! 没在跑, 都用 `hermes gateway start` (直接拉起, 10/27 鸿波手动跑这条恢复的) 再拉一次, 并确认端口通。

use serde::Deserialize;

/// 在 hermes 的 venv 里跑。只用 hermes 自己的函数, 规则跟它 `gateway install` 写出来的一致。
#[cfg_attr(any(not(windows), debug_assertions), allow(dead_code))]
const RECONCILE_PY: &str = r#"
import json
from hermes_cli import gateway_windows as w
from hermes_cli import gateway_windows_legacy as legacy
import re
out = {"rewritten": False, "legacy": [], "error": None, "interpreter_before": None, "interpreter_after": None}
def interpreter(text):
    m = re.search(r'^\s*(?:sh\.Run\s+")?(.+?)\s+-m hermes_cli\.main gateway run', text, re.M)
    return m.group(1).strip().strip('"') if m else None
try:
    script = w.get_task_script_path()
    vbs = script.with_suffix(".vbs")
    installed = script.exists() or vbs.exists() or w.get_startup_entry_path().exists()
    if installed:
        def read(p):
            return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        before = read(script) + read(vbs)
        out["interpreter_before"] = interpreter(read(vbs)) or interpreter(read(script))
        w._write_task_script()
        out["rewritten"] = (read(script) + read(vbs)) != before
        out["interpreter_after"] = interpreter(read(vbs)) or interpreter(read(script))
    found = legacy.legacy_launcher_artifacts()
    out["legacy"] = [str(t) for _k, _l, t in found]
    if found:
        legacy.remove_legacy_launchers()
        if not installed:
            # 原来只有老启动项在管自启 —— 删了它要补一个新的, 否则下次登录 hermes 不起来。
            # 用启动文件夹 (hermes 自己的兜底方式), 不碰计划任务, 不弹 UAC。
            w._install_startup_entry(w._write_task_script())
            out["rewritten"] = True
except Exception as e:
    out["error"] = f"{type(e).__name__}: {e}"
print("CATFISH_RECONCILE " + json.dumps(out, ensure_ascii=False))
"#;

#[derive(Debug, Deserialize, PartialEq)]
#[cfg_attr(any(not(windows), debug_assertions), allow(dead_code))]
pub(crate) struct ReconcileReport {
    pub rewritten: bool,
    pub legacy: Vec<String>,
    pub error: Option<String>,
    #[serde(default)]
    pub interpreter_before: Option<String>,
    #[serde(default)]
    pub interpreter_after: Option<String>,
}

impl ReconcileReport {
    #[cfg_attr(any(not(windows), debug_assertions), allow(dead_code))]
    pub(crate) fn changed(&self) -> bool {
        self.interpreter_changed() || !self.legacy.is_empty()
    }

    /// 启动脚本里的解释器换了 (大小写不敏感 —— Windows 路径)。认不出解释器的老格式按「文本变了」算。
    fn interpreter_changed(&self) -> bool {
        if !self.rewritten {
            return false;
        }
        match (&self.interpreter_before, &self.interpreter_after) {
            (Some(a), Some(b)) => !a.eq_ignore_ascii_case(b),
            _ => true,
        }
    }
}

/// 从脚本输出里认出那一行 (hermes 自己 print 的 ✓/⚠ 行混在前面)。
#[cfg_attr(any(not(windows), debug_assertions), allow(dead_code))]
pub(crate) fn parse_report(stdout: &str) -> Option<ReconcileReport> {
    stdout
        .lines()
        .rev()
        .find_map(|l| l.strip_prefix("CATFISH_RECONCILE "))
        .and_then(|json| serde_json::from_str(json).ok())
}

/// Companion 启动、hermes 装好之后调。失败只记日志。
///
/// 唯一调用方 hermes_install::spawn_hermes_bootstrap 只在 release 构建里有
/// (`not(debug_assertions)`), 这里用同样的条件 —— 原来只写 `cfg(windows)`, Windows 的
/// debug / 测试构建里它和上面三样都没人用, cargo_check_windows.sh 报 4 条 dead_code。
#[cfg(all(windows, not(debug_assertions)))]
pub(crate) fn reconcile_windows_launcher() {
    let Some(home) = crate::services::catfish_paths::hermes_home() else { return };
    let install_dir = home.join("hermes-agent");
    let py = crate::services::catfish_paths::hermes_venv_python(&install_dir);
    if !py.is_file() {
        return;
    }
    // 10/29: 先挪开没建好依赖环境的 hermes 自管 Python 登记 —— 否则下面按 hermes 的
    // get_python_path() 重写出来的自启脚本还是指向它, 每次登录都起不来。挪开后解释器变了,
    // 下面会照常判定「有改动」并重启网关。
    super::hermes_store_guard::set_aside_uncommitted_store_python("打开 Companion 时");
    let out = match crate::services::process::python_command(&py)
        .args(["-c", RECONCILE_PY])
        .env("HERMES_HOME", &home)
        .env_remove("PYTHONPATH")
        .env_remove("PYTHONHOME")
        .current_dir(&install_dir)
        .output()
    {
        Ok(out) => out,
        Err(e) => {
            log::warn!("[hermes-launcher] 起不了 hermes 的 Python ({e}), 没检查开机自启脚本");
            return;
        }
    };
    let stdout = String::from_utf8_lossy(&out.stdout);
    let Some(report) = parse_report(&stdout) else {
        log::warn!(
            "[hermes-launcher] 检查开机自启脚本没出结果 ({}): {}",
            out.status,
            String::from_utf8_lossy(&out.stderr).trim()
        );
        return;
    };
    if let Some(err) = &report.error {
        log::warn!("[hermes-launcher] 检查开机自启脚本出错: {err}");
    }
    if !report.changed() {
        if report.rewritten {
            log::info!(
                "[hermes-launcher] 开机自启脚本有出入已重写, 解释器没变 ({:?}), 不重启网关",
                report.interpreter_after
            );
        }
        // 10/27: 打开 Companion 时网关没在跑 (上次重启没起来 / 登录时自启失败) → 拉起来
        super::hermes_gateway_health::ensure_gateway_up("打开 Companion 时");
        return;
    }
    log::info!(
        "[hermes-launcher] 开机自启脚本已按当前 hermes 重写 (解释器 {:?} → {:?}, 删掉老启动项 {:?}), 重启网关换掉旧解释器",
        report.interpreter_before,
        report.interpreter_after,
        report.legacy
    );
    // 10/27: 先等手头的回答写完 —— Windows 的重启是先停再起, 只给进行中的回答 30 秒。
    // 这里本来就在 bootstrap 后台线程的最后一步, 阻塞着等不碍事。
    crate::services::hermes_idle::wait_until_idle(crate::services::hermes_idle::MAX_WAIT, "重启 hermes 网关");
    if let Err(e) = super::codex_gateway::restart_hermes_gateway() {
        log::warn!("[hermes-launcher] 重写后重启 hermes 网关没成: {e}");
    }
    super::hermes_gateway_health::ensure_gateway_up("重写启动脚本重启后");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 认出结果行_前面混着_hermes_自己的输出() {
        let out = "✓ Removed legacy pre-suffix Windows login item: C:\\x\\Hermes_Gateway.vbs\n\
                   CATFISH_RECONCILE {\"rewritten\": true, \"legacy\": [\"C:\\\\x\\\\Hermes_Gateway.vbs\"], \"error\": null}\n";
        let r = parse_report(out).unwrap();
        assert!(r.rewritten && r.changed());
        assert_eq!(r.legacy, vec!["C:\\x\\Hermes_Gateway.vbs".to_string()]);
    }

    #[test]
    fn 文本变了但解释器没变_不重启() {
        let r = parse_report(
            "CATFISH_RECONCILE {\"rewritten\": true, \"legacy\": [], \"error\": null, \
             \"interpreter_before\": \"C:\\\\h\\\\tools\\\\python.exe\", \
             \"interpreter_after\": \"c:\\\\H\\\\tools\\\\python.exe\"}",
        )
        .unwrap();
        assert!(!r.changed());
    }

    #[test]
    fn 解释器换了才重启_老格式认不出按变了算() {
        let r = parse_report(
            "CATFISH_RECONCILE {\"rewritten\": true, \"legacy\": [], \"error\": null, \
             \"interpreter_before\": \"C:\\\\uv\\\\cpython-3.11.10\\\\pythonw.exe\", \
             \"interpreter_after\": \"C:\\\\h\\\\tools\\\\python.exe\"}",
        )
        .unwrap();
        assert!(r.changed());
        let old = parse_report("CATFISH_RECONCILE {\"rewritten\": true, \"legacy\": [], \"error\": null}").unwrap();
        assert!(old.changed());
    }

    #[test]
    fn 没改动就不重启() {
        let r = parse_report("CATFISH_RECONCILE {\"rewritten\": false, \"legacy\": [], \"error\": null}").unwrap();
        assert!(!r.changed());
        assert!(parse_report("Traceback ...").is_none());
    }
}
