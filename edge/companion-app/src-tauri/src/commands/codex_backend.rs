//! Codex 后端接入：让 Companion 用 UI 驱动 Hermes 自带的
//! `codex_app_server` runtime，而不是在 Catfish 里重新实现一套 Codex 协议。
//!
//! 安全边界：
//! - 登录凭据完全由 Codex CLI 管理，Companion 只执行 `login status`。
//! - 切换时只备份/恢复 `model.provider` 和 `model.default`，不读写 API key。
//! - 复用 Hermes 自己的 `codex_runtime_switch.apply()`，保留其 MCP/plugin 迁移逻辑。

use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

const MIN_CODEX_VERSION: (u32, u32, u32) = (0, 130, 0);

/// 这段 helper 运行在 Hermes 自己的 venv 中，所用 API 与 `/codex-runtime`
/// 命令完全相同。JSON 始终放在 stdout 最后一行，以兼容 Hermes
/// 迁移过程中可能打印的提示。
const HERMES_HELPER: &str = r#"
import json
import sys
from dataclasses import asdict
from pathlib import Path

from hermes_cli import codex_runtime_switch as crs
from hermes_cli.codex_models import get_codex_model_ids
from hermes_cli.config import get_config_path, load_config, read_raw_config, save_config

action = sys.argv[1]
requested_model = sys.argv[2].strip() if len(sys.argv) > 2 else ""
config_path = get_config_path()
backup_path = config_path.parent / "catfish-codex-backend-backup.json"

def model_view(config):
    model = config.get("model") if isinstance(config, dict) else {}
    if not isinstance(model, dict):
        model = {}
    models = [m for m in get_codex_model_ids() if isinstance(m, str) and m.startswith("gpt-")]
    return {
        "runtime": crs.get_current_runtime(config),
        "provider": str(model.get("provider") or ""),
        "model": str(model.get("default") or ""),
        "models": models,
    }

if action == "status":
    print(json.dumps(model_view(load_config()), ensure_ascii=False))
    raise SystemExit(0)

config = load_config()
if not isinstance(config.get("model"), dict):
    config["model"] = {}

if action == "select":
    available = [m for m in get_codex_model_ids() if isinstance(m, str) and m.startswith("gpt-")]
    action = "enable" if requested_model in available else "disable"

if action == "enable":
    raw = read_raw_config()
    raw_model = raw.get("model") if isinstance(raw, dict) else {}
    if not isinstance(raw_model, dict):
        raw_model = {}
    current_runtime = crs.get_current_runtime(config)
    if current_runtime != "codex_app_server":
        backup = {
            "version": 2,
            "provider_present": "provider" in raw_model,
            "provider": raw_model.get("provider"),
            "default_present": "default" in raw_model,
            "default": raw_model.get("default"),
        }
        backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            backup_path.chmod(0o600)
        except OSError:
            pass
    elif not backup_path.exists():
        # 已在 Codex runtime 却丢了备份时，绝不能把 openai-codex/gpt-* 本身
        # 当成“原后端”保存；否则切回普通模型会留下污染配置。Catfish 的普通
        # gateway 凭据仍在 base_url/api_key，provider/default 可由 picker 注入。
        backup = {
            "version": 2,
            "provider_present": False,
            "provider": None,
            "default_present": False,
            "default": None,
        }
        backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            backup_path.chmod(0o600)
        except OSError:
            pass

    models = [m for m in get_codex_model_ids() if isinstance(m, str) and m.startswith("gpt-")]
    if not models:
        raise RuntimeError("Codex 没有返回可用模型")
    selected_model = requested_model if requested_model in models else models[0]
    already_selected = (
        crs.get_current_runtime(config) == "codex_app_server"
        and str(config["model"].get("provider") or "") == "openai-codex"
        and str(config["model"].get("default") or "") == selected_model
    )
    config["model"]["provider"] = "openai-codex"
    config["model"]["default"] = selected_model
    if already_selected:
        result = crs.CodexRuntimeStatus(
            success=True,
            new_value="codex_app_server",
            old_value="codex_app_server",
            message=f"Codex model already selected: {selected_model}",
            requires_new_session=False,
        )
    else:
        result = crs.apply(config, "codex_app_server", persist_callback=save_config)
    # Hermes 的 runtime 迁移只承诺切换执行器；迁移代码与未来版本可能会
    # 重新写入它自己的推荐模型。picker 的选择才是用户明确意图，所以必须
    # 在迁移完成后再次锁定 provider/default 并持久化。
    if result.success:
        config["model"]["provider"] = "openai-codex"
        config["model"]["default"] = selected_model
        save_config(config)
