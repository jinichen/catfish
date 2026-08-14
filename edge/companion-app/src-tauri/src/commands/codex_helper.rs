//! 跑 Hermes 自己 venv 里的 helper 脚本, 把 stdout 最后一行的 JSON 解出来。
//!
//! 2026-08-15 从 codex_backend.rs 切出来。纯搬迁, 逻辑一行未改。
//!
//! 为什么它必须单独一个文件: 那段 `HERMES_HELPER` 是 174 行内嵌 Python,
//! 占原文件的六分之一。方案里原本只切三块 (probe/shim/gateway), 但那样
//! codex_backend.rs 还剩 801 行 —— 仍然超。这一块不是可选的。

use serde::Deserialize;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

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
    # 对称于上面 enable 分支的 already_selected 短路 —— 8/10 补。
    #
    # 少这一条, 出事的不是 Codex 那边, 是**普通模型**: select 动作里
    #     action = "enable" if requested_model in available else "disable"
    # 于是员工每换一次网关模型 (deepseek / qwen ...) 都落到这个分支, 无条件
    # 调一次 crs.apply(config, "auto")。那是 hermes 的**运行时执行器**切换器,
    # 它返回的 requires_new_session 说的是"换执行器要新会话", 跟员工选的模型
    # 没有关系。Companion 原样回传这一位, 前端就弹出
    #     「这个模型要新开一个对话才会生效 —— 在当前对话继续发, 跑的还是原来那个」
    #
    # 而这句话在 8/9 P46 之后已经不成立: 会话级模型 override (state.db
    # sessions.model) 被砍掉了, picker 成为唯一真源, agent.model 在每条消息
    # 新建 agent 时都被覆盖一次 (见 catfish-xcatfish-user/model_authority.py)。
    # 员工照着提示新建对话, 丢掉整段上下文, 换来一件本来就已经成立的事。
    #
    # 一台从没启用过 Codex 的机器 (openai_runtime: auto, 连备份文件都没有)
    # 根本不存在"运行时切换"这回事, 这里就该如实说 False。
    #
    # 真从 Codex 切回来时照旧走 crs.apply, 信号如实回传 —— 那种时候执行器
    # 确实换了, 不能瞒着前端 (理由见 codex_backend_select_model 里那段注释)。
    if crs.get_current_runtime(config) != "codex_app_server":
        result = crs.CodexRuntimeStatus(
            success=True,
            new_value="auto",
            old_value="auto",
            message="already on default runtime",
            requires_new_session=False,
        )
    else:
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

#[derive(Debug, Deserialize, Default)]
pub(crate) struct HermesState {
    #[serde(default)]
    pub(crate) runtime: String,
    #[serde(default)]
    pub(crate) provider: String,
    #[serde(default)]
    pub(crate) model: String,
    #[serde(default)]
    pub(crate) models: Vec<String>,
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

pub(crate) fn prepend_path(command: &mut Command, dirs: &[PathBuf]) {
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

pub(crate) fn run_hermes_helper(
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

#[cfg(test)]
mod tests {
    use super::{parse_last_json_line, HermesState};

    #[test]
    fn parses_last_json_after_noise() {
        let value: HermesState = parse_last_json_line(
            b"migration note\n{\"runtime\":\"codex_app_server\",\"provider\":\"openai-codex\"}\n",
        )
        .expect("last JSON should parse");
        assert_eq!(value.runtime, "codex_app_server");
        assert_eq!(value.provider, "openai-codex");
    }
}
