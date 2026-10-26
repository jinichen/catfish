//! 10/14 hermes v0.22: Companion 启动时给 hermes config.yaml 补 / 改几项鲶鱼的规定配置。
//!
//! v0.22 换了一批默认值, 不管的话员工机上会出事 (docs/HERMES-022-UPGRADE-ASSESSMENT.md「B」):
//!
//! | key | v0.22 默认 | 鲶鱼 | 为什么 |
//! |---|---|---|---|
//! | `sessions.auto_prune` | true | **false** | 90 天前的会话被删, 不认 Companion 的软删标记和任务关联 (实测留孤儿行) |
//! | `auth.adopt_external_logins` | true | **false** | 会去接管本机别的工具的登录 (Codex / Claude 等), 不该替员工做 |
//! | `security.allow_lazy_installs` | true | **false** | 用到某功能时现场 pip 装包 —— 离线机装不上还卡着, 也绕过了打包时锁的版本 |
//! | `nous.guest` | true | **false** | 没登录 Nous 也去试它的免费通道, 启动时一串 "Nous client unavailable" |
//! | `tools.connectors.enabled` | true | **false** + `agent.disabled_toolsets` 加 `connections` | 第三方连接器 (把数据接到外部 SaaS), 员工机不开放 |
//! | `plugins.entries.<鲶鱼插件>` | 未授权 | `granted_capabilities: [tools.override]` | 两个插件都替换内置 memory 工具, v0.22 起没授权就被拒, 记忆路由整个失效 |
//! | `agent.max_turns` | 不设 = **不限** | 90 (没配才补) | 一轮对话里模型最多连调多少次工具; 不限会把一个卡住的任务跑到天荒地老 |
//! | `approvals.mode` | smart | smart (没配才补, 写明) | 10/14 定: AI 先判断, 拿不准再问员工 |
//! | `approvals.smart_policy` | 空 | 公司规则 (空才补) | 给判断审批的 AI 的规则, 10/14 鸿波确认的措辞 |
//! | `approvals.timeout` | 300 秒 | 1800 (没配才补) | 10/26 鸿波定: 等员工批准 30 分钟。5 分钟开个会回来就作废, 只能从头重做 |
//!
//! 「强制」的几项每次启动都校正 (有人改回去也改回来) —— 都是安全 / 数据相关, 员工不该自己开;
//! 「没配才补」的尊重已有值 (运维可能有意调过)。config.yaml 不存在 (hermes 没装) → 不凭空造文件。
//! hermes 按进程读配置, 下次 hermes 重启生效。

use anyhow::{Context, Result};
use serde_yaml::{Mapping, Value as YamlValue};
use std::fs;
use std::path::{Path, PathBuf};

/// 给 smart 审批判断用的公司规则 (10/14 鸿波确认)。
pub const SMART_POLICY: &str = "读取本机文件、查询数据、生成报表的脚本可以批准; \
删除或覆盖文件、向外部地址发送数据、安装软件、修改系统配置、操作邮件 / 微信发送的, 一律 ESCALATE";

pub const MAX_TURNS: u64 = 90;

/// 等员工批准多久 (秒)。hermes 自己的默认是 300, 没配时按 300 算 (见 [`approval_timeout_s`])。
pub const APPROVAL_TIMEOUT_S: u64 = 1800;
const HERMES_DEFAULT_APPROVAL_TIMEOUT_S: u64 = 300;

/// 替换内置 memory 工具的两个鲶鱼插件。
const TOOL_OVERRIDE_PLUGINS: [&str; 2] = ["catfish-xcatfish-user", "catfish-memory"];
const TOOL_OVERRIDE_CAPABILITY: &str = "tools.override";

/// 强制的布尔项: (路径, 值)
const ENFORCED: [(&[&str], bool); 5] = [
    (&["sessions", "auto_prune"], false),
    (&["auth", "adopt_external_logins"], false),
    (&["security", "allow_lazy_installs"], false),
    (&["nous", "guest"], false),
    (&["tools", "connectors", "enabled"], false),
];