elif action == "disable":
    result = crs.apply(config, "auto", persist_callback=None)
    if not result.success:
        print(json.dumps({"result": asdict(result), **model_view(config)}, ensure_ascii=False))
        raise SystemExit(2)
    codex_models = {m for m in get_codex_model_ids() if isinstance(m, str) and m.startswith("gpt-")}
    model = config["model"]
    if backup_path.exists():
        backup = json.loads(backup_path.read_text(encoding="utf-8"))
        backup_provider = str(backup.get("provider") or "")
        backup_default = str(backup.get("default") or "")
        contaminated = backup_provider == "openai-codex" or backup_default in codex_models
        if backup.get("provider_present") and not contaminated:
            model["provider"] = backup.get("provider")
        else:
            model.pop("provider", None)
        if backup.get("default_present") and not contaminated:
            model["default"] = backup.get("default")
        else:
            model.pop("default", None)
    else:
        # 兼容旧版本留下的“无备份 Codex 配置”。
        if str(model.get("provider") or "") == "openai-codex":
            model.pop("provider", None)
        if str(model.get("default") or "") in codex_models:
            model.pop("default", None)
    save_config(config)
    try:
        backup_path.unlink()
    except FileNotFoundError:
        pass
else:
    raise RuntimeError(f"unsupported action: {action}")

payload = {"result": asdict(result), **model_view(config)}
print(json.dumps(payload, ensure_ascii=False))
if not result.success:
    raise SystemExit(2)
"#;

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

#[derive(Debug, Deserialize, Default)]
struct HermesState {
    #[serde(default)]
    runtime: String,
    #[serde(default)]
    provider: String,
    #[serde(default)]
    model: String,
    #[serde(default)]
    models: Vec<String>,
    #[serde(default)]
    result: Option<HermesActionResult>,
}

#[derive(Debug, Deserialize, Default)]
struct HermesActionResult {
    #[serde(default)]
    success: bool,
    #[serde(default)]
    message: String,
    #[serde(default)]
    requires_new_session: bool,
}

#[derive(Debug, Clone)]
struct CodexProbe {
    path: PathBuf,
    version_text: String,
    version: (u32, u32, u32),
    logged_in: bool,
}

fn home_dir() -> Result<PathBuf, String> {
    crate::util::paths::home_env()
        .map(PathBuf::from)
        .map_err(|_| "找不到用户主目录".to_string())
}

fn hermes_root() -> Result<PathBuf, String> {
    if let Some(value) = std::env::var_os("HERMES_HOME") {
        return Ok(PathBuf::from(value));
    }
    Ok(home_dir()?.join(".hermes"))
}

fn hermes_agent_root() -> Result<PathBuf, String> {
    Ok(hermes_root()?.join("hermes-agent"))
}

fn hermes_python() -> Result<PathBuf, String> {
    let root = hermes_agent_root()?;
    let candidates = [
        root.join("venv/bin/python"),
        root.join("venv/bin/python3"),
        root.join("venv/Scripts/python.exe"),
    ];
    candidates
        .into_iter()
        .find(|path| path.is_file())
        .ok_or_else(|| "Hermes 运行时尚未安装".to_string())
}

