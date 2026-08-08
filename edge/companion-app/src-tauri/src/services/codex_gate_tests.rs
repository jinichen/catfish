//! `codex_gate.rs` 的测试。
//!
//! 拆成独立文件是习惯做法 (见 hermes_jwt_sync 那两个), 主文件保持只讲"为什么"。
//!
//! 这里**不测** `codex_enabled()` 本身 —— 它读进程环境变量和 `$HOME/.hermes/.env`,
//! 在测试里改进程环境是全局副作用, 并行跑会互相打架 (本仓 util/test_env.rs 就是
//! 为这个存在的)。真正需要钉死的是两个纯函数的语义, 它们才是"会不会误开"的判据。

use super::*;

// ── parse_flag: 宁可误关不可误开 ──────────────────────────────────────

#[test]
fn 认得出开() {
    for raw in ["1", "true", "TRUE", "True", "yes", "YES", "on", "ON", " 1 ", "\"1\"", "'true'"] {
        assert!(parse_flag(raw), "{raw:?} 应该算开");
    }
}

#[test]
fn 其余一律算关() {
    // 空串 / 0 / false 是显式关; 后面几个是**拼错或手滑**的形态 ——
    // 它们必须算关, 因为误开是数据静默出端, 没有任何现象。
    for raw in ["", " ", "0", "false", "no", "off", "enable", "ture", "y", "2", "-1", "null"] {
        assert!(!parse_flag(raw), "{raw:?} 应该算关");
    }
}

// ── flag_from_dotenv ─────────────────────────────────────────────────

#[test]
fn 从_dotenv_里读到() {
    let env = "API_SERVER_KEY=abc\nCATFISH_CODEX_ENABLED=1\nOTHER=x\n";
    assert_eq!(flag_from_dotenv(env), Some(true));
}

#[test]
fn dotenv_里没有这个键返回_none() {
    // None 跟 Some(false) 语义不同: None 才会让调用方继续走"默认关",
    // 而不是被当成"文件里明确写了关"。现在结果一样, 但以后若要加第三层
    // 兜底 (比如 companion.yaml), 这个区别就是对的。
    let env = "API_SERVER_KEY=abc\nOTHER=x\n";
    assert_eq!(flag_from_dotenv(env), None);
}

#[test]
fn 注释掉的行不算数() {
    // 这条是实用的: 开发机上常见的做法是把开关注释掉而不是删掉, 那时候必须算关。
    let env = "# CATFISH_CODEX_ENABLED=1\nAPI_SERVER_KEY=abc\n";
    assert_eq!(flag_from_dotenv(env), None);
}

#[test]
fn 键名只匹配整个前缀_不被相似键名骗到() {
    // MY_CATFISH_CODEX_ENABLED / CATFISH_CODEX_ENABLED_EXTRA 都不该命中。
    let env = "MY_CATFISH_CODEX_ENABLED=1\nCATFISH_CODEX_ENABLED_EXTRA=1\n";
    assert_eq!(flag_from_dotenv(env), None);
}

#[test]
fn 显式写关也读得到() {
    assert_eq!(flag_from_dotenv("CATFISH_CODEX_ENABLED=0\n"), Some(false));
}

#[test]
fn 第一次出现的那行说了算() {
    // dotenv 的常见语义是后面覆盖前面, 但这里取第一条 —— 更保守: 有人在文件末尾
    // 手滑加了一行开, 不会悄悄覆盖掉前面明确写的关。
    assert_eq!(
        flag_from_dotenv("CATFISH_CODEX_ENABLED=0\nCATFISH_CODEX_ENABLED=1\n"),
        Some(false)
    );
}

// ── 文案 ─────────────────────────────────────────────────────────────

#[test]
fn 关闭说明里要讲清楚怎么打开() {
    let msg = disabled_reason();
    assert!(msg.contains(CODEX_ENABLED_KEY), "得说出键名: {msg}");
    assert!(msg.contains(".env"), "得说清楚改哪里: {msg}");
    // 光说"不可用"没有信息量 —— 必须说明为什么关着
    assert!(msg.contains("网关") || msg.contains("出端"), "得说明白原因: {msg}");
}
