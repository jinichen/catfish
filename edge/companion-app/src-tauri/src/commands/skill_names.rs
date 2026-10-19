//! 技能给人看的中文名 / 一句话说明 (10/19, 鸿波「技能清单对普通办公人员只能靠猜」)。
//!
//! 跟 tool-bridge `skill_names.py` 同一套规则 —— 改一边要改另一边 (两边的测试用同样的例子):
//!   名字: frontmatter `title` → 一级标题破折号后的中文 (去括号) → description 第一句 → 目录名
//!   说明: description 里写给模型的「员工说"X"/"Y"等关键词时, 立即调用…」→「说「X」「Y」时用」;
//!         否则去掉 MUST CALL / tool_call 指令后的 description, 最多 80 字

use std::sync::OnceLock;

use regex::Regex;

const MAX_TITLE: usize = 16;

fn re(cell: &'static OnceLock<Regex>, pat: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(pat).expect("regex"))
}

static DASH: OnceLock<Regex> = OnceLock::new();
static PAREN: OnceLock<Regex> = OnceLock::new();
static CLAUSE: OnceLock<Regex> = OnceLock::new();
static DIRECTIVE: OnceLock<Regex> = OnceLock::new();
static TRIGGER: OnceLock<Regex> = OnceLock::new();
static QUOTED: OnceLock<Regex> = OnceLock::new();
static SPACES: OnceLock<Regex> = OnceLock::new();
static SEP: OnceLock<Regex> = OnceLock::new();

fn has_cjk(s: &str) -> bool {
    s.chars().any(|c| ('\u{4e00}'..='\u{9fff}').contains(&c))
}

fn clip(s: &str, max: usize) -> String {
    let s = re(&SPACES, r"\s+").replace_all(s, " ");
    let s = s.trim_matches(|c: char| " ,，、:：-—".contains(c));
    if s.chars().count() <= max {
        s.to_string()
    } else {
        s.chars().take(max - 1).collect::<String>() + "…"
    }
}

/// (frontmatter 里的 title / description, 正文)
pub fn title_from(title: Option<&str>, description: Option<&str>, body: &str, fallback: &str) -> String {
    if let Some(t) = title.map(str::trim).filter(|t| !t.is_empty()) {
        return clip(t, MAX_TITLE);
    }
    if let Some(line) = body.lines().find(|l| l.starts_with("# ")) {
        let head = line[2..].trim();
        let parts: Vec<&str> = re(&DASH, r"\s+[—–-]{1,2}\s+").splitn(head, 2).collect();
        let cand = if parts.len() == 2 { parts[1] } else { head };
        let cand = re(&PAREN, r"[（(][^）)]*[）)]").replace_all(cand, "");
        let mut cand = cand.trim().to_string();
        // 「花叔Design · Huashu Design …」这种中英并列, 留前面带中文的那段
        if let Some(first) = re(&SEP, r"\s*[·|｜]\s*").split(&cand).next().map(|s| s.trim().to_string()) {
            if !first.is_empty() && has_cjk(&first) {
                cand = first;
            }
        }
        let cand = cand.as_str();
        if !cand.is_empty() && has_cjk(cand) {
            return clip(&cand.replace(" skill", ""), MAX_TITLE);
        }
    }
    let desc = description.unwrap_or("");
    let directive = re(&DIRECTIVE, r"(?i)⚠️?\s*MUST CALL\s*[:：]?|\*\*|立即调用[^。；;]*|\(?catfish_[a-z_]+[^)）]*[)）]?");
    let desc = directive.replace_all(desc, "");
    let desc = desc.trim();
    if !desc.is_empty() && !trigger_re().is_match(desc) {
        let no_paren = re(&PAREN, r"[（(][^）)]*[）)]").replace_all(desc, "");
        let first = re(&CLAUSE, r"[，,。！？!?；;：:\n]|\s+[—–]\s+").split(&no_paren).next().unwrap_or("").trim().to_string();
        if !first.is_empty() && has_cjk(&first) {
            return clip(&first, MAX_TITLE);
        }
    }
    fallback.to_string()
}

