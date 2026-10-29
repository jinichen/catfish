//! llm-gateway (中央模型网关) 状态查询 —— 只读, 员工端无权启停。
//!
//! P39 (5/22 gateway 解耦收尾): start/stop/get_dev_token 3 个 Rust command 删了.
//! 生产员工机 gateway 由 launchctl/客户 IT 管, Companion 不 spawn 也不管 dev_token.
//!
//! 10/29 重写: 原来先 TCP 0.8 秒探活, 连不上就红点「未启动 — 点 "启动" 按钮拉起」(按钮 5 月就删了,
//! 而中央服务连不上多半是网络 / VPN / 地址问题, 不是「未启动」); 能连上但健康检查失败时提示
//! 「检查 .env / dev token」(开发期文案); 还读一个 Companion 自己起网关时代的 PID 文件 (早没人写了)。
//! 现在跟其他服务同一个探测 (service_probe::http_health): 端口被拒 = 中央服务没在那个端口上;
//! 超时 / 证书 / DNS = 状态未确认; 文案都是员工看得懂、知道该找谁的话。
//!
//! catfish_paths::{gateway_dir, gateway_log_path} 保留 (被 file_parse_env / logs.rs 用).

use crate::commands::types::ServiceStatus;
use crate::services::endpoints;

#[tauri::command]
pub async fn gateway_status() -> Result<ServiceStatus, String> {
    let ep = endpoints::endpoints();
    let base = ep.gateway_base();
    match crate::util::service_probe::http_health(&base, "/healthz", Some(ep.gateway_port), false).await {
        Ok(mut status) => {
            status.message = Some(if !status.running {
                format!("连不上中央服务器（{base} 上没有服务在监听）—— 核对「服务器地址」或联系 IT")
            } else if status.healthy {
                format!("已连接 {base}")
            } else {
                format!("连上了 {base}，但健康检查没通过 —— 中央服务可能在重启或异常，请联系 IT")
            });
            Ok(status)
        }
        Err(e) => Err(format!("{e} —— 检查网络 / VPN / 服务器地址，或联系 IT")),
    }
}
