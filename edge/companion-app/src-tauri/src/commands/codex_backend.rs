//! Codex 后端接入：让 Companion 用 UI 驱动 Hermes 自带的
//! `codex_app_server` runtime，而不是在 Catfish 里重新实现一套 Codex 协议。
//!
//! 安全边界：
//! - 登录凭据完全由 Codex CLI 管理，Companion 只执行 `login status`。
//! - 切换时只备份/恢复 `model.provider` 和 `model.default`，不读写 API key。
//! - 复用 Hermes 自己的 `codex_runtime_switch.apply()`，保留其 MCP/plugin 迁移逻辑。

use serde::{Deserialize, Serialize};
use std::process::Command;
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use super::codex_gateway::{hermes_api_ready, restart_hermes_gateway};
use super::codex_helper::{run_hermes_helper, HermesState};
use super::codex_probe::{probe_codex, CodexProbe};
use super::codex_shim::{ensure_hermes_codex_shim, remove_hermes_codex_shim};

const MIN_CODEX_VERSION: (u32, u32, u32) = (0, 130, 0);


#[derive(Debug, Clone, Serialize, Deserialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct CodexBackendStatus {
    pub installed: bool,
    pub binary_path: Option<String>,
    pub version: Option<String>,
    pub supported: bool,
    pub logged_in: bool,
    pub hermes_ready: bool,
    pub enabled: bool,
    pub runtime: String,
    pub provider: Option<String>,
    pub model: Option<String>,
    pub models: Vec<String>,
    pub ready: bool,
    pub requires_new_session: bool,
    pub message: String,
}

fn build_status(
    probe: Option<CodexProbe>,
    hermes: Result<HermesState, String>,
    action_message: Option<String>,
    requires_new_session: bool,
) -> CodexBackendStatus {
    let installed = probe.is_some();
    let supported = probe
        .as_ref()
        .map(|value| value.version >= MIN_CODEX_VERSION)
        .unwrap_or(false);
    let logged_in = probe.as_ref().map(|value| value.logged_in).unwrap_or(false);
    let hermes_ready = hermes.is_ok() && hermes_api_ready();
    let (runtime, provider, model, models, hermes_error) = match hermes {
        Ok(value) => (
            value.runtime,
            non_empty(value.provider),
            non_empty(value.model),
            value.models,
            None,
        ),
        Err(error) => ("auto".to_string(), None, None, Vec::new(), Some(error)),
    };
    let enabled = runtime == "codex_app_server" && provider.as_deref() == Some("openai-codex");
    let ready = installed && supported && logged_in && hermes_ready;

    let message = action_message.unwrap_or_else(|| {
        if let Some(error) = hermes_error {
            error
        } else if !installed {
            "未找到 Codex CLI，可先安装 Codex 或 ChatGPT 桌面版".to_string()
        } else if !supported {
            format!(
                "Codex 版本过旧，需要 {}.{}.{} 或更新",
                MIN_CODEX_VERSION.0, MIN_CODEX_VERSION.1, MIN_CODEX_VERSION.2
            )
        } else if !logged_in {
            "Codex 尚未登录，登录后即可启用".to_string()
        } else if enabled {
            "Codex 已接管 Hermes 的 OpenAI 会话".to_string()
        } else {
            "已就绪，可以一键启用 Codex 后端".to_string()
        }
    });

    CodexBackendStatus {
        installed,
        binary_path: probe
            .as_ref()
            .map(|value| value.path.to_string_lossy().to_string()),
        version: probe.as_ref().map(|value| value.version_text.clone()),
        supported,
        logged_in,
        hermes_ready,
        enabled,
        runtime,
        provider,
        model,
        models,
        ready,
        requires_new_session,
        message,
    }
}

fn non_empty(value: String) -> Option<String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

#[tauri::command]
pub async fn codex_backend_status() -> Result<CodexBackendStatus, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let probe = probe_codex();
        let hermes = run_hermes_helper(
            "status",
            probe.as_ref().map(|value| value.path.as_path()),
            None,
        );
        Ok(build_status(probe, hermes, None, false))
    })
    .await
    .map_err(|error| format!("Codex 状态检查失败: {error}"))?
}

