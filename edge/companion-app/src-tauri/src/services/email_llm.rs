//! 邮件紧急度评级的 LLM 调用。
//!
//! 2026-08-15 从 email_scheduler.rs 切出来。纯搬迁, 逻辑一行未改。
//!
//! ⚠ 863 行那个 client 是 8/14 才改成走 trust_central 的 —— 它打的是本机
//! hermes 8642, 而 reqwest 默认会读系统代理。原样搬过来了, 别退回裸的
//! Client::builder()。util/http_client.rs 里有条守卫测试盯着这件事。
//!
//! 这里 import 了 email_notify 的 truncate —— 方向是别扭的 (llm 不该依赖
//! notify)。真正的原因是 truncate 是个通用字符串函数却没有通用的家:
//! services/ 下现在有**两份**实现 (这里和 phishing_llm.rs), 行为一样写法不同。
//! 该做的是合成一份放进 util, 那是改逻辑, 不在这次纯搬迁里做。

use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::services::{hermes_api_config, picker_config, upstream_error_guard};

use super::email_notify::truncate;
use super::email_types::{EmailItem, Urgency};

#[derive(Serialize)]
struct ChatMessage {
    role: &'static str,
    content: String,
}

#[derive(Serialize)]
struct ChatRequest {
    model: String,
    messages: Vec<ChatMessage>,
    temperature: f32,
    max_tokens: u32,
    stream: bool,
}

#[derive(Deserialize)]
struct ChatChoice {
    message: ChatChoiceMsg,
}

#[derive(Deserialize)]
struct ChatChoiceMsg {
    content: Option<String>,
}

#[derive(Deserialize)]
struct ChatResponse {
    choices: Vec<ChatChoice>,
}

pub(crate) async fn call_rate_llm(items: &[EmailItem]) -> Result<Vec<Urgency>, String> {
    // P3.5.140 (6/29 鸿波"数据流应该是 companion → hermes → gateway(8999), 不是双路径,
    // 更不是 gateway(8999) 作为 hermes 的 fallback"):
    // 单路径: Companion → hermes 8642 → gateway 8999 → LLM. 没有 fallback.
    //   - body.model 真 chain 真值真 → hermes plugin P11 截 → P6 wrap _create_agent
    //     → agent.model = chain 真值 → auxiliary_client 自动 sync (跟 TS 前端**统一**)
    //   - 数据零出端 X-Catfish-User header 跨员工保护**统一**走 plugin
    //   - 客户**单点配置**真 hermes (P11/P21 picker) 自动覆盖 background task
    //   - hermes_api 没配 / 没起 → Err (不再静默 fallback gateway 老路径)
    let hermes_cfg = hermes_api_config::hermes_api_config();
    let hermes_key = hermes_cfg.key.as_deref().ok_or_else(|| {
        "hermes_api.key 没配 (~/.catfish/companion.yaml hermes_api.key 或 \
         env CATFISH_HERMES_API_KEY 必填)".to_string()
    })?;
    let base_url = hermes_cfg.url.clone();
    let auth_header = format!("Bearer {hermes_key}");
    // P3.5.29 Phase 4 (6/17 鸿波"啥意思不干活") + P3.5.139 (6/29 鸿波"都要去除硬编码"):
    // chain picker > role > yaml > Err (硬 chain).
    //   1. picker_config::current_model() — 员工 chat picker (P3.5.28)
    // 8/9: 原来 2/3 顺位是 role_config("rate_fast") 和 email_config().rate_model,
    //      已砍。理由见下面那行注释 + 本文件顶部。
    // 8/9 鸿波: 同上 —— picker 是唯一真源, 砍掉 rate_fast / rate_model 两级兜底。
    let model = picker_config::current_model().ok_or_else(|| {
        "picker 未选模型 (~/.catfish/picker_model 空/不在) —— 邮件评级不猜模型, \
         开一次 Companion 对话 tab 让 picker 落盘即可"
            .to_string()
    })?;

    let list = items
        .iter()
        .enumerate()
        .map(|(i, it)| {
            format!(
                "{}. 主题: {} | 发件人: {}",
                i + 1,
                truncate(&it.subject, 60),
                truncate(&it.sender, 40),
            )
        })
        .collect::<Vec<_>>()
        .join("\n");

    let system = "你是邮件分类助手. 按重要程度评级邮件: 急 / 中 / 低. \
                  急 = 老板 / 客户 / 直接老板 / 含 deadline 关键词 / 紧急任务; \
                  低 = newsletter / 促销 / 自动通知 / GitHub PR review 之类的常规事项; \
                  其它一律 中. \
                  返回 JSON 数组, 每个元素只一个字: '急' / '中' / '低'. 不要其它任何解释.";
    let user = format!(
        "{list}\n\n按上面顺序, 返回 {} 个评级的 JSON 数组, 例如 [\"急\",\"中\",\"低\"]. 只返 JSON, 不要任何解释.",
        items.len()
    );

    let req = ChatRequest {
        model: model.clone(),
        messages: vec![
            ChatMessage { role: "system", content: system.to_string() },
            ChatMessage { role: "user", content: user },
        ],
        temperature: 0.0,
        max_tokens: 64,
        stream: false,
    };

    // 8/14: 走 trust_central —— 这条打的是本机 hermes 8642, 而 reqwest 默认会读
    // 系统代理。员工开着 Clash / 公司 VPN 时请求被塞进代理隧道, 报出来是
    // "error sending request"(详见 util/http_client.rs 上那段长注释)。
    // 今晚 embedding 就是这么挂的 —— 这处是同一个病, 只是还没爆。
    let client = crate::util::http_client::trust_central(
        reqwest::Client::builder()
            .timeout(Duration::from_secs(15)),
    )
    .build()
    .map_err(|e| format!("reqwest build 失败: {e}"))?;

    let resp = client
        .post(format!("{base_url}/v1/chat/completions"))
        .header("Authorization", &auth_header)
        .header("X-Catfish-Source", "companion-email-scheduler")
        .header("X-Catfish-Skip-Identity", "true")  // 不需要 SOUL inject, 服务式调用
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

    // 8/8: 上游把错误当正文返 (HTTP 200 + "API call failed after 3 retries: ...")。
    // 不认它的话会一路走到 parse_urgencies 报"没 JSON array", 而那条是
    // log::debug! —— 生产 INFO 级别下永远看不见, 于是每 30 秒静默重烧一次,
    // 每次经 hermes 还要 ×3。详见 upstream_error_guard.rs。
    if upstream_error_guard::is_upstream_error_as_content(&content) {
        let cd = upstream_error_guard::mark_upstream_error("email-rate", &content);
        return Err(format!("上游返回的是一条错误, 已冷却 {}s", cd.as_secs()));
    }

    parse_urgencies(&content)
}

