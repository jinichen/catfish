//! 专家机器人 profile 里那份插件也跟着同步 (10/30)。
//!
//! 专家机器人是 `hermes profile create --clone-from default` 建的 (expert_bots_profiles.rs),
//! 会把当时的 `plugins/` 一起复制到 `profiles/<id>/plugins/`。之后 Companion 启动同步
//! 只写 `~/.hermes/plugins/`, profile 里那份就一直停在建机器人那天的版本。
//!
//! 而主 gateway 进程会把 profile 那份也加载进来 (10/30 加探针实测: 同一个 pid 里
//! `plugins/catfish-xcatfish-user` 和 `profiles/expert-*/plugins/catfish-xcatfish-user`
//! 各 register 一次)。专家机器人的钩子 / 工具用的就是 profile 那份 —— 版本旧了,
//! 修过的 bug 在专家机器人那边照样在。
//!
//! 只同步 profile 里**已经有**这个插件的; 没有的不新建 (那是 profile 自己的选择)。
//! 健康软链 (开发机 deploy.sh) 照 `sync_baked_files` 的规矩不动。

use std::fs;
use std::path::Path;

use super::hermes_plugin::{hermes_home, sync_baked_files};

/// 同步所有 profile 里名为 `name` 的插件副本。返回有没有文件真的变了。
pub(super) fn sync_profile_copies(name: &str, files: &[(&str, &str)]) -> bool {
    match hermes_home() {
        Some(home) => sync_profile_copies_at(&home, name, files),
        None => false,
    }
}

fn sync_profile_copies_at(home: &Path, name: &str, files: &[(&str, &str)]) -> bool {
    let Ok(entries) = fs::read_dir(home.join("profiles")) else {
        return false;
    };
    let mut changed = false;
    for entry in entries.filter_map(Result::ok) {
        let target = entry.path().join("plugins").join(name);
        if fs::symlink_metadata(&target).is_err() {
            continue;
        }
        match sync_baked_files(&target, files) {
            Ok(c) => {
                if c {
                    log::info!("[profile-plugins] 同步 {} ✓", target.display());
                }
                changed |= c;
            }
            Err(e) => log::warn!("[profile-plugins] 同步 {} 失败 ({e:#}), 跳", target.display()),
        }
    }
    changed
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    const FILES: &[(&str, &str)] = &[("plugin.py", "NEW = 1\n")];

    #[test]
    fn 旧副本被刷新_没有插件的_profile_不新建() {
        let tmp = TempDir::new().unwrap();
        let home = tmp.path();
        let stale = home.join("profiles/expert-a/plugins/catfish-xcatfish-user");
        fs::create_dir_all(&stale).unwrap();
        fs::write(stale.join("plugin.py"), "OLD = 1\n").unwrap();
        fs::create_dir_all(home.join("profiles/expert-b/plugins")).unwrap();

        assert!(sync_profile_copies_at(home, "catfish-xcatfish-user", FILES));
        assert_eq!(fs::read_to_string(stale.join("plugin.py")).unwrap(), "NEW = 1\n");
        assert!(!home.join("profiles/expert-b/plugins/catfish-xcatfish-user").exists());
        // 第二次没变化, 不该触发重启
        assert!(!sync_profile_copies_at(home, "catfish-xcatfish-user", FILES));
    }

    #[test]
    fn 没有_profiles_目录不报错() {
        let tmp = TempDir::new().unwrap();
        assert!(!sync_profile_copies_at(tmp.path(), "catfish-xcatfish-user", FILES));
    }
}