fn yaml_path() -> Result<PathBuf> {
    if let Ok(home) = std::env::var("HERMES_HOME") {
        return Ok(PathBuf::from(home).join("config.yaml"));
    }
    let home = crate::util::paths::home_env().context("找不到 HOME / USERPROFILE 环境变量")?;
    Ok(crate::services::catfish_paths::hermes_home_for(&PathBuf::from(home)).join("config.yaml"))
}

/// 沿路径取 (没有就建) 子 mapping; 中间有非 mapping 的值 (写坏了) 换成 mapping。
fn section<'a>(root: &'a mut Mapping, path: &[&str]) -> &'a mut Mapping {
    let mut node = root;
    for key in path {
        let entry = node
            .entry(YamlValue::from(*key))
            .or_insert_with(|| YamlValue::Mapping(Mapping::new()));
        if !entry.is_mapping() {
            *entry = YamlValue::Mapping(Mapping::new());
        }
        node = entry.as_mapping_mut().expect("刚确保是 mapping");
    }
    node
}

/// 设 `path` = value; 返回是否改了。
fn set_if_different(root: &mut Mapping, path: &[&str], value: YamlValue) -> bool {
    let (last, parents) = path.split_last().expect("路径非空");
    let sec = section(root, parents);
    let key = YamlValue::from(*last);
    if sec.get(&key) == Some(&value) {
        return false;
    }
    sec.insert(key, value);
    true
}

/// 没配 (或值为空) 才设; 返回是否改了。
fn set_if_missing(root: &mut Mapping, path: &[&str], value: YamlValue) -> bool {
    let (last, parents) = path.split_last().expect("路径非空");
    let sec = section(root, parents);
    let key = YamlValue::from(*last);
    let empty = match sec.get(&key) {
        None | Some(YamlValue::Null) => true,
        Some(YamlValue::String(s)) => s.trim().is_empty(),
        _ => false,
    };
    if !empty {
        return false;
    }
    sec.insert(key, value);
    true
}

/// 列表里没有就追加 (原来不是列表就换成列表); 返回是否改了。
fn ensure_in_list(root: &mut Mapping, path: &[&str], item: &str) -> bool {
    let (last, parents) = path.split_last().expect("路径非空");
    let sec = section(root, parents);
    let key = YamlValue::from(*last);
    let list = sec.entry(key).or_insert_with(|| YamlValue::Sequence(Vec::new()));
    if !list.is_sequence() {
        *list = YamlValue::Sequence(Vec::new());
    }
    let seq = list.as_sequence_mut().expect("刚确保是列表");
    if seq.iter().any(|v| v.as_str() == Some(item)) {
        return false;
    }
    seq.push(YamlValue::from(item));
    true
}

/// 把规定配置落到 config.yaml。返回改了哪些 key (空 = 没改)。
pub fn ensure_at(path: &Path) -> Result<Vec<String>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let raw = fs::read_to_string(path).with_context(|| format!("读 {}", path.display()))?;
    let mut root: YamlValue = if raw.trim().is_empty() {
        YamlValue::Mapping(Mapping::new())
    } else {
        // 解析失败不要覆盖 —— 那是 hermes 自己也读不了的文件, 写回去只会丢东西
        serde_yaml::from_str(&raw).with_context(|| format!("{} 不是合法 YAML", path.display()))?
    };
    let map = root.as_mapping_mut().context("config.yaml 顶层不是 mapping")?;
    let mut changed = Vec::new();

    for (keys, value) in ENFORCED {
        if set_if_different(map, keys, YamlValue::from(value)) {
            changed.push(keys.join("."));
        }
    }
    if ensure_in_list(map, &["agent", "disabled_toolsets"], "connections") {
        changed.push("agent.disabled_toolsets+connections".into());
    }
    for plugin in TOOL_OVERRIDE_PLUGINS {
        let base = ["plugins", "entries", plugin];
        if ensure_in_list(map, &[base[0], base[1], base[2], "granted_capabilities"], TOOL_OVERRIDE_CAPABILITY) {
            changed.push(format!("plugins.entries.{plugin}.granted_capabilities"));
        }
        // v0.22 自己记授权时也同时写这个旧键 (plugin_capabilities.record_consent), 跟它一致
        if set_if_different(map, &[base[0], base[1], base[2], "allow_tool_override"], YamlValue::from(true)) {
            changed.push(format!("plugins.entries.{plugin}.allow_tool_override"));
        }
    }
    if set_if_missing(map, &["agent", "max_turns"], YamlValue::from(MAX_TURNS)) {
        changed.push("agent.max_turns".into());
    }
    if set_if_missing(map, &["approvals", "mode"], YamlValue::from("smart")) {
        changed.push("approvals.mode".into());
    }
    if set_if_missing(map, &["approvals", "smart_policy"], YamlValue::from(SMART_POLICY)) {
        changed.push("approvals.smart_policy".into());
    }
    if set_if_missing(map, &["approvals", "timeout"], YamlValue::from(APPROVAL_TIMEOUT_S)) {
        changed.push("approvals.timeout".into());
    }

    if changed.is_empty() {
        return Ok(changed);
    }
    let out = serde_yaml::to_string(&root).context("序列化 config.yaml 失败")?;
    let tmp = path.with_extension("yaml.policy.tmp");
    fs::write(&tmp, out).with_context(|| format!("写 {}", tmp.display()))?;
    fs::rename(&tmp, path).with_context(|| format!("rename → {}", path.display()))?;
    Ok(changed)
}

