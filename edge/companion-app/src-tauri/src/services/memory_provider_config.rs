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

/// 9/17 鸿波: MEMORY.md 常年顶着 hermes 默认 2200 字符 (2199/2200), 15 条政企口径
/// 挤不下, 每加一条先删一条, 整理时还打转。翻一倍。只在 memory 段**没有**这两个
/// key 时补 —— 员工自己调过的值不动; 已有的 catfish 默认值也不会被将来改小的
/// 常量覆盖掉 (要改就改这里再删机器上的行, 这是有意的)。
const MEMORY_CHAR_LIMIT: u32 = 4000;
const USER_CHAR_LIMIT: u32 = 2000;

/// memory 段的行范围 [start, end): start 是 `memory:` 那行, end 是下一个顶层 key 或文件尾。
fn memory_section_range(lines: &[String]) -> Option<(usize, usize)> {
    let start = lines
        .iter()
        .position(|l| l == "memory:" || l.starts_with("memory:"))?;
    let end = lines
        .iter()
        .enumerate()
        .skip(start + 1)
        .find_map(|(idx, line)| {
            (!line.is_empty() && !line.starts_with(' ') && !line.starts_with('#')).then_some(idx)
        })
        .unwrap_or(lines.len());
    Some((start, end))
}

/// 在 memory 段末尾补缺失的字符上限。段内已有同名 key (不论值) 就不碰。
fn ensure_memory_char_limits(raw: &str) -> String {
    let mut lines: Vec<String> = raw.lines().map(str::to_owned).collect();
    let Some((start, end)) = memory_section_range(&lines) else {
        return raw.to_owned();
    };
    let has = |key: &str| {
        lines[start + 1..end]
            .iter()
            .any(|l| l.trim_start().starts_with(&format!("{key}:")))
    };
    let mut add: Vec<String> = Vec::new();
    if !has("memory_char_limit") {
        add.push(format!("  memory_char_limit: {MEMORY_CHAR_LIMIT}"));
    }
    if !has("user_char_limit") {
        add.push(format!("  user_char_limit: {USER_CHAR_LIMIT}"));
    }
    if add.is_empty() {
        return raw.to_owned();
    }
    // 插在段内最后一个非空行之后, 让段尾的空行仍留在段尾
    let mut insert_at = end;
    while insert_at > start + 1 && lines[insert_at - 1].trim().is_empty() {
        insert_at -= 1;
    }
    lines.splice(insert_at..insert_at, add);
    lines.join("\n") + "\n"
}

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

/// 判断顶层 memory provider 是否确实是 catfish-memory。
fn has_catfish_memory_provider(raw: &str) -> bool {
    let mut in_memory = false;
    for line in raw.lines() {
        if line == "memory:" || line.starts_with("memory:") {
            in_memory = true;
            continue;
        }
        if in_memory && !line.starts_with(' ') && !line.starts_with('#') && !line.is_empty() {
            in_memory = false;
        }
        if in_memory && line.trim() == "provider: catfish-memory" {
            return true;
        }
    }
    false
}

/// 判断 plugins.entries.catfish-memory 是否允许覆盖 builtin memory 工具。
fn has_catfish_memory_override(raw: &str) -> bool {
    raw.contains("    catfish-memory:\n      allow_tool_override: true")
}

/// 在不重写 YAML 的前提下，补上 catfish-memory 的工具覆盖权限。
fn ensure_catfish_memory_override(raw: &str) -> String {
    if has_catfish_memory_override(raw) {
        return raw.to_owned();
    }

    let mut lines: Vec<String> = raw.lines().map(str::to_owned).collect();
    let plugin_idx = lines
        .iter()
        .position(|line| line == "plugins:" || line.starts_with("plugins:"));
    let Some(plugin_idx) = plugin_idx else {
        if !lines.is_empty() && lines.last().is_some_and(|line| !line.is_empty()) {
            lines.push(String::new());
        }
        lines.extend([
            "plugins:".to_owned(),
            "  entries:".to_owned(),
            "    catfish-memory:".to_owned(),
            "      allow_tool_override: true".to_owned(),
        ]);
        return lines.join("\n") + "\n";
    };

    if let Some(entries_idx) = lines
        .iter()
        .enumerate()
        .skip(plugin_idx + 1)
        .find_map(|(idx, line)| (line == "  entries:").then_some(idx))
    {
        if let Some(catfish_idx) = lines
            .iter()
            .enumerate()
            .skip(entries_idx + 1)
            .find_map(|(idx, line)| (line == "    catfish-memory:").then_some(idx))
        {
            let block_end = lines
                .iter()
                .enumerate()
                .skip(catfish_idx + 1)
                .find_map(|(idx, line)| {
                    (!line.is_empty() && line.starts_with("    ") && !line.starts_with("      "))
                        .then_some(idx)
                })
                .unwrap_or(lines.len());
            if let Some(allow_idx) = lines[catfish_idx + 1..block_end]
                .iter()
                .position(|line| line.trim_start().starts_with("allow_tool_override:"))
            {
                lines[catfish_idx + 1 + allow_idx] =
                    "      allow_tool_override: true".to_owned();
            } else {
                lines.insert(
                    catfish_idx + 1,
                    "      allow_tool_override: true".to_owned(),
                );
            }
            return lines.join("\n") + "\n";
        }
        lines.splice(
            entries_idx + 1..entries_idx + 1,
            [
                "    catfish-memory:".to_owned(),
                "      allow_tool_override: true".to_owned(),
            ],
        );
        return lines.join("\n") + "\n";
    }

    let section_end = lines
        .iter()
        .enumerate()
        .skip(plugin_idx + 1)
        .find_map(|(idx, line)| {
            (!line.is_empty() && !line.starts_with(' ') && !line.starts_with('#')).then_some(idx)
        })
        .unwrap_or(lines.len());
    lines.splice(
        section_end..section_end,
        [
            "  entries:".to_owned(),
            "    catfish-memory:".to_owned(),
            "      allow_tool_override: true".to_owned(),
        ],
    );
    lines.join("\n") + "\n"
}

