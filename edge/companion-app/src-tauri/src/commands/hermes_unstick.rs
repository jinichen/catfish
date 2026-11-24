//! 11/24 鸿波「这是为什么？」: 小鲶答到一半本机服务 (hermes) 重启, 那一轮被记成「结果未知」,
//! hermes 就把整个对话暂停 —— 之后发什么都只回 `unknown_execution`, 员工没有任何办法解开。
//! 这里给对话里那个「放弃卡住的那一轮」按钮用: 走 hermes 自己的控制通道 (跟 `hermes sessions discard`
//! 同一条 prompt.resolve_unknown), 先取消暂停期间排着队的消息 (员工当时看到的都是报错, 不该事后自己跑起来),
//! 再确认那一轮作废。不重放任何输入。

use serde::Serialize;

#[derive(Serialize, Debug, PartialEq, Eq)]
pub struct UnstickResult {
    pub discarded: u32,
    pub cancelled: u32,
}

const SCRIPT: &str = r#"
import asyncio, json, sys
from hermes_cli.gateway_client import connect_gateway

async def main(sid):
    async with connect_gateway() as client:
        snap = await client.rpc("session.resume", session_id=sid)
        pending = snap.get("pending", [])
        if not any(row.get("status") == "unknown" for row in pending):
            # 没卡住 (可能已经被解开): 排着的是正常排队, 不能动
            print(json.dumps({"discarded": 0, "cancelled": 0}))
            return
        cancelled = 0
        for row in pending:
            if row.get("status") == "queued":
                await client.rpc("prompt.cancel", session_id=sid, admission_id=row["admission_id"])
                cancelled += 1
        discarded = 0
        for row in pending:
            if row.get("status") == "unknown":
                await client.rpc("prompt.resolve_unknown", session_id=sid, admission_id=row["admission_id"],
                                 execution_generation=row["execution_generation"])
                discarded += 1
        print(json.dumps({"discarded": discarded, "cancelled": cancelled}))

asyncio.run(main(sys.argv[1]))
"#;

pub(crate) fn parse_output(stdout: &str) -> Result<UnstickResult, String> {
    let line = stdout.lines().rev().find(|l| l.trim_start().starts_with('{')).ok_or("没有拿到结果")?;
    let v: serde_json::Value = serde_json::from_str(line.trim()).map_err(|e| e.to_string())?;
    let n = |k: &str| v.get(k).and_then(|x| x.as_u64()).unwrap_or(0) as u32;
    Ok(UnstickResult { discarded: n("discarded"), cancelled: n("cancelled") })
}

fn valid_session_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 128 && id.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

#[tauri::command]
pub async fn hermes_session_unstick(session_id: String) -> Result<UnstickResult, String> {
    if !valid_session_id(&session_id) {
        return Err("会话 id 不对".into());
    }
    let python = super::codex_probe::hermes_python()?;
    let root = python
        .parent()
        .and_then(|p| p.parent())
        .and_then(|p| p.parent())
        .ok_or("Hermes 路径异常")?
        .to_path_buf();
    let home = super::hermes_plugin::hermes_home().ok_or("找不到 Hermes 目录")?;
    tauri::async_runtime::spawn_blocking(move || {
        let out = crate::services::process::python_command(&python)
            .current_dir(&root)
            .env("HERMES_HOME", &home)
            .arg("-c")
            .arg(SCRIPT)
            .arg(&session_id)
            .output()
            .map_err(|e| format!("启动失败: {e}"))?;
        if !out.status.success() {
            let err = String::from_utf8_lossy(&out.stderr);
            let tail: String = err.lines().rev().take(3).collect::<Vec<_>>().into_iter().rev().collect::<Vec<_>>().join(" / ");
            return Err(format!("本机服务没接受: {tail}"));
        }
        parse_output(&String::from_utf8_lossy(&out.stdout))
    })
    .await
    .map_err(|e| e.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_last_json_line() {
        let r = parse_output("warn: x\n{\"discarded\": 1, \"cancelled\": 6}\n").unwrap();
        assert_eq!(r, UnstickResult { discarded: 1, cancelled: 6 });
    }

    #[test]
    fn rejects_odd_session_ids() {
        assert!(valid_session_id("20261106_142422_babcd2"));
        assert!(!valid_session_id("a;rm -rf"));
        assert!(!valid_session_id(""));
    }
}
