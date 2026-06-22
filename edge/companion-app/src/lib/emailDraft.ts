/** P3.5.57 Phase 2 (6/22 鸿波 catch "应该要能支持用户选择是生成拟稿还是不要"):
 *  一次性 (non-streaming) 调 catfish-gateway /v1/chat/completions 让 LLM
 *  根据原邮件起一段回复草稿, 直接填到 DetailPane Compose body 输入框.
 *
 *  跟 EmailTab.tsx:222 handleAskCatfish 区别:
 *   - handleAskCatfish 跳工作台 chat tab, 让 user 跟 LLM 多轮聊
 *   - draftEmailReply 一次性返一段回复草稿, 落 Compose body, **不跳 chat**
 *
 *  跟 briefing.ts fetchBriefingSuggestion 同 pattern (non-streaming + fetchWithAuth +
 *  Personality + picker model + timeout). 复用 _personalityHint 模式.
 *
 *  失败处理: 返 null + error string, 调用方 toast 错误. 不挂 Compose UI.
 */

import { type Personality } from "./agent";
import { config } from "./env";
import { fetchWithAuth } from "./me";

const SERVICE_LLM_HEADERS = {
  "Content-Type": "application/json",
  // P3.5.27 邮件类 LLM 调用走内网 model 默认 (charter 数据零出端), 但 picker
  // model 仍优先 (员工知道自己选啥). 没 picker 时 fallback catfish-private-main.
};

// catfish_skip_identity=1 让 gateway 不注 SOUL identity (邮件拟稿用不上小鲶人格,
// 节 token, 跟 briefing 同款).
const SERVICE_LLM_QUERY = "?catfish_source=companion-email-draft&catfish_skip_identity=1";

const DRAFT_TIMEOUT_MS = 30_000;

function _personalityHint(p: Personality | undefined): string {
  switch (p) {
    case "direct":
      return "语气: 直爽老李型 — 说话短, 不绕弯, 不堆套话.";
    case "roast":
      return "语气: 毒舌小赵型 — 敢吐槽, 但邮件场景请正经一些不带攻击.";
    case "gentle":
    default:
      return "语气: 温柔同事型 — 贴心, 主动确认细节, 不卑不亢.";
  }
}

function _buildSystemPrompt(agentName: string, personality: Personality | undefined): string {
  return `你是${agentName}, 员工的鲶鱼数字副手. 现在帮员工起一段邮件回复草稿.

${_personalityHint(personality)}

规则:
- 直接返回邮件正文 (Body only). 不要带 "Subject:" / "Re:" / 收件人 / 签名 / 客套话开头.
- 不要"亲爱的 X" / "您好" / "祝您工作顺利" 这种套话, 除非原邮件特别正式.
- 用员工第一人称 ("我", 不要"我们"代员工说话).
- 不知道细节就用 [TODO: 这里待员工补充 X] 占位, 不要瞎编事实/数字/日期.
- 中文邮件用中文回, 英文邮件用英文回.
- 长度看原邮件复杂度: 简单确认 1-3 行, 实质回复 5-10 行, 不要超过 15 行.`;
}

function _buildUserPrompt(opts: {
  sender: string;
  subject: string;
  date: string;
  bodyText: string;
}): string {
  // 复用 EmailTab.handleAskCatfish 模板, 但末句改"直接给草稿"
  const { sender, subject, date, bodyText } = opts;
  const snippet = bodyText.slice(0, 1500);
  const truncated = bodyText.length > 1500 ? "…(原邮件过长, 已截 1500 字)" : "";
  return (
    `这封邮件:\n` +
    `- 发件人: ${sender}\n` +
    `- 主题: ${subject}\n` +
    `- 时间: ${date}\n\n` +
    `正文:\n${snippet}${truncated}\n\n` +
    `请直接给回复草稿正文 (不要带 Subject/收件人/签名).`
  );
}

export interface DraftEmailReplyInput {
  sender: string;
  subject: string;
  date: string;
  bodyText: string;
  agentName: string;
  personality?: Personality;
  model: string;
}

export interface DraftEmailReplyResult {
  ok: boolean;
  /** 拟稿正文, ok=true 时非空 */
  body?: string;
  /** 错误描述, ok=false 时非空 */
  error?: string;
}

/** 调 catfish-gateway 一次性 LLM call 起邮件回复草稿. 30s timeout.
 *
 *  返 {ok: true, body: "..."} 或 {ok: false, error: "..."}. 调用方 toast 显错.
 */
export async function draftEmailReply(input: DraftEmailReplyInput): Promise<DraftEmailReplyResult> {
  if (!input.bodyText.trim()) {
    return { ok: false, error: "原邮件正文为空, 没法拟稿" };
  }

  const systemPrompt = _buildSystemPrompt(input.agentName, input.personality);
  const userPrompt = _buildUserPrompt({
    sender: input.sender,
    subject: input.subject,
    date: input.date,
    bodyText: input.bodyText,
  });

  const url = `${config.backendUrl}/v1/chat/completions${SERVICE_LLM_QUERY}`;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), DRAFT_TIMEOUT_MS);

  try {
    const resp = await fetchWithAuth(url, {
      method: "POST",
      headers: SERVICE_LLM_HEADERS,
      body: JSON.stringify({
        model: input.model,
        messages: [
          { role: "system", content: systemPrompt },
          { role: "user", content: userPrompt },
        ],
        max_tokens: 600,
        temperature: 0.6,
        stream: false,
      }),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      return {
        ok: false,
        error: `LLM 调用失败 (${resp.status}): ${text.slice(0, 200) || resp.statusText}`,
      };
    }
    const data = await resp.json();
    const content = data?.choices?.[0]?.message?.content;
    if (typeof content !== "string" || !content.trim()) {
      return { ok: false, error: "LLM 返回为空" };
    }

    // 清理 LLM 偶尔返的 markdown code fence / 前缀引号
    const cleaned = content
      .trim()
      .replace(/^```[a-z]*\n?/i, "")
      .replace(/\n?```$/i, "")
      .replace(/^(Subject|主题)[:：][^\n]*\n+/i, "")
      .replace(/^["「『]/, "")
      .replace(/["」』]$/, "")
      .trim();

    if (!cleaned) {
      return { ok: false, error: "LLM 返回内容清理后为空" };
    }
    return { ok: true, body: cleaned };
  } catch (e) {
    clearTimeout(timeoutId);
    const msg = e instanceof Error ? e.message : String(e);
    if (msg.includes("abort")) {
      return { ok: false, error: `LLM 调用超时 (>${DRAFT_TIMEOUT_MS / 1000}s)` };
    }
    return { ok: false, error: `LLM 调用异常: ${msg}` };
  }
}
