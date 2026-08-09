//! `~/.hermes/.env` 里的 `API_SERVER_KEY` 维护 (8/9 从 hermes_plugin.rs 抽出)。
//!
//! # 为什么单独一个文件
//!
//! hermes_plugin.rs 933 行破了 800 红线。按拆分协议抽这一块 —— 它跟"同步 plugin
//! 文件"是**两件事**: 那边管 ~/.hermes/plugins/ 下的 .py, 这边管 ~/.hermes/.env
//! 里的一行 key。只是恰好都在 Companion 启动时做, 才长在一起。
//!
//! # 这个 key 决定"鲶鱼记不记得你"
//!
//! 聊天有两条路, 分界线就是它:
//!   有 key → 前端 useHermes=true → Companion → hermes → gateway
//!   没 key → Companion 直连 gateway, **不经 hermes**
//!
//! 而记忆是 hermes 侧写的 (catfish-memory 的 sync_turn 是 agent loop 钩子),
//! 不经 hermes 就不触发。配错了的表现是"一切正常但永远不积累记忆" —— 现场
//! 看不出来, 所以下面的测试必须留着。

use anyhow::{Context, Result};
use std::fs;
use std::path::PathBuf;

/// P3.5.82 (7/29): 保证 `~/.hermes/.env` 里有 `API_SERVER_KEY` + `API_SERVER_ENABLED`.
///
/// ── 为什么这一步决定"鲶鱼记不记得你" ────────────────────────────────
///
/// 聊天有两条路, 分界线就是这个 key:
///   有 key → `hermes_api_config` 的 `enabled = enabled_raw && key.is_some()` 成立
///            → 前端 `useHermes = true` → Companion → hermes → gateway
///   没 key → `enabled` 被强制 false → Companion 直连 gateway, **不经 hermes**
///
/// 而**记忆是 hermes 侧写的**: catfish-memory 的 `sync_turn` 是 hermes agent loop
/// 每轮结束后的钩子, 不经 hermes 就不触发; gateway 侧的写入能力 5/23 已主动删除
/// (memory_distill.py 747 行 + session_summarizer.py 527 行, 见 gateway app.py 注释),
/// 理由是"中央边缘分离, gateway 不再读写员工本机数据".
///
/// 于是没有 key 的机器上, 鲶鱼**能读旧记忆但永远不产生新记忆** —— 而新员工的
/// USER.md / memories/ 本来就是空的 (identity_bundle.rs: "员工个人数据, 没就是没").
/// 表现是"用起来一切正常, 但用多久都不会更懂你", 现场根本看不出哪里坏了。
///
/// 这个 key 原来只有 `scripts/setup-catfish-edge.sh` 会生成, 而那个脚本从自己所在
/// 的**仓库路径**推导依赖 (`CATFISH_REPO/edge/hermes-plugins/...`), 员工只有一个
/// dmg、没有源码, 结构上跑不了。所以边缘能力实际上从没进过员工安装包。
///
/// ── 幂等 ────────────────────────────────────────────────────────────
///
/// 已有 key **一律保留**, 不 rotate。setup-catfish-edge.sh 是每次生成新 key 的
/// (它的场景是"手动轮换"), 但装机路径不能这样: 换了 key 而正在跑的 hermes 内存里
/// 还是旧的, Companion 调 8642 直接 401, 而且要等 hermes 重启才自愈。
pub(crate) fn ensure_api_server_key() -> Result<()> {
    let home = crate::util::paths::home_env().context("拿 HOME")?;
    let hermes = PathBuf::from(&home).join(".hermes");
    if !hermes.exists() {
        log::debug!("[P3.5.82] {} 不存在 (hermes 未装) · skip", hermes.display());
        return Ok(());
    }
    let env_path = hermes.join(".env");
    let text = fs::read_to_string(&env_path).unwrap_or_default();

    let existing = text
        .lines()
        .find_map(|l| l.strip_prefix("API_SERVER_KEY="))
        .map(str::trim)
        .filter(|v| !v.is_empty());

    let key = match existing {
        Some(k) => {
            log::debug!("[P3.5.82] API_SERVER_KEY 已存在 (len={}) · 保留不换", k.len());
            k.to_string()
        }
        None => {
            let k = gen_api_server_key();
            log::info!("[P3.5.82] API_SERVER_KEY 不存在 · 已生成 (len={})", k.len());
            k
        }
    };

    let mut out = replace_or_append_env_line(&text, "API_SERVER_KEY", &key);
    out = replace_or_append_env_line(&out, "API_SERVER_ENABLED", "true");

    if out == text {
        return Ok(()); // 没变化就不写盘, 免得每次启动都动 mtime
    }
    fs::write(&env_path, out).with_context(|| format!("写 {}", env_path.display()))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&env_path, fs::Permissions::from_mode(0o600));
    }
    log::info!(
        "[P3.5.82] ✓ {} 已配 API_SERVER_KEY + API_SERVER_ENABLED=true \
         (hermes 下次启动生效, 之后聊天经 hermes, 记忆开始积累)",
        env_path.display()
    );
    Ok(())
}

