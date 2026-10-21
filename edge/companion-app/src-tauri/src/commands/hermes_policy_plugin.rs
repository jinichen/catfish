//! 红线策略插件 catfish-policy: Companion 启动时自动装 + 启用 (10/21)。
//!
//! 插件源码在仓库 `plugins/catfish-policy/` (4/25 写的), 但一直只能手动软链 +
//! `hermes plugins enable`, 从没进过安装包 —— 员工机器上它一次都没跑过。
//! 10/21 回放近 30 天真实调用时顺带发现: 按原样装上会把全部 skill_manage 都拦掉
//! (匹配器忽略了 deny_args_pattern), 所以先修了匹配器和规则, 再接进来。
//!
//! 机制跟 catfish-xcatfish-user 完全一样 (`hermes_plugin::sync_baked_files` /
//! `ensure_named_plugin_enabled_at`): 健康软链不动 (开发机), 其他情况写烘焙文件;
//! config.yaml `plugins.enabled` 补上名字, 员工显式 disabled 就尊重;
//! 文件真变了由调用方统一重启一次 hermes。

use super::hermes_plugin::{
    config_yaml_path, ensure_named_plugin_enabled_at, hermes_home, sync_baked_files,
};

const POLICY_PLUGIN_NAME: &str = "catfish-policy";

/// 插件全部文件 (测试文件不发)。新增 .py / .yaml 必须同步加到这里,
/// 守护测试 `baked_覆盖仓库里所有运行文件` 会拦。
const POLICY_BAKED_FILES: &[(&str, &str)] = &[
    ("__init__.py", include_str!("../../../../../plugins/catfish-policy/__init__.py")),
    ("matchers.py", include_str!("../../../../../plugins/catfish-policy/matchers.py")),
    ("rules.yaml", include_str!("../../../../../plugins/catfish-policy/rules.yaml")),
    ("plugin.yaml", include_str!("../../../../../plugins/catfish-policy/plugin.yaml")),
];

/// 同步文件 + 启用。返回插件文件有没有真的变 (调用方据此决定重启 hermes)。
/// 失败只 warn, 不挂 Companion 启动, 也不影响 catfish-xcatfish-user。
pub fn bootstrap_policy_plugin() -> bool {
    let Some(home) = hermes_home() else {
        return false;
    };
    let changed = match sync_baked_files(&home.join("plugins").join(POLICY_PLUGIN_NAME), POLICY_BAKED_FILES) {
        Ok(c) => c,
        Err(e) => {
            log::warn!("[policy] 同步 {POLICY_PLUGIN_NAME} 失败 ({e:#}), 跳");
            return false;
        }
    };
    if let Some(cfg) = config_yaml_path() {
        match ensure_named_plugin_enabled_at(&cfg, POLICY_PLUGIN_NAME) {
            Ok(true) => log::info!("[policy] config.yaml plugins.enabled += {POLICY_PLUGIN_NAME}"),
            Ok(false) => {}
            Err(e) => log::warn!("[policy] config.yaml 改写失败 ({e:#}), {POLICY_PLUGIN_NAME} 可能不生效"),
        }
    }
    changed
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    #[test]
    fn baked_覆盖仓库里所有运行文件() {
        let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../plugins/catfish-policy");
        let Ok(entries) = fs::read_dir(&dir) else { return };
        let baked: Vec<&str> = POLICY_BAKED_FILES.iter().map(|(n, _)| *n).collect();
        let mut missing: Vec<String> = entries
            .flatten()
            .map(|e| e.file_name().to_string_lossy().to_string())
            .filter(|n| (n.ends_with(".py") || n.ends_with(".yaml")) && !n.starts_with("test_") && n != "conftest.py")
            .filter(|n| !baked.contains(&n.as_str()))
            .collect();
        missing.sort();
        assert!(missing.is_empty(), "这些文件在仓库里但没进 POLICY_BAKED_FILES, 装出去会缺: {missing:?}");
    }

    #[test]
    fn 同步后文件齐全_第二次不算变() {
        let tmp = TempDir::new().unwrap();
        let p = tmp.path().join("plugins").join(POLICY_PLUGIN_NAME);
        assert!(sync_baked_files(&p, POLICY_BAKED_FILES).unwrap());
        for (name, content) in POLICY_BAKED_FILES {
            assert!(!content.is_empty(), "{name} 烘焙内容空");
            assert_eq!(fs::read_to_string(p.join(name)).unwrap(), *content);
        }
        assert!(!sync_baked_files(&p, POLICY_BAKED_FILES).unwrap(), "内容一致却报变了 → 每次启动白重启 hermes");
    }

    #[test]
    fn 启用时保留已有插件_尊重_disabled() {
        let tmp = TempDir::new().unwrap();
        let cfg = tmp.path().join("config.yaml");
        fs::write(&cfg, "plugins:\n  enabled:\n    - catfish-xcatfish-user\n").unwrap();
        assert!(ensure_named_plugin_enabled_at(&cfg, POLICY_PLUGIN_NAME).unwrap());
        let raw = fs::read_to_string(&cfg).unwrap();
        assert!(raw.contains("catfish-xcatfish-user") && raw.contains(POLICY_PLUGIN_NAME), "{raw}");
        assert!(!ensure_named_plugin_enabled_at(&cfg, POLICY_PLUGIN_NAME).unwrap());

        fs::write(&cfg, "plugins:\n  enabled: []\n  disabled:\n    - catfish-policy\n").unwrap();
        assert!(!ensure_named_plugin_enabled_at(&cfg, POLICY_PLUGIN_NAME).unwrap(), "员工关掉的不能强开");
    }
}