/// 生产入口 (lib.rs setup 用)。
pub fn ensure() -> Result<Vec<String>> {
    ensure_at(&yaml_path()?)
}

/// hermes 实际等员工批准多久 (秒) —— 界面倒计时 / 超时提示跟它对齐。
/// 跟 hermes 读法一致 (tools/approval_context.py _get_approval_timeout): 读 `approvals.timeout`,
/// 没配 / 不是整数按 hermes 默认 300。hermes 的批准事件里不带这个数, 只能读配置。
pub fn approval_timeout_at(path: &Path) -> u64 {
    let Ok(raw) = fs::read_to_string(path) else {
        return HERMES_DEFAULT_APPROVAL_TIMEOUT_S;
    };
    let Ok(root) = serde_yaml::from_str::<YamlValue>(&raw) else {
        return HERMES_DEFAULT_APPROVAL_TIMEOUT_S;
    };
    match &root["approvals"]["timeout"] {
        YamlValue::Number(n) => n.as_u64().filter(|v| *v > 0).unwrap_or(HERMES_DEFAULT_APPROVAL_TIMEOUT_S),
        YamlValue::String(s) => s.trim().parse().ok().filter(|v| *v > 0).unwrap_or(HERMES_DEFAULT_APPROVAL_TIMEOUT_S),
        _ => HERMES_DEFAULT_APPROVAL_TIMEOUT_S,
    }
}