#[tauri::command]
pub async fn codex_backend_set_enabled(enabled: bool) -> Result<CodexBackendStatus, String> {
    // 8/8: 只挡"开", 不挡"关"。
    //
    // 挡关会把人锁死在 Codex 状态里 —— 8/8 那台机器就正好卡在
    // provider=openai-codex / default=gpt-5.6-luna, 要靠这条路读备份文件切回来。
    // 一道防呆闸不该顺手堵住唯一的退路。
    if enabled && !crate::services::codex_gate::codex_enabled() {
        return Err(crate::services::codex_gate::disabled_reason());
    }
    tauri::async_runtime::spawn_blocking(move || {
        let probe = probe_codex().ok_or_else(|| "未找到 Codex CLI".to_string())?;
        if probe.version < MIN_CODEX_VERSION {
            return Err(format!(
                "Codex 版本过旧，需要 {}.{}.{} 或更新",
                MIN_CODEX_VERSION.0, MIN_CODEX_VERSION.1, MIN_CODEX_VERSION.2
            ));
        }
        if enabled && !probe.logged_in {
            return Err("请先登录 Codex".to_string());
        }

        let created_shim = if enabled {
            ensure_hermes_codex_shim(&probe.path)?
        } else {
            None
        };
        let action = if enabled { "enable" } else { "disable" };
        let state = match run_hermes_helper(action, Some(&probe.path), None) {
            Ok(value) => value,
            Err(error) => {
                if created_shim.is_some() {
                    remove_hermes_codex_shim();
                }
                return Err(error);
            }
        };
        if !enabled {
            remove_hermes_codex_shim();
        }
        let result = state.result.as_ref();
        let message = result
            .map(|_| {
                if enabled {
                    "Codex 后端已启用，新建一个会话后生效".to_string()
                } else {
                    "已切回原有 Hermes 后端，新建一个会话后生效".to_string()
                }
            })
            .or_else(|| Some("后端配置已更新".to_string()));
        let requires_new_session = result
            .map(|value| value.requires_new_session)
            .unwrap_or(true);
        if let Some(value) = result.filter(|value| !value.success) {
            return Err(value.message.clone());
        }
        Ok(build_status(
            Some(probe),
            Ok(state),
            message,
            requires_new_session,
        ))
    })
    .await
    .map_err(|error| format!("Codex 后端切换失败: {error}"))?
}

/// 聊天模型选择器的唯一切换入口。选中 Hermes 公布的 Codex
/// 模型时自动启用 app-server runtime；选中其他模型时恢复原有
/// Hermes/gateway 配置。凭据仍完全由 Codex CLI 保管。
#[tauri::command]
pub async fn codex_backend_select_model(model: String) -> Result<CodexBackendStatus, String> {
    let requested = model.trim().to_string();
    if requested.is_empty() {
        return Err("模型名不能为空".to_string());
    }
    tauri::async_runtime::spawn_blocking(move || {
        // 8/8: 开关关着时不许**选中** Codex 模型, 但仍要放行选回普通模型 ——
        // 后者正是从 Codex 状态退出来的那条路 (helper 会判成 action=disable,
        // 读备份把 provider/default 还原)。
        //
        // 判据用 catalog_status() 而不是 catalog_models(): 后者已经被闸门清空了,
        // 拿它判会永远判不出"这是个 Codex 模型", 等于闸门失效。
        if !crate::services::codex_gate::codex_enabled()
            && catalog_status().models.iter().any(|m| m == &requested)
        {
            return Err(crate::services::codex_gate::disabled_reason());
        }
        let probe = probe_codex();
        let state = run_hermes_helper(
            "select",
            probe.as_ref().map(|value| value.path.as_path()),
            Some(&requested),
        )?;
        let selected_codex = state.runtime == "codex_app_server"
            && state.provider == "openai-codex"
            && state.model == requested;
        if selected_codex {
            let available = probe
                .as_ref()
                .ok_or_else(|| "未找到 Codex CLI".to_string())?;
            if !available.logged_in {
                return Err("请先登录 Codex".to_string());
            }
            ensure_hermes_codex_shim(&available.path)?;
        } else {
            remove_hermes_codex_shim();
        }
        invalidate_catalog_cache();
        let hermes_requires_new_session = state
            .result
            .as_ref()
            .map(|value| value.requires_new_session)
            .unwrap_or(false);
        // API server 的每条消息本来就是新 AIAgent，config.yaml + picker_state
        // 会在下一次请求即时生效。完整 gateway 重启要 10–30 秒，只在服务已经
        // 不健康时兜底；健康路径是真热切换。
        if !hermes_api_ready() {
            restart_hermes_gateway()?;
        }
        // ⚠ 这一位必须**如实**回传 Hermes 说的话。
        //
        // 原来这里读出了 hermes_requires_new_session, 打一条 log, 然后给
        // build_status 传字面量 `false` —— 而那条 log 的文案 ("下一个 API 回合
        // 直接生效") 恰好是它的反面。Hermes 明确要求新会话时, 前端收到的是
        // "不需要", 员工在当前会话继续发消息、跑的还是旧 runtime。
        //
        // 这个 commit 要修的就是"UI 显示 A 实际跑 B"; 靠丢掉 Hermes 自己的信号
        // 来"加速", 等于把要修的问题重新造了一遍, 而且这次连日志都在说反话。
        //
        // 热切换本来就是常态 (API server 每条消息新建 AIAgent), 所以这一位平时
        // 是 false; 它为 true 的少数场景恰恰是热切换**不够**、必须开新会话的
        // 时候 —— 那正是不能瞒着前端的时候。
        if hermes_requires_new_session {
            log::info!("Hermes 要求新建会话后才生效 —— 已如实告知前端");
        }
        let message = if selected_codex {
            Some(format!("已热切换到 Codex 模型 {requested}"))
        } else {
            Some(format!("已热切换到普通模型 {requested}"))
        };
        Ok(build_status(
            probe,
            Ok(state),
            message,
            hermes_requires_new_session,
        ))
    })
    .await
    .map_err(|error| format!("模型切换失败: {error}"))?
}