fn codex_candidates() -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(value) = std::env::var_os("CODEX_BINARY") {
        candidates.push(PathBuf::from(value));
    }
    if let Some(path) = std::env::var_os("PATH") {
        for dir in std::env::split_paths(&path) {
            candidates.push(dir.join(if cfg!(windows) { "codex.exe" } else { "codex" }));
        }
    }
    candidates.extend([
        PathBuf::from("/Applications/ChatGPT.app/Contents/Resources/codex"),
        PathBuf::from("/opt/homebrew/bin/codex"),
        PathBuf::from("/usr/local/bin/codex"),
    ]);
    if let Ok(home) = home_dir() {
        candidates.push(home.join("Applications/ChatGPT.app/Contents/Resources/codex"));
        candidates.push(home.join(".local/bin/codex"));
        candidates.push(home.join(".npm-global/bin/codex"));
        candidates.push(home.join("AppData/Roaming/npm/codex.cmd"));
    }

    let mut seen = HashSet::new();
    candidates
        .into_iter()
        .filter(|path| seen.insert(path.clone()))
        .collect()
}

fn parse_version(text: &str) -> Option<(u32, u32, u32)> {
    let re = regex::Regex::new(r"(\d+)\.(\d+)\.(\d+)").ok()?;
    let captures = re.captures(text)?;
    Some((
        captures.get(1)?.as_str().parse().ok()?,
        captures.get(2)?.as_str().parse().ok()?,
        captures.get(3)?.as_str().parse().ok()?,
    ))
}

fn command_output(binary: &Path, args: &[&str]) -> Option<Output> {
    Command::new(binary)
        .args(args)
        .stdin(Stdio::null())
        .output()
        .ok()
}

fn probe_codex() -> Option<CodexProbe> {
    for path in codex_candidates() {
        if !path.is_file() {
            continue;
        }
        let Some(output) = command_output(&path, &["--version"]) else {
            continue;
        };
        if !output.status.success() {
            continue;
        }
        let version_text = String::from_utf8_lossy(&output.stdout).trim().to_string();
        let Some(version) = parse_version(&version_text) else {
            continue;
        };
        let logged_in = command_output(&path, &["login", "status"])
            .filter(|value| value.status.success())
            .map(|value| {
                let combined = format!(
                    "{}\n{}",
                    String::from_utf8_lossy(&value.stdout),
                    String::from_utf8_lossy(&value.stderr)
                )
                .to_lowercase();
                combined.contains("logged in")
            })
            .unwrap_or(false);
        return Some(CodexProbe {
            path,
            version_text,
            version,
            logged_in,
        });
    }
    None
}

fn prepend_path(command: &mut Command, dirs: &[PathBuf]) {
    let mut all = dirs.to_vec();
    if let Some(existing) = std::env::var_os("PATH") {
        all.extend(std::env::split_paths(&existing));
    }
    if let Ok(value) = std::env::join_paths(all) {
        command.env("PATH", value);
    }
}

fn parse_last_json_line<T: for<'de> Deserialize<'de>>(stdout: &[u8]) -> Result<T, String> {
    let text = String::from_utf8_lossy(stdout);
    for line in text.lines().rev() {
        let trimmed = line.trim();
        if trimmed.starts_with('{') {
            if let Ok(value) = serde_json::from_str(trimmed) {
                return Ok(value);
            }
        }
    }
    Err(format!("无法解析 Hermes 返回结果: {}", text.trim()))
}

