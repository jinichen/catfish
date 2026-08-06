//! 确保 `~/.hermes/config.yaml` 里有 `memory.provider: catfish-memory`。
//!
//! # 为什么需要 (8/6 鸿波「早安里说的项目进度, 工作台不知道」)
//!
//! 查下来 catfish-memory 这个 memory provider **从来没被激活过**:
//!
//!   · 打包时 `build-mac-resources.sh:185` 只 `cp -R` 把插件拷进
//!     `plugins/memory/catfish-memory/` —— 文件到位
//!   · 激活逻辑在 `edge/hermes-plugins/install-catfish-memory.sh:122`
//!     (写 `memory.provider`)
//!   · 而 `hermes_install.rs` **一处都没调用那个脚本**
//!
//! 结果: 每台装过 Companion 的机器, `~/.hermes/config.yaml` 里根本没有
//! `memory:` 段, 长期记忆整个功能从未开启。而 `lib/chat.ts` 的注释一直写着
//! 「内部 memory inject (含 catfish-memory plugin)」—— 代码假定它在跑。
//!
//! 同一个根因还有第二个症状: `sync_turn` / `on_session_end` 钩子一次都没被调过,
//! `.catfish_memory_buffer.jsonl` 停在 7/18。8/4 那次是在 Rust 侧另加定时器
//! **绕过**了它, 没治根。
//!
//! # 为什么放启动路径而不是装机路径
//!
//! 放装机路径只能修新装的机器。放启动路径, **已经装好的机器重启一次
//! Companion 就自动修好** —— 达华那台不用重装。
//!
//! # 为什么自己拼字符串而不用 serde_yaml
//!
//! `services/curator_config.rs` 用的是 `serde_yaml` 反序列化 + 全量回写。
//! 那条路会**抹掉整份 config 的注释**。
//!
//! 8/6 实测: `install-catfish-memory.sh` 用 pyyaml 做同样的往返, 把
//! `~/.hermes/config.yaml` 里两整段注释文档抹了 ——「── Security ──」
//! (redact_secrets / tirith_* 说明) 和「── Fallback Model ──」(openrouter /
//! codex / nous / zai / kimi / minimax / bedrock 九个 provider 的配置模板)。
//! 功能没坏 (都是注释掉的配置), 但员工以后想开 fallback 就得重新查文档。
//!
//! 这里要写的只有两行, 犯不着为它冒抹掉整份文件注释的风险。纯文本追加,
//! 其余内容一个字节都不动。

use anyhow::{anyhow, Context, Result};
use std::fs;
use std::path::{Path, PathBuf};

const PROVIDER: &str = "catfish-memory";

/// 跟 `curator_config::yaml_path` 同款 —— 项目里没有 `dirs` crate, 且要保留
/// `HERMES_HOME` 这个测试重定向口子 (不污染真 `~/.hermes`)。
fn yaml_path() -> Result<PathBuf> {
    if let Ok(home) = std::env::var("HERMES_HOME") {
        return Ok(PathBuf::from(home).join("config.yaml"));
    }
    let home = crate::util::paths::home_env()
        .or_else(|_| std::env::var("USERPROFILE"))
        .map_err(|_| anyhow!("找不到 HOME 环境变量"))?;
    Ok(PathBuf::from(home).join(".hermes").join("config.yaml"))
}

/// 顶层是否已有 `memory:` 段。
///
/// 只认**行首顶格**的 `memory:` —— 嵌套在别的 key 下面的 `  memory:` 不算,
/// 否则会把 `mcp_servers: { catfish-tools: { memory: ... } }` 这类误判成已配置。
fn has_memory_section(raw: &str) -> bool {
    raw.lines()
        .any(|l| l == "memory:" || l.starts_with("memory:"))
}

