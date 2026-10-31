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

use super::email_classify_parse::{parse_classifications, Classification};
use super::email_notify::truncate;
use super::email_types::EmailItem;

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

pub(crate) async fn call_rate_llm(items: &[EmailItem]) -> Result<Vec<Classification>, String> {
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

    // 8/21 分诊升级: 带正文摘要 (160 字)。之前只有 主题60+发件人40 —— 连正文
    // 都不看, "2026年9月10日前报汇总表" 这种截止日永远提不出来, 分诊只能分
    // "重要程度"分不出"要我干什么"。token 账: 30 封/批 × ~160 字 ≈ 2-3K tokens,
    // 换来 action+deadline 两个新产出, 且 LLM 调用次数不变 (一次调用三个产出)。
    let list = items
        .iter()
        .enumerate()
        .map(|(i, it)| {
            // 10/31: 收件方式 —— 不知道 (老数据) 就不写这一栏
            let hint = it.delivery_hint();
            format!(
                "{}. 主题: {} | 发件人: {}{} | 摘要: {}",
                i + 1,
                truncate(&it.subject, 60),
                truncate(&it.sender, 40),
                if hint.is_empty() { String::new() } else { format!(" | 收件: {hint}") },
                truncate(&it.snippet, 160),
            )
        })
        .collect::<Vec<_>>()
        .join("\n");

    // ⚠ 协议跟 email_classify_parse.rs 一体两面, 改一处必改另一处 (那边有测试钉着)。
    // 10/31: 加「收件」一栏 + 群发 / 抄送的判法。原来 460 封里标了 179 封办、68 封回 ——
    // 群发给近百人的通报、各单位自查的预警, 跟点名找我办的事模型分不出来。
    let system = "你是邮件分诊助手. 对每封邮件给三个判断: \
                  u = 紧急度: 急 (老板/客户/紧急任务/临近截止) / 低 (newsletter/促销/自动通知) / 其它一律 中. \
                  a = 这封要收件人干什么: 知 (知悉即可, 不用动作) / 回 (要回信) / 办 (要办事, 如提交材料/审批/参加). \
                  「收件」一栏说明这封是直接发给我、抄送我还是群发 (共几人); 收件人几十上百的就是群发, 哪怕我在收件人一栏. \
                  抄送我的、或群发给很多人的通知 / 通报 / \
                  预警 / 公文 / 制度, 默认 知; 只有正文明确要求每个收件人本人去做 (每人提交、填报、参加、完成) 才是 办; \
                  「请各单位 / 各部门落实」这类是给负责人的, 我只是群发里的一个时归 知. 回 只给直接发给我并且在等我答复的. \
                  系统自动发的通知、订阅、营销一律 知, u 给 低. \
                  d = 截止日期, 格式严格 YYYY-MM-DD, 只在 a 为 回/办 且邮件里有明确日期时给, 没有就省略这个键. 不要编造日期. \
                  返回 JSON 数组, 每封一个对象 {\"u\":..,\"a\":..,\"d\":..}. 不要其它任何解释.";
    let user = format!(
        "{list}\n\n按上面顺序, 返回 {} 个对象的 JSON 数组, 例如 [{{\"u\":\"急\",\"a\":\"办\",\"d\":\"2026-09-10\"}},{{\"u\":\"低\",\"a\":\"知\"}}]. 只返 JSON, 不要任何解释.",
        items.len()
    );

    let req = ChatRequest {
        model: model.clone(),
        messages: vec![
            ChatMessage { role: "system", content: system.to_string() },
            ChatMessage { role: "user", content: user },
        ],
        temperature: 0.0,
        // 8/21: 64 → 512。对象数组比单字数组长 (30 封 × ~25 tokens/对象)。
        // 64 会把数组截半, 解析 Err → 冷却 → badge 全灭 —— 跟 8/19 验证码
        // max_tokens:30 是同一族坑 ("答案短"不等于"生成短")。
        max_tokens: 512,
        stream: false,
    };

    // 8/14: 走 trust_central —— 这条打的是本机 hermes 8642, 而 reqwest 默认会读
    // 系统代理。员工开着 Clash / 公司 VPN 时请求被塞进代理隧道, 报出来是
    // "error sending request"(详见 util/http_client.rs 上那段长注释)。
    // 今晚 embedding 就是这么挂的 —— 这处是同一个病, 只是还没爆。
    let client = crate::util::http_client::trust_central(
        reqwest::Client::builder()
            // 10/31: 15 → 90 s。经 hermes 一批 30 封, 开着思考实测 16–103 s, 15 s 全超时 ——
            // 1.2.12 重判时「办 / 回」一条没写进去。插件已对这类请求关思考 (3 s 级),
            // 这里留足余量给上游拥堵, 不再让超时决定分诊有没有结果。
            .timeout(Duration::from_secs(90)),
    )
    .build()
    .map_err(|e| format!("reqwest build 失败: {e}"))?;

    let resp = service_llm_post(&client, &base_url, "companion-email-scheduler")
        .header("Authorization", &auth_header)
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

    // 8/21: 解析搬到 email_classify_parse.rs (纯函数, 独立可测)。
    // 老 parse_urgencies 删掉 —— 它的全部场景 (fence/前缀文字/纯字符串数组/
    // 无数组报错) 都在新解析器的测试里, 含老格式兼容。
    parse_classifications(&content)
}


/// 后台分类调用 (邮件分诊 / 钓鱼复审) 的 POST: 地址带来源标记, 头带员工。
///
/// 10/30 鸿波看门户配额日志: 这两类调用显示成「client:hermes-cli · 对话」——
/// 员工和来源都丢了, 配额记不到人, 也看不出是邮件在跑。原来只在请求头上标
/// `X-Catfish-Source` / `X-Catfish-Skip-Identity`, 也不带 `X-Catfish-User`。
/// 早安参谋走同一条 hermes 8642 却标得对, 它的标记写在 URL 上
/// (briefing_advisor_common.ts `SERVICE_LLM_QUERY`), 员工走 `X-Catfish-User` 头。
/// 这里照它的写法来; 原来的两个头也留着, 网关直连时照样认。
pub(crate) fn service_llm_post(
    client: &reqwest::Client,
    base_url: &str,
    source: &str,
) -> reqwest::RequestBuilder {
    let mut rb = client
        .post(service_llm_url(base_url, source))
        .header("X-Catfish-Source", source)
        .header("X-Catfish-Skip-Identity", "true"); // 不需要 SOUL inject, 服务式调用
    if let Some(user) = crate::services::oauth::current_user_sub() {
        rb = rb.header("X-Catfish-User", user);
    }
    rb
}

fn service_llm_url(base_url: &str, source: &str) -> String {
    format!(
        "{}/v1/chat/completions?catfish_source={source}&catfish_skip_identity=1&catfish_internal=1",
        base_url.trim_end_matches('/'),
    )
}

#[cfg(test)]
mod service_llm_url_tests {
    use super::service_llm_url;

    #[test]
    fn source_and_flags_ride_on_the_url() {
        assert_eq!(
            service_llm_url("http://localhost:8642/", "companion-phishing-scan"),
            "http://localhost:8642/v1/chat/completions?catfish_source=companion-phishing-scan\
             &catfish_skip_identity=1&catfish_internal=1",
        );
    }
}
