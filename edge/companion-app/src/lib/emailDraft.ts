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
import { warnIfUpstreamError } from "./upstreamErrorGuard";
import { resolveExpertBotRequest } from "./expertBots";

const SERVICE_LLM_HEADERS = {
  "Content-Type": "application/json",
  // P3.5.27 邮件类 LLM 调用走内网 model 默认 (charter 数据零出端), 但 picker
  // model 仍优先 (员工知道自己选啥). 没 picker 时 fallback catfish-private-main.
};

// catfish_skip_identity=1 让 gateway 不注 SOUL identity (邮件拟稿用不上小鲶人格,
// 节 token, 跟 briefing 同款).
const SERVICE_LLM_QUERY = "?catfish_source=companion-email-draft&catfish_skip_identity=1";

/** 单次尝试的超时。总耗时最坏 = 3 × 30s + 退避 14s。 */
const DRAFT_TIMEOUT_MS = 30_000;

/** 当前邮件正文给模型的上限。详情页已经拿到全文，但仍需防止异常超长邮件挤掉上下文。 */
const CURRENT_BODY_LIMIT = 12_000;

/** 429 退避重试 (8/7 鸿波实盘).
 *
 *  现象: 点拟稿报
 *    `LLM 调用失败 (429): {"error":{"message":"Too many concurrent runs (max 10)",…}}`
 *
 *  查 outbound_log.db: **没有泄漏的挂起请求 (0 条 ts_response 为空)**, 而且
 *    13:54:04 status=429  ← 这次
 *    13:54:25 status=200  ← 21 秒后同一条路就通了
 *  说明是上游 provider 的**瞬时**并发闸 (gateway 原样透传), 不是故障。
 *
 *  原来一次 429 就把 provider 的原始 JSON 甩给员工, 既没重试也看不懂。
 */
const RETRY_BACKOFF_MS = [4_000, 10_000];

/** 这些状态码值得重试: 429 限流 + 5xx 网关抖动。4xx 其余是请求本身有问题, 重试没用。 */
function _retryable(status: number): boolean {
  return status === 429 || status === 502 || status === 503 || status === 504;
}

/** 把 provider 返的错误体翻成人话。
 *
 *  上游给的是 `{"error":{"message":"…","type":"rate_limit_error","code":"…"}}`,
 *  直接贴给员工既长又看不懂。 */
function _humanError(status: number, raw: string): string {
  let msg = "";
  try {
    const j = JSON.parse(raw);
    msg = j?.error?.message || j?.message || "";
  } catch {
    msg = raw.slice(0, 160);
  }
  if (status === 429) {
    return `模型正忙 (并发已满), 重试几次仍不通。稍等一会儿再点一次${msg ? ` —— ${msg}` : ""}`;
  }
  if (status >= 500) {
    return `网关或模型端出错 (${status})${msg ? `: ${msg}` : ""}`;
  }
  if (status === 401 || status === 403) {
    return `没有调用权限 (${status}) —— 检查 gateway 的 token 配置`;
  }
  return `LLM 调用失败 (${status})${msg ? `: ${msg}` : ""}`;
}

const _sleep = (ms: number, signal?: AbortSignal) => new Promise<void>((resolve) => {
  if (signal?.aborted) {
    resolve();
    return;
  }
  const timer = setTimeout(done, ms);
  function done() {
    clearTimeout(timer);
    signal?.removeEventListener("abort", done);
    resolve();
  }
  signal?.addEventListener("abort", done, { once: true });
});

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
- **写不了草稿就别写**: 如果连该回什么立场都定不了 (例: 不知道员工在这件事里的
  角色 / 该确认还是该拒绝), 不要把问题写成"草稿"寄给对方 —— 返回以 [QUESTIONS]
  开头的清单, 每行一个要员工回答的问题. 界面会把它显示给员工而不是填进发送框.
  能用 [TODO] 占位写出立场明确的草稿时, 优先写草稿.
- 中文邮件用中文回, 英文邮件用英文回.
- 长度看原邮件复杂度: 简单确认 1-3 行, 实质回复 5-10 行, 不要超过 15 行.
- 给了「你已知的背景」就先读完再动笔: 已经答应过的别再答应一遍, 对方已经给过的
  信息不要当作未知去问, 还欠对方或欠自己的事该提就提.