const CATALOG_READY_CACHE_TTL: Duration = Duration::from_secs(5 * 60);
/// 不可用状态的缓存时长。**2 秒 → 30 秒 (8/1)**。
///
/// 原值 2 秒, 而 useCatalog 每 15 秒轮询一次 —— 每一次都过期, 于是每一次都
/// 重新 `probe_codex()` (扫整个 PATH 做 stat) 外加起一个完整 Python 解释器
/// import hermes_cli。而绝大多数员工机器上永远不会有 Codex, `ready` 恒 false,
/// 这笔开销是**常驻的、且永远不会有结果**。
///
/// 原注释说 2 秒是为了"绝不能把灰色选项钉住 5 分钟"—— 但模型切换那条路径
/// 自己就调了 `invalidate_catalog_cache()` (见 select_model), 切换窗口根本
/// 不靠 TTL 兜。也就是说这个短 TTL 对它声称的目的是多余的, 留下的只有开销。
///
/// 30 秒仍然远短于 ready 状态的 5 分钟: 装了 Codex 但临时不健康的机器,
/// 最多半分钟就会重新探到, 而不装 Codex 的机器少了 14/15 的无用功。
const CATALOG_UNREADY_CACHE_TTL: Duration = Duration::from_secs(30);
static CATALOG_CACHE: OnceLock<Mutex<Option<(Instant, CodexBackendStatus)>>> = OnceLock::new();

fn invalidate_catalog_cache() {
    if let Some(cache) = CATALOG_CACHE.get() {
        if let Ok(mut value) = cache.lock() {
            *value = None;
        }
    }
}

fn catalog_status() -> CodexBackendStatus {
    let cache = CATALOG_CACHE.get_or_init(|| Mutex::new(None));
    if let Ok(value) = cache.lock() {
        if let Some((created, status)) = value.as_ref() {
            // 登录成功且 gateway 健康时可以长缓存；任何不可用状态都可能只是
            // 模型切换时的几秒重启窗口，绝不能把灰色选项钉住 5 分钟。
            let ttl = if status.ready {
                CATALOG_READY_CACHE_TTL
            } else {
                CATALOG_UNREADY_CACHE_TTL
            };
            if created.elapsed() < ttl {
                return status.clone();
            }
        }
    }

    let probe = probe_codex();
    // 机器上压根没有 codex 二进制时, 不用再起一个 Python 去问 hermes 的 Codex
    // 状态 —— 没有 codex 的机器上那个答案只有一种, 而问一次的代价是一个完整的
    // Python 解释器 + import hermes_cli。
    //
    // 这不是边角情况: 国内基本用不上 Codex (要 OpenAI 账号), 也就是说
    // **绝大多数员工机器的常态就是这一支**。原来每次 catalog 轮询都白跑一趟。
    //
    // 传的错误文案跟 build_status 里 `!installed` 那一支逐字相同, 所以界面上
    // 看到的话没有变化 —— 变的只是不再为它起进程。
    let hermes = if probe.is_some() {
        run_hermes_helper(
            "status",
            probe.as_ref().map(|value| value.path.as_path()),
            None,
        )
    } else {
        Err("未找到 Codex CLI，可先安装 Codex 或 ChatGPT 桌面版".to_string())
    };
    let status = build_status(probe, hermes, None, false);
    if let Ok(mut value) = cache.lock() {
        *value = Some((Instant::now(), status.clone()));
    }
    status
}

