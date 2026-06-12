//! BL-ADVISOR-PROFILE (5/21 Phase 7 第 1 步): 员工职级 + 画像自动识别.
//!
//! 设计稿: docs/CATFISH-ADVISOR-DESIGN.md §2
//!
//! 这一层 Rust 只负责:
//!   - profile.json schema (serde)
//!   - 读写 ~/.catfish/profile.json
//!   - 标"识别错了" (写 profile_hints.md)
//!   - 判断 profile 是否过期 (一周复算)
//!
//! LLM 推断走前端 TS (src/lib/profile.ts) — TS 调 gateway loopback, 解析 JSON,
//! 调 profile_save 写回. Rust 端不持 HTTP 客户端, 复杂度低.
//!
//! 跟 BL-CENTRAL-EDGE 边界 (5/17): profile 是员工本机数据, 写 ~/.catfish/, 不出端.

use std::path::PathBuf;
use std::sync::Mutex;
use chrono::{DateTime, Duration, Utc};
use serde::{Deserialize, Serialize};

/// P3.3.50 (6/12 鸿波 "rename profile.json 失败 No such file or directory"):
/// 全局 Mutex 串行化 profile_save. 多场景并发 (advisor refresh + 后台 trigger +
/// AdvisorView mount) 会同时跑 profile_save, A 写 tmp → B 写 tmp 覆盖 → A rename
/// 走 tmp → B rename 找不到 tmp 报错. 弹到 UI 让员工以为大事 (实际数据已 A 保存).
/// 加锁后两个调用串行, 不互相覆盖 tmp. Mutex::new const since Rust 1.63 → 直 static.
static PROFILE_SAVE_LOCK: Mutex<()> = Mutex::new(());

/// 员工画像 (catfish 自动识别, 员工不直接编辑).
///
/// 见 docs/CATFISH-ADVISOR-DESIGN.md §2.3 完整 schema.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Profile {
    /// 职级三档.
    pub tier: Tier,

    /// 央国企信号强度 — 强时早安主菜会嵌入合规/政治扫描.
    pub central_state: CentralStateSignal,

    /// 员工关心风格 (合规优先 / 业务优先 / 关系优先 / 数字优先).
    pub style: String,

    /// 关键人脉, 最多 10 人. 早安"关键关系节点" 段会用.
    pub key_people: Vec<KeyPerson>,

    /// 重点项目, 最多 5 个. 主菜识别时优先级提升.
    pub key_projects: Vec<KeyProject>,

    /// LLM 推断置信度 [0.0, 1.0]. 低于 0.5 时 UI 提示员工"画像可能不准, 多用几天 catfish 自动校准".
    pub confidence: f32,

    /// 3-5 条引用具体语料的证据 (设置页透明展示给员工).
    pub evidence: Vec<String>,

    /// 5/21 cold start: 员工写作 / 沟通风格 (高级总结, 来自 catfish_style_fingerprint 输出
    /// + 邮件/日历观察). LLM 起草草稿时按此模仿. None = 没 fingerprint (cold start) 或推断挂.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub personality: Option<Personality>,

    /// 上次复算 ISO-8601.
    pub updated_at: String,

    /// 下次自动复算时间 ISO-8601 (默认 7 天后).
    pub next_recompute_at: String,
}