fn trigger_re() -> &'static Regex {
    re(&TRIGGER, r#"员工说((?:\s*["“「][^"”」]{1,20}["”」]\s*[/、，,]?)+)\s*等?(?:关键词)?时"#)
}

pub fn summary_from(description: Option<&str>, body: &str) -> String {
    let raw = description.unwrap_or("").trim();
    if let Some(c) = trigger_re().captures(raw) {
        let phrases: Vec<String> = re(&QUOTED, r#"["“「]([^"”」]{1,20})["”」]"#)
            .captures_iter(&c[1])
            .take(3)
            .map(|m| format!("「{}」", &m[1]))
            .collect();
        return format!("说{}时用", phrases.join(""));
    }
    let directive = re(&DIRECTIVE, r"(?i)⚠️?\s*MUST CALL\s*[:：]?|\*\*|立即调用[^。；;]*|\(?catfish_[a-z_]+[^)）]*[)）]?");
    let mut desc = directive.replace_all(raw, "").trim_matches(|c: char| " ,，:：".contains(c)).to_string();
    if desc.is_empty() {
        desc = body
            .split("\n\n")
            .map(str::trim)
            .find(|p| !p.is_empty() && !p.starts_with('#'))
            .unwrap_or("")
            .to_string();
    }
    let desc = re(&SPACES, r"\s+").replace_all(&desc, " ").to_string();
    if desc.chars().count() <= 80 {
        desc
    } else {
        desc.chars().take(79).collect::<String>() + "…"
    }
}

/// 界面: 改名 (`set`, 只许改员工自己 ~/.catfish/skills 下的) / 一次性让小鲶给没名字的起名 (`backfill`)。
#[tauri::command]
pub async fn skill_name_call(action: String, params: Option<serde_json::Value>) -> Result<serde_json::Value, String> {
    if !["set", "backfill"].contains(&action.as_str()) {
        return Err(format!("不支持的动作: {action}"));
    }
    crate::services::tool_bridge_rpc::call_with_timeout(
        &format!("pim/skillname_{action}"),
        params.unwrap_or_else(|| serde_json::json!({})),
        std::time::Duration::from_secs(150),
    )
    .await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn 名字按顺序取() {
        assert_eq!(title_from(None, Some("x"), "# eis-login — 登录 EIS 一站式信息门户（支持验证码）\n", "eis-login"),
                   "登录 EIS 一站式信息门户");
        assert_eq!(title_from(Some("每周资质邮件摘要"), None, "# d — 别的\n", "d"), "每周资质邮件摘要");
        assert_eq!(title_from(None, Some("员工丢来视频要摘要时用，本地转写。"), "# sum\n", "sum"), "员工丢来视频要摘要时用");
        assert_eq!(title_from(None, Some("Conduct research"), "# x\n", "in-depth-research"), "in-depth-research");
        assert_eq!(title_from(None, None, "# 花叔Design · Huashu Design 原型\n", "huashu"), "花叔Design");
        assert_eq!(title_from(None, Some("花叔 Design — HTML 原生设计 skill. 触发词: x"), "# h\n", "h"), "花叔 Design");
        let long = title_from(Some("一二三四五六七八九十一二三四五六七八"), None, "", "x");
        assert!(long.ends_with('…') && long.chars().count() == 16);
    }

    #[test]
    fn 写给模型的指令改成人话() {
        let d = "⚠️ MUST CALL: 员工说\"上班打卡\"/\"上班签到\"/\"打上班卡\"等关键词时, **立即调用此 skill 的 tool_call** (catfish_run_skill)";
        assert_eq!(title_from(None, Some(d), "# eis-checkin — 登录 EIS 后点上班打卡按钮\n", "eis-checkin"), "登录 EIS 后点上班打卡按钮");
        assert_eq!(summary_from(Some(d), ""), "说「上班打卡」「上班签到」「打上班卡」时用");
        assert_eq!(summary_from(Some("每周一扫描邮件，出摘要。"), ""), "每周一扫描邮件，出摘要。");
        assert_eq!(summary_from(None, "# t\n\n第一段说明\n\n第二段"), "第一段说明");
    }
}
