/** 调 catfish-gateway /v1/chat/completions 的 streaming 客户端。
 *
 * 用 fetch + ReadableStream 解 SSE：
 *     data: {"choices":[{"delta":{"content":"..."}}]}\n\n
 *     data: {"choices":[{"delta":{"tool_calls":[...]}}]}\n\n
 *     data: [DONE]\n\n
 *
 * SOUL/memory 注入由 gateway middleware 自动处理（决策 3b），
 * 这里直接发 user messages 即可。
 *
 * tool calling: 客户端传 tools 参数,LLM 决定调哪个 → 我们执行 → 回传继续。
 */

import type { ChatMessage, ToolCall } from "../types/chat";
import { config } from "./env";
import { gatewayGetDevToken } from "./tauri";
import { getOverrideToken } from "./me";
import { useAgentStore } from "../store/agent";

interface SendChatParams {
  model: string;
  messages: ChatMessage[];
  /** OpenAI tool calling 兼容的 tool 定义列表,空表示不带 tools */
  tools?: OpenAITool[];
  /** 每个 token 来一次 */
  onDelta: (text: string) => void;
  /** LLM 决定调工具(stream 中 tool_calls 累积完毕)时触发 */
  onToolCalls?: (calls: ToolCall[]) => void;
  /** 流自然结束(finish_reason=stop / [DONE]) */
  onDone: (info?: ChatStreamDoneInfo) => void;
  /** 任何错误 */
  onError: (msg: string) => void;
  signal?: AbortSignal;
}

export interface OpenAITool {
  type: "function";
  function: {
    name: string;
    description?: string;
    parameters: Record<string, unknown>;
  };
}

export interface ChatUsage {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
}

export interface ChatStreamDoneInfo {
  finish_reason?: string;
  usage?: ChatUsage;
}

/** Token 缓存 —— 第一次调 gateway 时通过 Tauri Rust 读 .env 拿真 token,后续复用。
 *
 * 五一 sprint 5/2 加多账号支持: 切换器选的 token (localStorage) 优先级高于 .env.
 */
let _cachedEnvToken: string | null = null;

async function getToken(): Promise<string> {
  // 1. 切换器优先 (DevUserSwitcher 写 localStorage)
  const override = getOverrideToken();
  if (override) return override;
  // 2. .env 兜底
  if (_cachedEnvToken) return _cachedEnvToken;
  try {
    const token = await gatewayGetDevToken();
    _cachedEnvToken = token;
    return token;
  } catch (e) {
    console.warn("[catfish chat] 读 dev token 失败,用 fallback:", e);
    return "dev-token-local";
  }
}

export function _clearTokenCache(): void {
  _cachedEnvToken = null;
}

// ── OpenAI 兼容线格式 ──

/** OpenAI multimodal content part —— text 或 image_url. Gemini / Qwen3-VL /
 * Qwen-Flash 都接受这个 shape (LiteLLM 透传)。 */
type OpenAIContentPart =
  | { type: "text"; text: string }
  | { type: "image_url"; image_url: { url: string } };

interface OpenAIWireMessage {
  role: string;
  content: string | OpenAIContentPart[] | null;
  tool_calls?: Array<{
    id: string;
    type: "function";
    function: { name: string; arguments: string };
  }>;
  tool_call_id?: string;
}