/// 确保 `memory.provider` 存在。
///
/// - `Ok(true)`  这次写入了
/// - `Ok(false)` 已有 `memory:` 段 —— **不动**。员工可能自己配了 mem0 / honcho
///   之类, 尊重他的选择, 跟 curator_config 同一个原则。
pub fn ensure_at(path: &Path) -> Result<bool> {
    let raw = if path.exists() {
        fs::read_to_string(path).with_context(|| format!("读 {} 失败", path.display()))?
    } else {
        // config.yaml 都不存在说明 hermes 还没装好, 这时候不该由我们凭空造一个
        return Ok(false);
    };

    if has_memory_section(&raw) {
        return Ok(false);
    }

    let mut out = raw;
    if !out.is_empty() && !out.ends_with('\n') {
        out.push('\n');
    }
    out.push_str(&format!("\nmemory:\n  provider: {PROVIDER}\n"));

    // 原子写 —— config.yaml 里有 JWT 和 mcp_servers 配置, 半截写挂会让 hermes
    // 整个起不来。tmp + rename 保证要么全新要么全旧。
    let tmp = path.with_extension("yaml.catfish-tmp");
    fs::write(&tmp, &out).with_context(|| format!("写 {} 失败", tmp.display()))?;
    fs::rename(&tmp, path).with_context(|| format!("替换 {} 失败", path.display()))?;
    Ok(true)
}

pub fn ensure() -> Result<bool> {
    ensure_at(&yaml_path()?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn write(dir: &TempDir, body: &str) -> PathBuf {
        let p = dir.path().join("config.yaml");
        fs::write(&p, body).unwrap();
        p
    }

    #[test]
    fn writes_when_missing() {
        let d = TempDir::new().unwrap();
        let p = write(&d, "plugins:\n  enabled:\n  - catfish-xcatfish-user\n");
        assert!(ensure_at(&p).unwrap());
        let out = fs::read_to_string(&p).unwrap();
        assert!(out.contains("memory:\n  provider: catfish-memory"));
        // 幂等: 第二次不动
        assert!(!ensure_at(&p).unwrap());
    }

    #[test]
    fn does_not_touch_user_chosen_provider() {
        // 员工自己配了别的 provider → 尊重, 不覆盖
        let d = TempDir::new().unwrap();
        let p = write(&d, "memory:\n  provider: mem0\n");
        assert!(!ensure_at(&p).unwrap());
        assert!(fs::read_to_string(&p).unwrap().contains("mem0"));
    }

    #[test]
    fn preserves_comments_and_everything_else() {
        // ★ 这条是这个模块存在的理由 —— serde_yaml 回写会把注释全抹掉。
        let d = TempDir::new().unwrap();
        let original = "# ── Security ──\n# security:\n#   redact_secrets: true\n\
                        \nplugins:\n  enabled:\n  - catfish-xcatfish-user\n\
                        \n# ── Fallback Model ──\n# fallback_model:\n#   provider: openrouter\n";
        let p = write(&d, original);
        assert!(ensure_at(&p).unwrap());
        let out = fs::read_to_string(&p).unwrap();
        // 原文一个字节不少, 只在末尾追加
        assert!(out.starts_with(original), "原有内容被改动了");
        assert!(out.contains("# ── Security ──"));
        assert!(out.contains("# ── Fallback Model ──"));
        assert!(out.contains("#   provider: openrouter"));
    }

    #[test]
    fn nested_memory_key_does_not_count() {
        // 嵌套的 memory: 不能被误判成顶层已配置
        let d = TempDir::new().unwrap();
        let p = write(&d, "mcp_servers:\n  catfish-tools:\n    memory: true\n");
        assert!(ensure_at(&p).unwrap());
        assert!(fs::read_to_string(&p)
            .unwrap()
            .contains("provider: catfish-memory"));
    }

    #[test]
    fn missing_file_is_noop() {
        // hermes 还没装好时不凭空造 config
        let d = TempDir::new().unwrap();
        let p = d.path().join("nope.yaml");
        assert!(!ensure_at(&p).unwrap());
        assert!(!p.exists());
    }

    #[test]
    fn appends_newline_when_file_lacks_trailing_one() {
        let d = TempDir::new().unwrap();
        let p = write(&d, "plugins:\n  enabled: []"); // 无末尾换行
        assert!(ensure_at(&p).unwrap());
        let out = fs::read_to_string(&p).unwrap();
        assert!(out.contains("enabled: []\n\nmemory:\n"), "换行没补上: {out:?}");
    }
}