/// 64 位十六进制随机 key, 跟 setup-catfish-edge.sh 的 `gen_key` 同规格.
fn gen_api_server_key() -> String {
    use rand::RngCore;
    let mut buf = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut buf);
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

/// dotenv 行级替换 / 追加. 保留注释和其它变量.
///
/// 跟 `server_config.rs` / `hermes_jwt_sync.rs` 里的同名函数是同一套语义 ——
/// 三处各有一份是既有的重复, 这次不顺手合并: 合并要动那两个已经验证过的调用点,
/// 交付前不做无关改动。合并这件事记在技术债里。
fn replace_or_append_env_line(text: &str, key: &str, value: &str) -> String {
    let prefix = format!("{key}=");
    let mut lines: Vec<String> = text.lines().map(str::to_string).collect();
    let mut replaced = false;
    for line in lines.iter_mut() {
        if line.starts_with(&prefix) {
            *line = format!("{key}={value}");
            replaced = true;
            break;
        }
    }
    if !replaced {
        lines.push(format!("{key}={value}"));
    }
    let mut out = lines.join("\n");
    if !out.ends_with('\n') {
        out.push('\n');
    }
    out
}

/// P3.5.82 (7/29): API_SERVER_KEY 装机配置的回归测试。
///
/// 这个 key 决定聊天走不走 hermes, 而记忆只在 hermes 那条路上写 —— 配错了的
/// 表现是"一切正常但永远不积累记忆", 现场看不出来, 所以必须有测试兜住。
#[cfg(test)]
mod tests_api_server_key {
    use super::replace_or_append_env_line;

    #[test]
    fn appends_when_absent() {
        // 员工机首装: install.sh 从模板 cp 的 .env 里没有这两项
        let input = "TAVILY_API_KEY=tvly-x\nAPI_SERVER_PORT=8642\n";
        let out = replace_or_append_env_line(input, "API_SERVER_KEY", "abc123");
        assert!(out.contains("API_SERVER_KEY=abc123"));
        assert!(out.contains("TAVILY_API_KEY=tvly-x"), "别的 key 被动了");
        assert!(out.contains("API_SERVER_PORT=8642"));
    }

    #[test]
    fn replaces_in_place_not_duplicate() {
        // 追加出两行的话 dotenv 取哪行看实现 —— 又是"看着配对了其实没生效"
        let input = "API_SERVER_KEY=old\nOTHER=1\n";
        let out = replace_or_append_env_line(input, "API_SERVER_KEY", "new");
        assert_eq!(out.matches("API_SERVER_KEY=").count(), 1);
        assert!(out.contains("API_SERVER_KEY=new"));
        assert!(!out.contains("old"));
    }

    #[test]
    fn keeps_comments_and_blank_structure() {
        // .env 里有注释说明各字段用途, 装机改写不该把它们吃掉
        let input = "# hermes API server\nAPI_SERVER_ENABLED=false\n# 上游 key\nTAVILY_API_KEY=x\n";
        let out = replace_or_append_env_line(input, "API_SERVER_ENABLED", "true");
        assert!(out.contains("# hermes API server"));
        assert!(out.contains("# 上游 key"));
        assert!(out.contains("API_SERVER_ENABLED=true"));
        assert!(!out.contains("API_SERVER_ENABLED=false"));
    }

    #[test]
    fn generated_key_is_64_hex() {
        // 跟 setup-catfish-edge.sh 的 gen_key 同规格 (32 字节 → 64 hex)
        let k = super::gen_api_server_key();
        assert_eq!(k.len(), 64, "长度跟脚本生成的不一致");
        assert!(k.chars().all(|c| c.is_ascii_hexdigit()));
        // 两次不能一样 —— 用死值等于所有员工共用一个密钥
        assert_ne!(k, super::gen_api_server_key());
    }

    #[test]
    fn empty_env_file_gets_both_keys() {
        // hermes 装了但 .env 是空文件 (install.sh 的 `touch` 分支)
        let out = replace_or_append_env_line("", "API_SERVER_KEY", "k");
        let out = replace_or_append_env_line(&out, "API_SERVER_ENABLED", "true");
        assert!(out.contains("API_SERVER_KEY=k"));
        assert!(out.contains("API_SERVER_ENABLED=true"));
        assert!(out.ends_with('\n'), "dotenv 末尾必须有换行");
    }
}