pub fn approval_timeout_s() -> u64 {
    yaml_path().map(|p| approval_timeout_at(&p)).unwrap_or(HERMES_DEFAULT_APPROVAL_TIMEOUT_S)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn cfg(tmp: &TempDir, body: &str) -> PathBuf {
        let p = tmp.path().join("config.yaml");
        fs::write(&p, body).unwrap();
        p
    }

    fn root(p: &Path) -> YamlValue {
        serde_yaml::from_str(&fs::read_to_string(p).unwrap()).unwrap()
    }

    #[test]
    fn missing_file_is_left_alone() {
        let tmp = TempDir::new().unwrap();
        let p = tmp.path().join("config.yaml");
        assert!(ensure_at(&p).unwrap().is_empty());
        assert!(!p.exists());
    }

    #[test]
    fn writes_everything_on_a_fresh_config_and_keeps_other_keys() {
        let tmp = TempDir::new().unwrap();
        let p = cfg(&tmp, "model:\n  provider: custom\n  base_url: http://127.0.0.1:8999/v1\ndisplay:\n  language: zh\n");
        let changed = ensure_at(&p).unwrap();
        assert!(changed.contains(&"sessions.auto_prune".to_string()), "{changed:?}");
        let r = root(&p);
        assert_eq!(r["sessions"]["auto_prune"].as_bool(), Some(false));
        assert_eq!(r["auth"]["adopt_external_logins"].as_bool(), Some(false));
        assert_eq!(r["security"]["allow_lazy_installs"].as_bool(), Some(false));
        assert_eq!(r["nous"]["guest"].as_bool(), Some(false));
        assert_eq!(r["tools"]["connectors"]["enabled"].as_bool(), Some(false));
        assert_eq!(r["agent"]["disabled_toolsets"][0].as_str(), Some("connections"));
        assert_eq!(r["agent"]["max_turns"].as_u64(), Some(90));
        assert_eq!(r["approvals"]["mode"].as_str(), Some("smart"));
        assert!(r["approvals"]["smart_policy"].as_str().unwrap().contains("ESCALATE"));
        assert_eq!(r["approvals"]["timeout"].as_u64(), Some(1800));
        assert_eq!(approval_timeout_at(&p), 1800);
        for plugin in TOOL_OVERRIDE_PLUGINS {
            let e = &r["plugins"]["entries"][plugin];
            assert_eq!(e["granted_capabilities"][0].as_str(), Some("tools.override"), "{plugin}");
            assert_eq!(e["allow_tool_override"].as_bool(), Some(true), "{plugin}");
        }
        // 原有的不动
        assert_eq!(r["model"]["provider"].as_str(), Some("custom"));
        assert_eq!(r["display"]["language"].as_str(), Some("zh"));
        // 第二次 no-op
        assert!(ensure_at(&p).unwrap().is_empty());
    }

    #[test]
    fn enforced_keys_are_flipped_back_but_tuned_ones_are_respected() {
        let tmp = TempDir::new().unwrap();
        let p = cfg(
            &tmp,
            "sessions:\n  auto_prune: true\nagent:\n  max_turns: 150\n  disabled_toolsets:\n  - browser\n\
             approvals:\n  mode: manual\n  smart_policy: 运维自己写的规则\n  timeout: 600\n",
        );
        ensure_at(&p).unwrap();
        let r = root(&p);
        assert_eq!(r["sessions"]["auto_prune"].as_bool(), Some(false), "安全项要改回来");
        assert_eq!(r["agent"]["max_turns"].as_u64(), Some(150), "运维调过的轮数不动");
        assert_eq!(r["approvals"]["mode"].as_str(), Some("manual"), "已配的审批模式不动");
        assert_eq!(r["approvals"]["smart_policy"].as_str(), Some("运维自己写的规则"));
        assert_eq!(approval_timeout_at(&p), 600, "运维调过的等待时长不动, 界面也按它倒计时");
        let toolsets: Vec<_> = r["agent"]["disabled_toolsets"]
            .as_sequence()
            .unwrap()
            .iter()
            .filter_map(|v| v.as_str())
            .collect();
        assert_eq!(toolsets, ["browser", "connections"], "原有的保留, 追加 connections");
    }

    #[test]
    fn existing_grants_are_kept_and_not_duplicated() {
        let tmp = TempDir::new().unwrap();
        let p = cfg(
            &tmp,
            "plugins:\n  enabled:\n  - catfish-xcatfish-user\n  entries:\n    catfish-memory:\n      \
             granted_capabilities:\n      - llm.model_override\n      - tools.override\n",
        );
        ensure_at(&p).unwrap();
        let r = root(&p);
        let grants = r["plugins"]["entries"]["catfish-memory"]["granted_capabilities"].as_sequence().unwrap();
        assert_eq!(grants.len(), 2);
        assert_eq!(r["plugins"]["enabled"][0].as_str(), Some("catfish-xcatfish-user"));
    }

    #[test]
    fn approval_timeout_falls_back_to_hermes_default() {
        let tmp = TempDir::new().unwrap();
        assert_eq!(approval_timeout_at(&tmp.path().join("none.yaml")), 300, "没文件");
        assert_eq!(approval_timeout_at(&cfg(&tmp, "model: {}\n")), 300, "没配");
        assert_eq!(approval_timeout_at(&cfg(&tmp, "approvals:\n  timeout: '900'\n")), 900, "写成字符串也认");
        assert_eq!(approval_timeout_at(&cfg(&tmp, "approvals:\n  timeout: abc\n")), 300);
        assert_eq!(approval_timeout_at(&cfg(&tmp, "model: [unclosed\n")), 300);
    }

    #[test]
    fn broken_yaml_is_not_overwritten() {
        let tmp = TempDir::new().unwrap();
        let p = cfg(&tmp, "model: [unclosed\n");
        assert!(ensure_at(&p).is_err());
        assert_eq!(fs::read_to_string(&p).unwrap(), "model: [unclosed\n");
    }
}