fn run_hermes_helper(
    action: &str,
    codex_path: Option<&Path>,
    requested_model: Option<&str>,
) -> Result<HermesState, String> {
    let python = hermes_python()?;
    let root = hermes_agent_root()?;
    let mut command = Command::new(&python);
    command
        .arg("-c")
        .arg(HERMES_HELPER)
        .arg(action)
        .current_dir(&root);
    if let Some(model) = requested_model {
        command.arg(model);
    }
    command.stdin(Stdio::null());

    let mut path_dirs = Vec::new();
    if let Some(parent) = codex_path.and_then(Path::parent) {
        path_dirs.push(parent.to_path_buf());
    }
    if let Some(parent) = python.parent() {
        path_dirs.push(parent.to_path_buf());
    }
    prepend_path(&mut command, &path_dirs);

    let output = command
        .output()
        .map_err(|error| format!("启动 Hermes 配置器失败: {error}"))?;

    // ⚠ 顺序要紧: 先判退出码, 再解析 JSON。
    //
    // 原来是反的 —— `parse_last_json_line(&output.stdout)?` 写在 status 检查
    // 之前。于是 helper 一旦崩掉:
    //   · stdout 是空的 (traceback 全在 stderr)
    //   · parse_last_json_line 返回 Err("无法解析 Hermes 返回结果: ") —— 冒号
    //     后面什么都没有
    //   · `?` 当场返回, 下面这段专门从 stderr 拼错误的代码**永远走不到**
    // 净效果是 Hermes 侧的任何故障, 用户和排查的人拿到的都是同一句没有信息量
    // 的空错误, 而真正的 traceback 就在手边却被丢掉了。
    if !output.status.success() {
        // 先看 helper 自己有没有结构化地说明原因 (已知失败它会打 JSON),
        // 没有再退到 stderr —— traceback 在那儿。
        let structured = parse_last_json_line::<HermesState>(&output.stdout)
            .ok()
            .and_then(|state| state.result.map(|value| value.message))
            .filter(|value| !value.trim().is_empty());
        let details = structured
            .unwrap_or_else(|| tail_for_error(&String::from_utf8_lossy(&output.stderr)));
        return Err(if details.is_empty() {
            format!("Hermes 配置器异常退出 ({}), 而且没有任何输出", output.status)
        } else {
            format!("Hermes 配置器失败 ({}): {details}", output.status)
        });
    }
    parse_last_json_line(&output.stdout)
}

/// 截出错误尾部给前端看。
///
/// 只留末尾是因为 Python traceback 最后几行才是真正的原因。设上限有两层理由:
/// 一是前端弹一屏日志没人看; 二是 helper 处理的 hermes 配置里含 `api_key`,
/// 将来某个 traceback 若打出 config 的 repr, 不设限就是原样透传到界面上。
fn tail_for_error(text: &str) -> String {
    const MAX: usize = 1200;
    let trimmed = text.trim();
    if trimmed.chars().count() <= MAX {
        return trimmed.to_string();
    }
    let tail: String = trimmed
        .chars()
        .skip(trimmed.chars().count().saturating_sub(MAX))
        .collect();
    format!("…(前面已截断)\n{tail}")
}

#[cfg(unix)]
#[derive(Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ShimMarker {
    path: String,
    target: String,
}

/// Hermes 的 launchd PATH 稳定包含它自己的 venv/bin。GUI 应用却经常
/// 看不到 Homebrew/npm/ChatGPT.app 里的 Codex，因此在该目录创建一个
/// 可追踪的 symlink。绝不覆盖现有文件。
#[cfg(unix)]
fn ensure_hermes_codex_shim(codex_path: &Path) -> Result<Option<PathBuf>, String> {
    use std::os::unix::fs::symlink;

    let python = hermes_python()?;
    let bin_dir = python
        .parent()
        .ok_or_else(|| "Hermes venv 路径异常".to_string())?;
    let shim = bin_dir.join("codex");
    if shim.exists() || shim.symlink_metadata().is_ok() {
        return Ok(None);
    }
    symlink(codex_path, &shim).map_err(|error| format!("创建 Codex 兼容链接失败: {error}"))?;

    let catfish_dir = home_dir()?.join(".catfish");
    std::fs::create_dir_all(&catfish_dir)
        .map_err(|error| format!("创建 Catfish 状态目录失败: {error}"))?;
    let marker_path = catfish_dir.join("codex-backend-shim.json");
    let marker = ShimMarker {
        path: shim.to_string_lossy().to_string(),
        target: codex_path.to_string_lossy().to_string(),
    };
    let bytes = serde_json::to_vec_pretty(&marker).map_err(|error| error.to_string())?;
    if let Err(error) = std::fs::write(&marker_path, bytes) {
        let _ = std::fs::remove_file(&shim);
        return Err(format!("记录 Codex 兼容链接失败: {error}"));
    }
    Ok(Some(shim))
}

