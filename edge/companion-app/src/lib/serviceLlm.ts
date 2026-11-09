/** 后台小调用 (不进对话、不写会话) 共用的一次模型调用: 托管提议归组 (routines)、群里 @我 的话是不是交代事 (roomAsks)。
 * 跟早安播报 / 顾问同一条路 (Companion → 网关 /v1/chat/completions, catfish_internal=1)。
 * 失败 / 超时 / 上游把错误当正文返回, 一律返 null —— 调用方当「这次没结果」, 不报错不重试。 */
import { config } from "./env";
import { fetchWithAuth } from "./me";
import { SERVICE_LLM_HEADERS, SERVICE_LLM_QUERY } from "./briefing_advisor_common";
import { warnIfUpstreamError } from "./upstreamErrorGuard";

export async function serviceLlm(
  where: string,
  system: string,
  user: string,
  model: string,
  opts: { maxTokens?: number; timeoutMs?: number } = {},
): Promise<string | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), opts.timeoutMs ?? 60_000);
  try {
    const resp = await fetchWithAuth(`${config.backendUrl}/v1/chat/completions${SERVICE_LLM_QUERY}`, {
      method: "POST",
      headers: SERVICE_LLM_HEADERS,
      body: JSON.stringify({
        model,
        messages: [
          { role: "system", content: system },
          { role: "user", content: user },
        ],
        max_tokens: opts.maxTokens ?? 800,
        temperature: 0.2,
        stream: false,
      }),
      signal: controller.signal,
    });
    if (!resp.ok) {
      console.warn(`[${where}] LLM 调用失败 ${resp.status}`);
      return null;
    }
    const data = await resp.json();
    const content = data?.choices?.[0]?.message?.content;
    if (typeof content !== "string" || warnIfUpstreamError(where, content)) return null;
    return content;
  } catch (e) {
    console.warn(`[${where}] LLM 调用异常`, e);
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/** 模型回的 JSON (可能包在代码块里、前后有字) → 对象; 解不出返 null */
export function parseJsonObject(text: string): Record<string, unknown> | null {
  const s = text.trim().replace(/^```(?:json)?\s*/i, "").replace(/```\s*$/, "");
  const a = s.indexOf("{");
  const b = s.lastIndexOf("}");
  if (a < 0 || b <= a) return null;
  try {
    const v = JSON.parse(s.slice(a, b + 1));
    return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}