function toWire(messages: ChatMessage[]): OpenAIWireMessage[] {
  return messages.map((m) => {
    if (m.role === "tool") {
      // tool 角色: content 是工具结果(已是字符串),携带 tool_call_id
      return {
        role: "tool",
        content: m.content,
        tool_call_id: m.tool_call_id,
      };
    }
    if (m.role === "assistant" && m.tool_calls && m.tool_calls.length > 0) {
      return {
        role: "assistant",
        content: m.content || null,
        tool_calls: m.tool_calls.map((tc) => ({
          id: tc.id,
          type: "function" as const,
          function: {
            name: tc.name,
            arguments: JSON.stringify(tc.args ?? {}),
          },
        })),
      };
    }
    // user 带附件 → 图片走 multipart image_url, 文档把提取的 text 拼到 text part
    if (m.role === "user" && m.attachments && m.attachments.length > 0) {
      const fileAttachments = m.attachments.filter((a) => a.kind === "file");
      const imageAttachments = m.attachments.filter((a) => a.kind === "image");

      // 拼文档内容到第一个 text part. 用 ===分隔便于模型识别边界.
      let textContent = m.content || "";
      if (fileAttachments.length > 0) {
        const fileBlocks = fileAttachments
          .map((a) => {
            const truncNote = a.truncated ? " [已截断, 仅显示前 50KB]" : "";
            return `\n\n=== 附件: ${a.name}${truncNote} ===\n${a.text || ""}`;
          })
          .join("");
        textContent = `${textContent}${fileBlocks}`;
      }

      // 没图片附件: 直接返普通 string content (兼容非 vision 模型)
      if (imageAttachments.length === 0) {
        return { role: "user", content: textContent };
      }

      // 有图片附件: 走 multipart array
      const parts: OpenAIContentPart[] = [];
      parts.push({ type: "text", text: textContent });
      for (const att of imageAttachments) {
        parts.push({
          type: "image_url",
          image_url: { url: `data:${att.mimeType};base64,${att.base64}` },
        });
      }
      return { role: "user", content: parts };
    }
    return {
      role: m.role,
      content: m.content,
    };
  });
}

// ── 流式 tool_calls 累积 ──
//
// LLM 流式返回 tool_calls 时, function.arguments 是 JSON 字符串, 分多个 chunk 来。
// 我们用 index 做 key, 累积成完整对象, 流结束时一次性 JSON.parse。
interface ToolCallAcc {
  id: string;
  name: string;
  argumentsJson: string;
}