#[cfg(not(unix))]
fn ensure_hermes_codex_shim(_codex_path: &Path) -> Result<Option<PathBuf>, String> {
    Ok(None)
}

#[cfg(unix)]
fn remove_hermes_codex_shim() {
    let Ok(home) = home_dir() else {
        return;
    };
    let marker_path = home.join(".catfish/codex-backend-shim.json");
    let Ok(bytes) = std::fs::read(&marker_path) else {
        return;
    };
    let Ok(marker) = serde_json::from_slice::<ShimMarker>(&bytes) else {
        return;
    };
    let path = PathBuf::from(&marker.path);
    let target = PathBuf::from(&marker.target);
    let points_to_managed_target = std::fs::read_link(&path)
        .map(|value| value == target)
        .unwrap_or(false);
    if points_to_managed_target {
        let _ = std::fs::remove_file(path);
        let _ = std::fs::remove_file(marker_path);
    }
}

#[cfg(not(unix))]
fn remove_hermes_codex_shim() {}

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

/// Hermes API server 通不通。
///
/// ⚠ 地址必须走 `hermes_api_config()`, 不能写死 `127.0.0.1:8642`。
///
/// 那一层已经支持 `~/.catfish/companion.yaml` 的 `hermes_api.url` 和环境变量
/// `CATFISH_HERMES_API_URL` 覆盖。写死的后果在改过端口的机器上是**双重**的:
///   · 这个函数恒 false → Codex 模型在 picker 里恒灰, 而理由说的是别的
///   · 每一次切模型 (含启动时的自动对齐) 都会走进 `restart_hermes_gateway()`
///     —— 一个最长约 80 秒的阻塞循环, 顺手把好好的 gateway 重启一遍
/// 而这台机器上 hermes 其实是好的, 只是不在默认端口。
fn hermes_api_ready() -> bool {
    use crate::services::hermes_api_config::hermes_api_config;

    let url = &hermes_api_config().url;
    // url 形如 http://localhost:8642。用 to_socket_addrs 而不是自己切字符串:
    // 主机名可能不是 IP (localhost / 内网 DNS 名), 直接 parse::<SocketAddr>
    // 对这些一律失败, 于是又变成恒 false。
    let Some(hostport) = url
        .split("://")
        .nth(1)
        .map(|rest| rest.split('/').next().unwrap_or(rest))
    else {
        log::warn!("hermes_api.url 不像个 URL, 无法探活: {url}");
        return false;
    };
    let with_port = if hostport.contains(':') {
        hostport.to_string()
    } else if url.starts_with("https") {
        format!("{hostport}:443")
    } else {
        format!("{hostport}:80")
    };
    use std::net::ToSocketAddrs;
    match with_port.to_socket_addrs() {
        Ok(mut addrs) => addrs.any(|value| {
            std::net::TcpStream::connect_timeout(&value, Duration::from_millis(500)).is_ok()
        }),
        Err(error) => {
            log::warn!("解析 hermes API 地址失败 ({with_port}): {error}");
            false
        }
    }
}

fn parse_gateway_pids(stdout: &[u8]) -> HashSet<u32> {
    String::from_utf8_lossy(stdout)
        .lines()
        .filter_map(|line| line.trim().parse::<u32>().ok())
        .collect()
}

/// 读取当前 Hermes gateway 进程代次。单看 8642 端口不够：`gateway restart`
/// 返回后，旧进程还会短暂继续监听；如果此时就让 picker 解锁，第一条消息会被
/// 旧 runtime 接走，表现为 DeepSeek/Codex 随机“未知错误”。
fn gateway_process_ids() -> HashSet<u32> {
    #[cfg(unix)]
    {
        return Command::new("pgrep")
            .args(["-f", "[h]ermes_cli\\.main gateway run"])
            .output()
            .ok()
            .map(|output| parse_gateway_pids(&output.stdout))
            .unwrap_or_default();
    }
    #[cfg(not(unix))]
    {
        HashSet::new()
    }
}

