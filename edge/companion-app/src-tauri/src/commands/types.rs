//! 命令返回值的共享类型。
//!
//! 与前端 `src/types/service.ts` 严格对齐：
//!   - camelCase (rename_all)
//!   - Option 字段用 skip_serializing_if 对齐 TS 的 `?: T`（缺字段而非 null）

use serde::Serialize;

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ServiceStatus {
    /// 进程在跑（TCP 端口可连或 PID 活）
    pub running: bool,
    /// 业务面健康（HTTP 探测通）—— "进程在跑" ≠ "服务能用"
    pub healthy: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pid: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub port: Option<u16>,
    /// 给 UI 显示的状态描述（人话）
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
}

impl ServiceStatus {
    /// 服务没启的标准回复 —— 各个 status 命令都用这个
    pub fn down(port: Option<u16>, message: impl Into<String>) -> Self {
        Self {
            running: false,
            healthy: false,
            pid: None,
            port,
            message: Some(message.into()),
        }
    }
}