export async function streamChat(params: SendChatParams): Promise<void> {
  const {
    model,
    messages,
    tools,
    onDelta,
    onToolCalls,
    onDone,
    onError,
    signal,
  } = params;

  const url = `${config.gatewayUrl}/v1/chat/completions`;
  const body: Record<string, unknown> = {
    model,
    messages: toWire(messages),
    stream: true,
  };
  if (tools && tools.length > 0) {
    body.tools = tools;
    // tool_choice 默认 auto,让 LLM 自己决定要不要调
  }

  let token: string;
  try {
    token = await getToken();
  } catch (e) {
    onError(`读 dev token 失败: ${stringify(e)}`);
    return;
  }

  // BL-E11 命名权: 把当前员工自定义的 agent name + personality 带过去, gateway
  // 拼 personalization preamble 在 SOUL 前面 (非默认值才发, 省 header 大小).
  const agentSnap = useAgentStore.getState();
  const agentHeaders: Record<string, string> = {};
  if (agentSnap.loaded) {
    if (agentSnap.name && agentSnap.name !== "小鲶" && agentSnap.name !== "Catfish") {
      agentHeaders["X-Catfish-Agent-Name"] = agentSnap.name;
    }
    if (agentSnap.personality && agentSnap.personality !== "gentle") {
      agentHeaders["X-Catfish-Agent-Personality"] = agentSnap.personality;
    }
  }

  let resp: Response;
  try {
    resp = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        ...agentHeaders,
      },
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    onError(`无法连接 gateway: ${stringify(e)}`);
    return;
  }

  if (!resp.ok) {
    // Quota 超限 (429): gateway 返 detail.message 已是 friendly 中文话术
    // ("你今日 Pro 用满了, 切到 catfish-private-main 继续").
    // 直接展示, 不再加 'HTTP 429:' 前缀污染 UX.
    if (resp.status === 429) {
      let friendlyMsg = "Quota 超限. 切到 catfish-private-main (内网模型) 继续聊.";
      try {
        const errJson = await resp.json();
        const d = errJson?.detail;
        if (typeof d === "object" && d?.message) {
          friendlyMsg = d.message;
        } else if (typeof d === "string") {
          friendlyMsg = d;
        }
      } catch {
        /* keep default */
      }
      onError(`⚠️ ${friendlyMsg}`);
      return;
    }
    let detail = "";
    try {
      const errJson = await resp.json();
      detail = errJson?.detail?.message || JSON.stringify(errJson);
    } catch {
      detail = await resp.text().catch(() => "");
    }
    onError(`HTTP ${resp.status}: ${detail || "unknown"}`);
    return;
  }
  if (!resp.body) {
    onError("响应没有 stream body");
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let usage: ChatUsage | undefined;
  let finishReason: string | undefined;
  // index → ToolCallAcc
  const toolCallsAcc: Record<number, ToolCallAcc> = {};

  function finalizeToolCallsIfAny() {
    const indices = Object.keys(toolCallsAcc).map(Number).sort((a, b) => a - b);
    if (indices.length === 0) return;

    const finalized: ToolCall[] = [];
    for (const idx of indices) {
      const acc = toolCallsAcc[idx];
      if (!acc.id || !acc.name) continue;
      let args: Record<string, unknown> = {};
      if (acc.argumentsJson.trim()) {
        try {
          args = JSON.parse(acc.argumentsJson);
        } catch (e) {
          // 参数解析失败 — 把原始字符串塞进去让 caller 知道
          finalized.push({
            id: acc.id,
            name: acc.name,
            args: { _raw: acc.argumentsJson, _parse_error: String(e) },
            status: "error",
            error: `参数 JSON 解析失败: ${e}`,
          });
          continue;
        }
      }
      finalized.push({
        id: acc.id,
        name: acc.name,
        args,
        status: "pending",
      });
    }
    if (finalized.length > 0 && onToolCalls) {
      onToolCalls(finalized);
    }
  }

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);

        const lines = chunk.split("\n");
        for (const line of lines) {
          if (!line.startsWith("data:")) continue;
          const data = line.slice(5).trim();
          if (!data) continue;
          if (data === "[DONE]") {
            finalizeToolCallsIfAny();
            onDone({ finish_reason: finishReason, usage });
            return;
          }

          let parsed: unknown;
          try {
            parsed = JSON.parse(data);
          } catch {
            continue;
          }

          if (
            typeof parsed === "object" &&
            parsed !== null &&
            "error" in parsed &&
            typeof (parsed as { error: unknown }).error === "string"
          ) {
            onError((parsed as { error: string }).error);
            return;
          }

          const obj = parsed as {
            choices?: Array<{
              delta?: {
                content?: string;
                tool_calls?: Array<{
                  index?: number;
                  id?: string;
                  type?: string;
                  function?: { name?: string; arguments?: string };
                }>;
              };
              finish_reason?: string | null;
            }>;
            usage?: ChatUsage;
          };
          if (obj.usage) usage = obj.usage;
          const choice = obj.choices?.[0];
          if (!choice) continue;

          if (choice.finish_reason) finishReason = choice.finish_reason;

          const delta = choice.delta;
          if (delta?.content) onDelta(delta.content);

          // 累积 tool_calls 块
          if (delta?.tool_calls) {
            for (const tc of delta.tool_calls) {
              const i = tc.index ?? 0;
              if (!toolCallsAcc[i]) {
                toolCallsAcc[i] = { id: "", name: "", argumentsJson: "" };
              }
              if (tc.id) toolCallsAcc[i].id = tc.id;
              if (tc.function?.name) toolCallsAcc[i].name = tc.function.name;
              if (tc.function?.arguments) {
                toolCallsAcc[i].argumentsJson += tc.function.arguments;
              }
            }
          }
        }
      }
    }
    // 流自然结束(没 [DONE]):也 finalize
    finalizeToolCallsIfAny();
    onDone({ finish_reason: finishReason, usage });
  } catch (e) {
    if ((e as Error).name === "AbortError") {
      onDone({ finish_reason: "abort", usage });
      return;
    }
    onError(`stream 中断: ${stringify(e)}`);
  }
}

function stringify(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}