/// 仅用于异常兜底。API server 每条消息都会创建新 AIAgent 并重新读取 runtime，
/// 正常模型切换无需重启整个 gateway；只有 gateway 本来就不健康时才走这里。
fn restart_hermes_gateway() -> Result<(), String> {
    let old_pids = gateway_process_ids();
    let was_ready = hermes_api_ready();
    let python = hermes_python()?;
    let root = hermes_root()?;
    let agent_root = hermes_agent_root()?;
    let mut command = Command::new(&python);
    command
        .args(["-m", "hermes_cli.main", "gateway", "restart"])
        .env("HERMES_HOME", &root)
        .current_dir(&agent_root)
        .stdin(Stdio::null());
    if let Some(parent) = python.parent() {
        prepend_path(&mut command, &[parent.to_path_buf()]);
    }
    let output = command
        .output()
        .map_err(|error| format!("重启 Hermes gateway 失败: {error}"))?;
    if !output.status.success() {
        let details = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if details.is_empty() {
            format!("Hermes gateway 重启失败 (exit {})", output.status)
        } else {
            details
        });
    }
    let mut saw_unavailable = !was_ready;
    for _ in 0..320 {
        let ready = hermes_api_ready();
        if !ready {
            saw_unavailable = true;
        }
        let current_pids = gateway_process_ids();
        let generation_changed = if old_pids.is_empty() {
            // pgrep 不可用时退化为“端口确实掉过再恢复”，仍不能把旧 listener
            // 误认成新 gateway。
            saw_unavailable
        } else {
            !current_pids.is_empty() && old_pids.is_disjoint(&current_pids)
        };

        if ready && generation_changed {
            // 连续两次探活，防止 launchd 刚拉起又因配置错误立即退出。
            std::thread::sleep(Duration::from_millis(750));
            let stable_pids = gateway_process_ids();
            let stable_generation = old_pids.is_empty()
                || (!stable_pids.is_empty() && old_pids.is_disjoint(&stable_pids));
            if stable_generation && hermes_api_ready() {
                return Ok(());
            }
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    Err("Hermes gateway 切换超时：旧进程未完整退出或新进程未稳定就绪".to_string())
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_codex_version_with_suffix() {
        assert_eq!(
            parse_version("codex-cli 0.146.0-alpha.9.2"),
            Some((0, 146, 0))
        );
    }

    #[test]
    fn rejects_unparseable_version() {
        assert_eq!(parse_version("codex nightly"), None);
    }

    #[test]
    fn parses_last_json_after_noise() {
        let value: HermesState = parse_last_json_line(
            b"migration note\n{\"runtime\":\"codex_app_server\",\"provider\":\"openai-codex\"}\n",
        )
        .expect("last JSON should parse");
        assert_eq!(value.runtime, "codex_app_server");
        assert_eq!(value.provider, "openai-codex");
    }

    #[test]
    fn parses_gateway_pid_generation() {
        assert_eq!(
            parse_gateway_pids(b"123\nnot-a-pid\n456\n"),
            HashSet::from([123, 456])
        );
    }

    #[test]
    #[ignore = "restarts the developer's live Hermes gateway"]
    fn live_gateway_restart_waits_for_a_new_stable_generation() {
        assert!(hermes_api_ready(), "Hermes gateway must be running");
        let before = gateway_process_ids();
        assert!(!before.is_empty(), "could not identify the old gateway PID");
        restart_hermes_gateway().expect("gateway should restart and become stable");
        let after = gateway_process_ids();
        assert!(!after.is_empty(), "new gateway PID should exist");
        assert!(before.is_disjoint(&after), "gateway generation did not change");
        assert!(hermes_api_ready(), "new gateway should answer health checks");
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn quotes_terminal_command_safely() {
        assert_eq!(shell_quote("/tmp/a'b/codex"), "'/tmp/a'\\''b/codex'");
        assert_eq!(apple_script_escape("a\\b\"c"), "a\\\\b\\\"c");
    }
}
