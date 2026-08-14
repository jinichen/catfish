//! 钓鱼识别的 LLM 复审层 —— batch 调网关, 一次评 N 封。
//!
//! 2026-08-15 从 phishing_scan.rs 切出来。纯搬迁, 逻辑一行未改。
//!
//! 为什么单独一个文件: `batch_llm_review` 在原文件里**没有任何调用者** ——
//! 调它的是 email_scheduler.rs。它跟规则层唯一的关系是共用一个话题, 不共用
//! 任何数据流。它是这里唯一会发网络请求的东西, 失败模式 (超时 / 网关挂 /
//! LLM 返回不是 JSON) 也跟规则层完全不同。
//!
//! 红线: LLM 走 catfish gateway 内网模型, 数据零出端。

use serde::{Deserialize, Serialize};

// ─── LLM 复审 batch ────────────────────────────────────────

#[derive(Debug, Clone, Serialize)]
pub struct LlmReviewInput {
    pub subject: String,
    pub sender: String,
    /// 规则触发结果一句话总结 (e.g. "PHISH-001 (sender_spoofing high), PHISH-021 (suspicious_link high)" 或 "无触发")
    pub rule_summary: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct LlmPhishingVerdict {
    /// "phishing" / "suspicious" / "safe" / "unknown" (LLM 漏返时)
    pub verdict: String,
    pub reason: String,
}

#[derive(Serialize)]
struct ChatMessage { role: &'static str, content: String }

#[derive(Serialize)]
struct ChatRequest {
    model: String,
    messages: Vec<ChatMessage>,
    temperature: f32,
    max_tokens: u32,
    stream: bool,
}

#[derive(Deserialize)]
struct ChatChoice { message: ChatChoiceMsg }
#[derive(Deserialize)]
struct ChatChoiceMsg { content: Option<String> }
#[derive(Deserialize)]
struct ChatResponse { choices: Vec<ChatChoice> }

/// LLM batch 复审 — 跟 email_scheduler::call_rate_llm 同款 reqwest pattern.
/// 一次喂 N 封邮件 (轻量 metadata + 规则结果), 让 LLM 返 N 个 verdict+reason.
/// 鸿波 6/12 拍板"所有邮件都调", 这就是 batch all.
///
/// P3.5.140 (6/29 鸿波"Rust 和 TS 统一走 hermes"): 参数 `base_url` 接 hermes 8642
/// 或 gateway 8999 (caller 灰度选), `token` 接 hermes raw key 或 OAuth token (对应
/// base_url 选). callee 不区分, 一律 `Authorization: Bearer {token}`.
pub async fn batch_llm_review(
    items: &[LlmReviewInput],
    base_url: &str,
    token: &str,
    model: &str,
) -> Result<Vec<LlmPhishingVerdict>, String> {
    if items.is_empty() {
        return Ok(Vec::new());
    }

    let list = items
        .iter()
        .enumerate()
        .map(|(i, it)| {
            format!(
                "{}. 主题: {} | 发件人: {} | 规则: {}",
                i + 1,
                truncate(&it.subject, 60),
                truncate(&it.sender, 40),
                truncate(&it.rule_summary, 80),
            )
        })
        .collect::<Vec<_>>()
        .join("\n");

    // P3.5.197 (7/7 鸿波军规审判): 加 marketing 档, 让营销/推广邮件不再被误报为
    // suspicious 触发橙色警告 banner. 员工反馈"营销当可疑, 警报太多容易麻木".
    // 分档语义清晰化: phishing/suspicious 是威胁, marketing 是打扰但非威胁, safe 是正常.
    let system = "你是邮件钓鱼识别助手. 看主题/发件人/规则触发结果, 评每封邮件: \
                  phishing (确定钓鱼/欺诈) / suspicious (可疑待查, 疑似钓鱼但不确定) / \
                  marketing (营销推广/订阅通知/群发广告, 非威胁但员工可能不想看) / \
                  safe (常规工作邮件). \
                  区分要点: 营销邮件从合法域名/知名品牌发来, 内容是产品推广/newsletter/促销, \
                  不诱导密码/汇款/紧急操作 — 归 marketing 不归 suspicious. \
                  返 JSON 数组, 每元素 {\"verdict\":\"...\",\"reason\":\"一句话理由\"}. \
                  不要 markdown, 不要解释, 只返 JSON 数组.";
    let user = format!(
        "{list}\n\n按上面顺序返回 {} 个 verdict 的 JSON 数组. 只返 JSON.",
        items.len()
    );

    let req = ChatRequest {
        model: model.to_string(),
        messages: vec![
            ChatMessage { role: "system", content: system.to_string() },
            ChatMessage { role: "user", content: user },
        ],
        temperature: 0.0,
        max_tokens: 1024,
        stream: false,
    };

    // 8/14: 走 trust_central —— 这条打的是本机 hermes 8642, 而 reqwest 默认会读
    // 系统代理。员工开着 Clash / 公司 VPN 时请求被塞进代理隧道, 报出来是
    // "error sending request"(详见 util/http_client.rs 上那段长注释)。
    // 今晚 embedding 就是这么挂的 —— 这处是同一个病, 只是还没爆。
    let client = crate::util::http_client::trust_central(
        reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(20)),
    )
    .build()
    .map_err(|e| format!("reqwest build 失败: {e}"))?;

    let resp = client
        .post(format!("{base_url}/v1/chat/completions"))
        .header("Authorization", format!("Bearer {token}"))
        .header("X-Catfish-Source", "companion-phishing-scan")
        .header("X-Catfish-Skip-Identity", "true")
        .json(&req)
        .send()
        .await
        .map_err(|e| format!("LLM 调用失败 ({base_url}): {e}"))?;

    if !resp.status().is_success() {
        return Err(format!("LLM 返 {} (via {})", resp.status(), base_url));
    }

    let body: ChatResponse = resp
        .json()
        .await
        .map_err(|e| format!("response 解析失败: {e}"))?;
    let content = body
        .choices
        .first()
        .and_then(|c| c.message.content.clone())
        .unwrap_or_default();

    parse_verdicts(&content, items.len())
}

fn truncate(s: &str, max: usize) -> String {
    if s.chars().count() <= max {
        s.to_string()
    } else {
        let mut out: String = s.chars().take(max).collect();
        out.push('…');
        out
    }
}

fn parse_verdicts(s: &str, expected_n: usize) -> Result<Vec<LlmPhishingVerdict>, String> {
    let trimmed = s.trim();
    let start = trimmed.find('[').ok_or_else(|| format!("没 JSON array: {trimmed:?}"))?;
    let end = trimmed.rfind(']').ok_or_else(|| format!("没 ]: {trimmed:?}"))?;
    if end <= start {
        return Err("] 在 [ 前".into());
    }
    let arr = &trimmed[start..=end];
    let parsed: Vec<LlmPhishingVerdict> = serde_json::from_str(arr)
        .map_err(|e| format!("verdicts JSON 解析失败 ({e}): {arr:?}"))?;
    if parsed.len() != expected_n {
        return Err(format!("LLM 返 {} 个 verdict, 期望 {}", parsed.len(), expected_n));
    }
    Ok(parsed)
}