- 背景来自员工本地知识库, 可能过时。跟原邮件冲突时以原邮件为准, 不确定就用 [TODO] 占位.`;
}

/** 拟稿参考的一条本地知识 —— 来自 catfish wiki (entities / concepts)。 */
export interface DraftContextItem {
  title: string;
  /** wiki 相对路径, 显给员工看「参考了哪几篇」 */
  relPath: string;
  /** 正文 (frontmatter 已剥) */
  body: string;
}

/** 单条参考的截断上限。wiki 页本来就短 (实测 300–1100 字), 主要防异常长页。 */
const CONTEXT_BODY_LIMIT = 1200;

function _renderContext(items: DraftContextItem[]): string {
  if (!items.length) return "";
  const blocks = items.map((c, i) => {
    const body = c.body.slice(0, CONTEXT_BODY_LIMIT);
    const cut = c.body.length > CONTEXT_BODY_LIMIT ? "…(略)" : "";
    return `【${i + 1}】${c.title}\n${body}${cut}`;
  });
  return (
    `你已知的背景 (来自员工本地知识库, 共 ${items.length} 条):\n\n` +
    blocks.join("\n\n---\n\n") +
    `\n\n════════\n\n`
  );
}

function _buildUserPrompt(opts: {
  sender: string;
  subject: string;
  date: string;
  bodyText: string;
  context?: DraftContextItem[];
  threadContext?: string;
  attachmentContext?: string;
}): string {
  // 复用 EmailTab.handleAskCatfish 模板, 但末句改"直接给草稿"
  const { sender, subject, date, bodyText } = opts;
  const snippet = bodyText.slice(0, CURRENT_BODY_LIMIT);
  const truncated = bodyText.length > CURRENT_BODY_LIMIT
    ? `…(原邮件过长, 已截 ${CURRENT_BODY_LIMIT} 字)`
    : "";
  return (
    _renderContext(opts.context || []) +
    (opts.threadContext || "") +
    (opts.attachmentContext || "") +
    `要回的是这封:\n` +
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
  /** 本地知识库里跟这封邮件相关的背景。空/省略 = 不附加 Wiki 背景。 */
  context?: DraftContextItem[];
  /** 同一 RFC 822 线程的近期往来，已经在本地读取并格式化。 */
  threadContext?: string;
  /** 附件的本地解析预览，原始附件不上传。 */
  attachmentContext?: string;
  agentName: string;
  personality?: Personality;
  model: string;
}

export interface DraftEmailReplyResult {
  ok: boolean;
  /** 拟稿正文, ok=true 且 kind="draft" 时非空 */
  body?: string;
  /** 错误描述, ok=false 时非空 */
  error?: string;
  /** 8/21: 返回的是草稿还是**给员工的反问**。
   *
   *  病历: 员工点「重拟」, 模型信息不足 (不知道员工是不是工会小组长), 没按
   *  prompt 用 [TODO] 占位, 而是整段反问员工「你不是个人工会小组长吧? …
   *  先别急着动笔, 你告诉我」。这段被原样 setComposeBody 塞进**发送框** ——
   *  员工点发送就会把它寄给对方 (还抄送 9 个人)。
   *
   *  8/8 已经治过同型病的另一半: 上游把 HTTP 错误当正文返 (upstreamErrorGuard)。
   *  这次是**模型自己**返非草稿文本, 同样不能进正文框。
   *
   *  判据用显式协议, 不做模糊启发: prompt 要求模型在信息不足时以
   *  [QUESTIONS] 开头列问题。带标记 → kind="questions", body 是问题清单
   *  (已剥掉标记), 调用方**不许**填进正文框。
   */
  kind?: "draft" | "questions";
}

/** 模型信息不足时的显式标记 —— 见 DraftEmailReplyResult.kind。 */
export const QUESTIONS_MARK = "[QUESTIONS]";

/** 8/21: 清理后的模型输出 → 草稿还是反问。抽成纯函数是为了可测
 *  (draftEmailReply 本体裹着网络重试, 单测不便)。判据只认显式标记, 不做
 *  模糊启发 —— 「像不像在提问」这种判据必然比真事宽或窄。 */
export function classifyDraftContent(cleaned: string): DraftEmailReplyResult {
  if (cleaned.startsWith(QUESTIONS_MARK)) {
    const questions = cleaned.slice(QUESTIONS_MARK.length).trim();
    if (!questions) {
      return { ok: false, error: "模型返回了空的问题清单" };
    }
    return { ok: true, kind: "questions", body: questions };
  }
  return { ok: true, kind: "draft", body: cleaned };
}

/** 调 catfish-gateway 一次性 LLM call 起邮件回复草稿。
 *
 *  429 / 5xx 会退避重试 (见 RETRY_BACKOFF_MS)。返 {ok, body} 或 {ok:false, error}。
 *  `onRetry` 可选, 让 UI 显「限流, 重试中 (2/3)」而不是干等。
 */
export async function draftEmailReply(
  input: DraftEmailReplyInput,
  onRetry?: (attempt: number, total: number, status: number) => void,
  externalSignal?: AbortSignal,
): Promise<DraftEmailReplyResult> {
  if (!input.bodyText.trim()) {
    return { ok: false, error: "原邮件正文为空, 没法拟稿" };
  }

  const systemPrompt = _buildSystemPrompt(input.agentName, input.personality);
  const userPrompt = _buildUserPrompt({
    sender: input.sender,
    subject: input.subject,
    date: input.date,
    bodyText: input.bodyText,
    context: input.context,
    threadContext: input.threadContext,
    attachmentContext: input.attachmentContext,
  });

  const expertRoute = await resolveExpertBotRequest({
    baseUrl: config.backendUrl,
    useHermes: config.useHermes,
    scenario: "email.draft",
    pickerModel: input.model,
    query: SERVICE_LLM_QUERY,
  });
  const url = expertRoute.url;
  const body = JSON.stringify({
    model: expertRoute.model,
    messages: [
      { role: "system", content: systemPrompt },
      { role: "user", content: userPrompt },
    ],
    max_tokens: 600,
    temperature: 0.6,
    stream: false,
  });

  const total = RETRY_BACKOFF_MS.length + 1;
  let lastErr = "未知错误";

  for (let attempt = 0; attempt < total; attempt++) {
    if (externalSignal?.aborted) {
      return { ok: false, error: "已停止拟稿" };
    }
    const controller = new AbortController();
    const stop = () => controller.abort();
    externalSignal?.addEventListener("abort", stop, { once: true });
    const timeoutId = setTimeout(() => controller.abort(), DRAFT_TIMEOUT_MS);
    try {
      const resp = await fetchWithAuth(url, {
        method: "POST",
        headers: SERVICE_LLM_HEADERS,
        body,
        signal: controller.signal,
      });
      clearTimeout(timeoutId);
      externalSignal?.removeEventListener("abort", stop);

      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        lastErr = _humanError(resp.status, text || resp.statusText);
        // 可重试且还有次数 → 退避后再来
        if (_retryable(resp.status) && attempt < total - 1) {
          onRetry?.(attempt + 2, total, resp.status);
          await _sleep(RETRY_BACKOFF_MS[attempt], externalSignal);
          continue;
        }
        return { ok: false, error: lastErr };
      }

      const data = await resp.json();
      const content = data?.choices?.[0]?.message?.content;
      if (typeof content !== "string" || !content.trim()) {
        return { ok: false, error: "LLM 返回为空" };
      }

      // 8/8: 上游把错误当正文返 (HTTP 200 + 错误文本)。不拦的话员工正文框里
      // 会出现 "API call failed after 3 retries: ..." 当草稿 —— 看起来像成功了,
      // 是最坏的一种失败。详见 upstreamErrorGuard.ts。
      if (warnIfUpstreamError("email-draft", content)) {
        return {
          ok: false,
          error: "模型没能给出内容 (上游返回的是一条错误). 常见原因: 配额耗尽 / 上游服务挂. 稍后再试.",
        };
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
      return classifyDraftContent(cleaned);
    } catch (e) {
      clearTimeout(timeoutId);
      externalSignal?.removeEventListener("abort", stop);
      const msg = e instanceof Error ? e.message : String(e);
      if (externalSignal?.aborted) {
        return { ok: false, error: "已停止拟稿" };
      }
      // 超时不重试 —— 已经等了 30s, 再等两轮员工早走了
      if (msg.includes("abort")) {
        return { ok: false, error: `LLM 调用超时 (>${DRAFT_TIMEOUT_MS / 1000}s)` };
      }
      lastErr = `LLM 调用异常: ${msg}`;
      if (attempt < total - 1) {
        onRetry?.(attempt + 2, total, 0);
        await _sleep(RETRY_BACKOFF_MS[attempt], externalSignal);
        continue;
      }
      return { ok: false, error: lastErr };
    }
  }
  return { ok: false, error: lastErr };
}