/// 供 Companion `/v1/catalog` 合并本机 Codex 模型。追加的
/// `source/selectable` 字段只用于前端分组和禁用不可用项。
pub fn catalog_models() -> Vec<serde_json::Value> {
    // 8/8: 开关关着就一个都不吐 —— Codex 模型压根不进对话的模型选择器。
    //
    // 这是这道闸最要紧的一处。8/8 之前只要本机装了 ChatGPT.app 并登录, Codex
    // 模型就自动出现在 picker 里, 点一下就把 hermes 全局切过去了, 而后台调用
    // (advisor / 记忆 / 邮件评级) 是写死打网关的, 网关没有这个模型 → 全线 404。
    // 员工看到的只是早安页坏了, 完全联想不到是刚才换了个模型。
    //
    // 从源头不给这个选项, 比事后提示可靠。理由见 services/codex_gate.rs。
    if !crate::services::codex_gate::codex_enabled() {
        log::debug!("[codex] 开关未启用, catalog 不含 Codex 模型");
        return Vec::new();
    }
    let status = catalog_status();
    let selectable =
        status.installed && status.supported && status.logged_in && status.hermes_ready;
    let reason = if selectable {
        "Codex App Server · ChatGPT 已登录".to_string()
    } else {
        status.message.clone()
    };
    status
        .models
        .iter()
        .map(|model| {
            serde_json::json!({
                "id": model,
                "display_name": format!("{} · Codex", model),
                "tier": "public",
                "recommended_for": "Codex 本机编程与文件任务",
                "context_window": 0,
                "cost_tier": "ChatGPT",
                "supports_tool_use": true,
                "supports_vision": true,
                "api_key_configured": status.logged_in,
                "is_reachable": if selectable { Some(true) } else { Some(false) },
                "status_reason": reason,
                "source": "codex",
                "selectable": selectable,
            })
        })
        .collect()
}

#[cfg(target_os = "macos")]
fn apple_script_escape(value: &str) -> String {
    value.replace('\\', "\\\\").replace('"', "\\\"")
}

#[cfg(target_os = "macos")]
fn shell_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\\''"))
}

#[tauri::command]
pub async fn codex_backend_open_login() -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(|| {
        let probe = probe_codex().ok_or_else(|| "未找到 Codex CLI".to_string())?;
        #[cfg(target_os = "macos")]
        {
            let command = format!("{} login", shell_quote(&probe.path.to_string_lossy()));
            let script = format!(
                "tell application \"Terminal\" to do script \"{}\"",
                apple_script_escape(&command)
            );
            let output = Command::new("osascript")
                .args([
                    "-e",
                    "tell application \"Terminal\" to activate",
                    "-e",
                    &script,
                ])
                .output()
                .map_err(|error| format!("无法打开登录窗口: {error}"))?;
            if !output.status.success() {
                return Err(String::from_utf8_lossy(&output.stderr).trim().to_string());
            }
            return Ok(());
        }
        #[cfg(not(target_os = "macos"))]
        {
            let _ = probe;
            Err("请在终端运行 codex login".to_string())
        }
    })
    .await
    .map_err(|error| format!("打开 Codex 登录失败: {error}"))?
}

// 8/15 拆分后这里只剩这一条 macOS 专属测试了 (其余四条跟着被测函数进了
// codex_probe / codex_helper / codex_gateway)。cfg 从 #[test] 上提到 mod 上
// —— 否则在 Windows / Linux 上这个 mod 是空的, `use super::*` 会变成一条
// 没人用的 import 警告。跑的时机没变: 本来就只在 macOS 上跑。
#[cfg(all(test, target_os = "macos"))]
mod tests {
    use super::*;

    #[test]
    fn quotes_terminal_command_safely() {
        assert_eq!(shell_quote("/tmp/a'b/codex"), "'/tmp/a'\\''b/codex'");
        assert_eq!(apple_script_escape("a\\b\"c"), "a\\\\b\\\"c");
    }
}