/// 从 LLM 输出文本解析 JSON 数组. 容错 — markdown code fence / 前后多余文字都试着扒出来.
fn parse_urgencies(s: &str) -> Result<Vec<Urgency>, String> {
    let trimmed = s.trim();
    // 找第一个 [ 到最后一个 ]
    let start = trimmed.find('[').ok_or_else(|| format!("没 JSON array: {trimmed:?}"))?;
    let end = trimmed.rfind(']').ok_or_else(|| format!("没 ] 闭合: {trimmed:?}"))?;
    if end <= start {
        return Err(format!("] 在 [ 前: {trimmed:?}"));
    }
    let array_str = &trimmed[start..=end];
    let labels: Vec<String> = serde_json::from_str(array_str)
        .map_err(|e| format!("JSON 解析失败 ({e}): {array_str:?}"))?;
    Ok(labels.iter().map(|l| Urgency::from_label(l)).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_urgencies_clean_json() {
        let r = parse_urgencies(r#"["急","中","低"]"#).unwrap();
        assert_eq!(r, vec![Urgency::Urgent, Urgency::Medium, Urgency::Low]);
    }

    #[test]
    fn parse_urgencies_with_markdown_fence() {
        let r = parse_urgencies("```json\n[\"急\",\"低\"]\n```").unwrap();
        assert_eq!(r, vec![Urgency::Urgent, Urgency::Low]);
    }

    #[test]
    fn parse_urgencies_with_leading_text() {
        let r = parse_urgencies("根据评级:\n[\"急\",\"中\"]\n谢谢").unwrap();
        assert_eq!(r, vec![Urgency::Urgent, Urgency::Medium]);
    }

    #[test]
    fn parse_urgencies_no_array_errors() {
        assert!(parse_urgencies("急 中 低").is_err());
        assert!(parse_urgencies("").is_err());
    }
}