/// 员工写作 / 沟通风格 (5/21 cold start 补丁).
///
/// 设计取舍: 不复制 style_fingerprint 全部字段 (太细, 喂 LLM 浪费 token).
/// 抽 LLM 起草草稿时能直接用的"高级总结".
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Personality {
    /// 句长倾向. 来自 stats.avg_sentence_len.
    pub verbosity: Verbosity,

    /// 结构倾向. 来自 structure_pref (list_ratio vs prose_ratio).
    pub structure: Structure,

    /// 正式度. 来自 punctuation + top_words + 邮件称呼.
    pub formality: Formality,

    /// 5-10 个高频实词 — LLM 起草时模仿用. 例 ["资质", "风控", "合规"].
    pub signature_words: Vec<String>,

    /// 1-3 句员工历史样本 — LLM 直接模仿语气.
    pub sample_sentences: Vec<String>,

    /// fingerprint 来源文档数. 0 = cold start (没 fingerprint).
    pub source_count: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Verbosity {
    Concise,
    Balanced,
    Verbose,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Structure {
    ListHeavy,
    Balanced,
    ProseHeavy,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Formality {
    Formal,
    Balanced,
    Casual,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Tier {
    Frontline,
    Mid,
    Senior,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum CentralStateSignal {
    None,
    Weak,
    Strong,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KeyPerson {
    pub name: String,
    /// "上级" / "客户" / "下属" / "平级" / "同事" / "兄弟单位" 等.
    pub relation: String,
    /// optional: 关联项目, e.g. "项目 A".
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub project: Option<String>,
    /// optional: 最后联系 ISO-8601 日期, 给"关键关系节点"段判用.
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub last_contact: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct KeyProject {
    pub name: String,
    /// "进行中" / "暂停" / "完成" / "待启动" 等.
    pub status: String,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub client: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub deadline: Option<String>,
}

// ── 文件路径 helpers ──────────────────────────────────────────────

fn catfish_dir() -> Result<PathBuf, String> {
    let home = std::env::var("HOME").map_err(|e| format!("HOME 未设: {e}"))?;
    Ok(PathBuf::from(home).join(".catfish"))
}

fn profile_path() -> Result<PathBuf, String> {
    Ok(catfish_dir()?.join("profile.json"))
}

fn profile_hints_path() -> Result<PathBuf, String> {
    Ok(catfish_dir()?.join("profile_hints.md"))
}

// ── Tauri commands ────────────────────────────────────────────────

/// 读 ~/.catfish/profile.json. 不存在返 None (调用方决定是否触发 recompute).
#[tauri::command]
pub async fn profile_get() -> Result<Option<Profile>, String> {
    let path = profile_path()?;
    if !path.exists() {
        return Ok(None);
    }
    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("读 profile.json 失败: {e}"))?;
    let p: Profile = serde_json::from_str(&text)
        .map_err(|e| format!("解析 profile.json 失败 (schema 不匹配?): {e}"))?;
    Ok(Some(p))
}

/// 前端 TS 调 gateway LLM 推断完, 调这个写回. 原子写 (tmp + rename), 防半途崩.
/// P3.3.50 (6/12 鸿波): 加 PROFILE_SAVE_LOCK 串行化 — 防多个并发 saver 互相覆盖
/// tmp 文件导致 rename 失败"No such file or directory". 锁 OK 失败也兜底友好降级.
#[tauri::command]
pub async fn profile_save(profile: Profile) -> Result<(), String> {
    // 拿锁串行 — async fn 里同步 Mutex 拿短锁 (写文件 ms 级) 不阻塞 tokio 太久
    let _guard = PROFILE_SAVE_LOCK.lock().map_err(|e| {
        format!("PROFILE_SAVE_LOCK 中毒: {e} (前次 panic 留下的, 应该不会)")
    })?;

    let dir = catfish_dir()?;
    std::fs::create_dir_all(&dir)
        .map_err(|e| format!("创建 ~/.catfish/ 失败: {e}"))?;

    let target = profile_path()?;
    // P3.3.50: tmp 文件名加 nanos 时间戳, 即使锁失败也尽量不撞 (双保险)
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.subsec_nanos())
        .unwrap_or(0);
    let tmp = target.with_extension(format!("json.tmp.{nanos}"));

    let text = serde_json::to_string_pretty(&profile)
        .map_err(|e| format!("serialize profile 失败: {e}"))?;
    std::fs::write(&tmp, text)
        .map_err(|e| format!("写 {} 失败: {e}", tmp.display()))?;
    std::fs::rename(&tmp, &target)
        .map_err(|e| format!("rename profile.json 失败: {e}"))?;
    Ok(())
}

/// 员工在设置页点"识别错了", 把原因 append 到 profile_hints.md.
/// 下次复算时 LLM 看到这条调整判断.
#[tauri::command]
pub async fn profile_mark_wrong(reason: String) -> Result<(), String> {
    let dir = catfish_dir()?;
    std::fs::create_dir_all(&dir)
        .map_err(|e| format!("创建 ~/.catfish/ 失败: {e}"))?;

    let path = profile_hints_path()?;
    let ts = Utc::now().to_rfc3339();
    let line = format!("- [{}] {}\n", ts, reason.trim());

    use std::io::Write;
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|e| format!("打开 profile_hints.md 失败: {e}"))?;
    file.write_all(line.as_bytes())
        .map_err(|e| format!("追加 profile_hints.md 失败: {e}"))?;
    Ok(())
}

/// 读 profile_hints.md 全文给 LLM 复算时用.
#[tauri::command]
pub async fn profile_hints_read() -> Result<String, String> {
    let path = profile_hints_path()?;
    if !path.exists() {
        return Ok(String::new());
    }
    std::fs::read_to_string(&path)
        .map_err(|e| format!("读 profile_hints.md 失败: {e}"))
}

/// 判断 profile 是否需要复算 (不存在 / 过期 / 占位 / 显式 force).
///
/// 调用场景:
///   - App 启动: 前端 useEffect 调一次, true 则后台 fire recomputeProfile()
///   - 设置页"重新识别"按钮: force=true
///   - 刷新按钮: force=true (5/22 修, 不让占位锁一周)
///
/// 5/22 修 cold start 锁死: 老逻辑只看 next_recompute_at, confidence=0 占位的 profile
/// (LLM 第一次挂时写的) 会锁一周, 即便上游 LLM 恢复了也不重试. 现加: confidence < 0.3
/// 视为"画像不可信", 任何调用都立即返 true 触发重算.
#[tauri::command]
pub async fn profile_needs_recompute(force: bool) -> Result<bool, String> {
    if force {
        return Ok(true);
    }
    let p = match profile_get().await? {
        Some(p) => p,
        None => return Ok(true),  // 不存在 → 复算
    };

    // 5/22 cold start 修: 低置信度 = 占位, 任何调用都重试
    if p.confidence < 0.3 {
        return Ok(true);
    }

    // next_recompute_at 解析失败 → 复算
    let next: DateTime<Utc> = match p.next_recompute_at.parse() {
        Ok(t) => t,
        Err(_) => return Ok(true),
    };
    Ok(Utc::now() >= next)
}

/// 算下次复算时间, 给前端写 profile 时用 (默认一周后).
#[tauri::command]
pub async fn profile_next_recompute_at(days: Option<i64>) -> Result<String, String> {
    let d = days.unwrap_or(7);
    Ok((Utc::now() + Duration::days(d)).to_rfc3339())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_profile() -> Profile {
        Profile {
            tier: Tier::Mid,
            central_state: CentralStateSignal::Strong,
            style: "合规优先".to_string(),
            key_people: vec![KeyPerson {
                name: "李局".to_string(),
                relation: "上级".to_string(),
                project: None,
                last_contact: Some("2026-05-19".to_string()),
            }],
            key_projects: vec![KeyProject {
                name: "项目 A".to_string(),
                status: "进行中".to_string(),
                client: Some("老李".to_string()),
                deadline: None,
            }],
            confidence: 0.82,
            evidence: vec![
                "projects.md 在跟 3 个项目".to_string(),
                "7 天对话频繁出现 班子会".to_string(),
            ],
            personality: None,  // 5/21 cold start 加, sample 默认 None (验 Optional 序列化)
            updated_at: "2026-05-21T20:48:00+08:00".to_string(),
            next_recompute_at: "2026-05-28T00:00:00+08:00".to_string(),
        }
    }

    fn sample_personality() -> Personality {
        Personality {
            verbosity: Verbosity::Concise,
            structure: Structure::ListHeavy,
            formality: Formality::Formal,
            signature_words: vec!["资质".to_string(), "风控".to_string(), "合规".to_string()],
            sample_sentences: vec!["按上次班子会决议, 本周完成 ISO 审核 day4".to_string()],
            source_count: 12,
        }
    }

    #[test]
    fn profile_roundtrip_json() {
        let p = sample_profile();
        let json = serde_json::to_string(&p).expect("serialize");
        let back: Profile = serde_json::from_str(&json).expect("parse");
        assert!(matches!(back.tier, Tier::Mid));
        assert!(matches!(back.central_state, CentralStateSignal::Strong));
        assert_eq!(back.key_people.len(), 1);
        assert_eq!(back.key_people[0].name, "李局");
        assert!(back.personality.is_none(), "sample 默认 personality=None");
    }

    #[test]
    fn tier_serializes_lowercase() {
        let p = sample_profile();
        let json = serde_json::to_value(&p).expect("serialize");
        assert_eq!(json["tier"], "mid");
        assert_eq!(json["centralState"], "strong");
        // personality=None → skip_serializing_if 不出现在 JSON
        assert!(json.get("personality").is_none(), "None 应被 skip");
    }

    #[test]
    fn personality_roundtrip_json() {
        // 5/21 cold start: personality 字段 roundtrip
        let mut p = sample_profile();
        p.personality = Some(sample_personality());
        let json = serde_json::to_string(&p).expect("serialize");
        let back: Profile = serde_json::from_str(&json).expect("parse");
        let pers = back.personality.expect("personality 应反序列化");
        assert!(matches!(pers.verbosity, Verbosity::Concise));
        assert!(matches!(pers.structure, Structure::ListHeavy));
        assert!(matches!(pers.formality, Formality::Formal));
        assert_eq!(pers.signature_words.len(), 3);
        assert_eq!(pers.source_count, 12);
    }

    #[test]
    fn personality_serializes_camel_case() {
        let mut p = sample_profile();
        p.personality = Some(sample_personality());
        let json = serde_json::to_value(&p).expect("serialize");
        let pers = &json["personality"];
        assert_eq!(pers["verbosity"], "concise");
        assert_eq!(pers["structure"], "list_heavy");
        assert_eq!(pers["formality"], "formal");
        assert_eq!(pers["signatureWords"][0], "资质");
        assert_eq!(pers["sourceCount"], 12);
        assert_eq!(pers["sampleSentences"].as_array().unwrap().len(), 1);
    }
}