/// 确保 `memory.provider` 存在。
///
/// - `Ok(true)`  这次写入了
/// - 其他 provider 已存在时不动；catfish-memory 已存在时只补它自己的工具权限。
///   员工可能自己配了 mem0 / honcho 之类, 继续尊重他的选择。
pub fn ensure_at(path: &Path) -> Result<bool> {
    let raw = if path.exists() {
        fs::read_to_string(path).with_context(|| format!("读 {} 失败", path.display()))?
    } else {
        // config.yaml 都不存在说明 hermes 还没装好, 这时候不该由我们凭空造一个
        return Ok(false);
    };

    let mut out = if has_memory_section(&raw) {
        if !has_catfish_memory_provider(&raw) {
            return Ok(false);
        }
        raw.clone()
    } else {
        let mut appended = raw.clone();
        if !appended.is_empty() && !appended.ends_with('\n') {
            appended.push('\n');
        }
        appended.push_str(&format!("\nmemory:\n  provider: {PROVIDER}\n"));
        appended
    };

    if !has_catfish_memory_override(&out) {
        out = ensure_catfish_memory_override(&out);
    }
    out = ensure_memory_char_limits(&out);
    if out == raw {
        return Ok(false);
    }

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
        assert!(out.contains("enabled: []\n\n  entries:\n"), "换行没补上: {out:?}");
        assert!(out.contains("memory:\n  provider: catfish-memory"));
    }

    #[test]
    fn adds_override_permission_for_catfish_provider() {
        let d = TempDir::new().unwrap();
        let p = write(
            &d,
            "plugins:\n  enabled:\n  - catfish-xcatfish-user\n  entries:\n    catfish-xcatfish-user:\n      allow_tool_override: true\n\nmemory:\n  provider: catfish-memory\n",
        );
        assert!(ensure_at(&p).unwrap());
        let out = fs::read_to_string(&p).unwrap();
        assert!(out.contains("    catfish-memory:\n      allow_tool_override: true"));
        assert!(!ensure_at(&p).unwrap());
    }

    #[test]
    fn repairs_existing_disabled_catfish_override() {
        let d = TempDir::new().unwrap();
        let p = write(
            &d,
            "plugins:\n  entries:\n    catfish-memory:\n      allow_tool_override: false\n\nmemory:\n  provider: catfish-memory\n",
        );
        assert!(ensure_at(&p).unwrap());
        let out = fs::read_to_string(&p).unwrap();
        assert!(out.contains("allow_tool_override: true"));
        assert!(!out.contains("allow_tool_override: false"));
    }

    #[test]
    fn adds_char_limits_when_missing() {
        let d = TempDir::new().unwrap();
        let p = write(
            &d,
            "plugins:\n  entries:\n    catfish-memory:\n      allow_tool_override: true\n\nmemory:\n  provider: catfish-memory\n\nskills:\n  external_dirs: []\n",
        );
        assert!(ensure_at(&p).unwrap());
        let out = fs::read_to_string(&p).unwrap();
        assert!(out.contains("memory:\n  provider: catfish-memory\n  memory_char_limit: 4000\n  user_char_limit: 2000\n\nskills:"), "{out}");
        assert!(!ensure_at(&p).unwrap(), "第二次应幂等");
    }

    #[test]
    fn keeps_user_tuned_limits() {
        // 员工自己调过 (或以后我们想改默认) —— 已有的值一个字节都不动
        let d = TempDir::new().unwrap();
        let p = write(
            &d,
            "plugins:\n  entries:\n    catfish-memory:\n      allow_tool_override: true\n\nmemory:\n  provider: catfish-memory\n  memory_char_limit: 6000\n",
        );
        assert!(ensure_at(&p).unwrap()); // 只补缺的 user_char_limit
        let out = fs::read_to_string(&p).unwrap();
        assert!(out.contains("memory_char_limit: 6000"));
        assert!(!out.contains("memory_char_limit: 4000"));
        assert!(out.contains("user_char_limit: 2000"));
    }

    #[test]
    fn fresh_memory_section_gets_limits_too() {
        // 没有 memory 段 → 补 provider 的同时把上限一起带上
        let d = TempDir::new().unwrap();
        let p = write(&d, "plugins:\n  enabled: []\n");
        assert!(ensure_at(&p).unwrap());
        let out = fs::read_to_string(&p).unwrap();
        assert!(out.contains("memory:\n  provider: catfish-memory\n  memory_char_limit: 4000\n  user_char_limit: 2000\n"), "{out}");
    }

    #[test]
    fn does_not_add_catfish_override_for_other_provider() {
        let d = TempDir::new().unwrap();
        let p = write(&d, "plugins:\n  enabled: []\n\nmemory:\n  provider: mem0\n");
        assert!(!ensure_at(&p).unwrap());
        let out = fs::read_to_string(&p).unwrap();
        assert!(!out.contains("catfish-memory"));
    }
}
