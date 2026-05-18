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
import { fetchWithAuth } from "./me";
import { useAgentStore } from "../store/agent";
import { useChatStore } from "../store/chat";
import { useTeachingStore } from "../store/teaching";
// 5/18 BL-CHAT-FALLBACK-MODEL-REVERT: fetchCatalog import 删了
// (老 BL-FIX45 B fallback 切模型用的). 删 import 防 tsc unused warning.
import { applySteerPrefix } from "./steer";  // BL-HERMES013-RED-1B (5/13 ACP /steer)
// 5/19 BL-COMPANION-CHAT-SWITCH-TO-HERMES Phase 2-2B: hermes API server 路径配置
import { hermesApiConfigGet, hermesApiAuthHeader } from "./tauri";

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
  /** BL-FIX45 (5/11) 内部递归用 — 错误恢复 retry 计数, 防死循环.
   *  外部调用方不应传, 仅 streamChat 自己 retry 时传.
   *
   *  fallback (DEPRECATED 5/18 BL-CHAT-FALLBACK-MODEL-REVERT): 老逻辑切别的
   *    model. 撤回. 字段保留作类型兼容, 新代码不读不写.
   *  401 reauth 已由 fetchWithAuth wrapper 内部处理, 这里不再计数 (BL-FIX45 A+ 5/11).
   *  upstreamFinalRetry (5/18 BL-CHAT-AUTO-RETRY-ON-UPSTREAM-500): 上游 500/502
   *    /503/504 时 sleep 5s 自动 retry **同 model** 一次 (上游间歇挂常 5s 内恢复).
   */
  _retryCounters?: {
    /** @deprecated 5/18 BL-CHAT-FALLBACK-MODEL-REVERT, 不再用 */
    fallback?: number;
    upstreamFinalRetry?: number;
  };
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
  /** BL-TASK-ASSESS (5/15): gateway 在 [DONE] 前多发一条 task_assessment 事件,
   *  Companion 用它做 promise-vs-reality 检测 (assistant 说"已生成"但
   *  cum_has_tool_call=false + 没真生成文件 → ⚠ 嘴炮). */
  task_assessment?: TaskAssessment;
}

export interface TaskAssessment {
  /** OpenAI 兼容客户端忽略未知 object — Companion 嗅这字段拿 metadata */
  object: "task_assessment";
  model: string;
  finish_reason: string | null;
  /** 这一轮 stream 收到的 tool_calls chunk 总数 (不是单次 call 个数) */
  tool_call_count: number;
  /** 这一轮 assistant content 累计字符数 */
  content_chars: number;
  /** 这一轮是否累计有 tool_call (任何 1 个就 true) */
  cum_has_tool_call: boolean;
  /** skill_guard 是否在这次请求触发了铁律注入 */
  skill_guard_fired: boolean;
  /** 这条 session 历史里, agent 真调过 catfish_run_skill 没? */
  ever_called_catfish_run_skill_in_session: boolean;
}

/** Token 缓存 —— OAuth access_token / dev token 兜底.
 *
 * BL-FIX30 (5/9 鸿波诊断): chat.ts 之前完全不读 OAuth keychain, 真员工
 * OIDC 登录后 chat 还是用 dev_token, gateway 关掉 dev_token 通道后直接 401.
 * BL-FIX26 (me.ts) 同款修, chat 这条路漏了. 优先级跟 me.ts getToken 对齐:
 *   0. OAuth keychain access_token (登录员工真 token, 最优先)
 *   1. localStorage 切换器 override (dev 多账号测试)
 *   2. .env CATFISH_DEV_TOKEN (单 token 兜底, 现已注释 → 抛错跳到 3)
 *   3. fallback 'dev-token-local' (gateway 已拒, 触发 LoginGate 重登录)
 *
 * 五一 sprint 5/2 加多账号支持: 切换器选的 token (localStorage) 优先级高于 .env.
 */
// BL-FIX45 A+ (5/11): chat.ts 不再自己 getToken — 走 fetchWithAuth (me.ts).
// 5/12 (companion build 修 TS strict): 删 getToken + _cachedEnvToken +
// _clearTokenCache (整套已无 caller).

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

/** 拼 file attachment 进 prompt (5/5 preview-only 重构).
 *
 * 给 LLM 看的不是数据本身, 是个**导航**:
 *   - 文件类型 / sheet / 行数 / 页数 等 meta
 *   - 前 N 行 / 前 N 页 / 前 N 段 preview
 *   - 完整文件路径
 *   - **强制**说明: 真分析必须 execute_code, 不要基于 preview 猜
 *
 * 这样不管 12 个月 Excel 还是 100 页 PDF 都能稳, 任意大小都 scalable.
 */
function formatFileAttachment(att: {
  name: string;
  fileKind?: string;
  previewText?: string;
  meta?: Record<string, unknown>;
  keptPath?: string;
  // BL-L26 (5/7): 大文件 BM25 检索结果 (top-K 跟员工问题相关的段落).
  // 有这个就替代 previewText 注入 — 信息密度比"前 5 页"高很多.
  bm25Passages?: Array<{ text: string; score: number; ord: number }>;
}): string {
  const kind = att.fileKind || "file";
  const meta = att.meta || {};

  // meta 简短描述, 给 LLM 一眼看出"这文件多大"
  let metaLine = "";
  if (kind === "excel") {
    const sheets = (meta.sheets as string[] | undefined) || [];
    const counts = (meta.row_counts as Record<string, number> | undefined) || {};
    const totalRows = Object.values(counts).reduce((a, b) => a + b, 0);
    metaLine = `Excel · ${sheets.length} 个 sheet (${sheets.join(", ")}) · 共 ${totalRows} 行`;
  } else if (kind === "pdf") {
    metaLine = `PDF · ${meta.page_count ?? "?"} 页`;
    // 5/6 BL-D17: parse_file.py 自动识别到结构化表格 (anchor + sub-records 模式).
    // metaLine 加上"已抽 N 条", LLM 看一眼就知道不用再读 raw text.
    if (meta.structured_path && typeof meta.structured_count === "number") {
      const cols = (meta.structured_columns as string[] | undefined)?.join(", ") || "";
      metaLine += ` · ✅ 已自动抽 ${meta.structured_count} 条结构化记录` +
        (cols ? ` (${cols})` : "");
    }
  } else if (kind === "word") {
    metaLine = `Word · ${meta.paragraph_count ?? "?"} 段` +
      (meta.table_count ? ` · ${meta.table_count} 表格` : "");
  } else if (kind === "csv") {
    metaLine = `CSV · ${meta.total_rows ?? "?"} 行`;
  } else if (kind === "text") {
    metaLine = `纯文本 · ${meta.total_chars ?? "?"} 字`;
  } else if (kind === "audio") {
    // BL-I4 (5/8) 预留 → BL-VOICE3 (5/10) 真接通: 音频走 whisper.cpp 转录
    // 跟其他 file kind 不同 — previewText 已经是**全文转录** (不是预览),
    // 没有 keptPath 给 LLM 读完整, 所以下面走 audio early return 不加 codeHint.
    const dur = meta.duration_sec as number | undefined;
    const chars = meta.transcript_chars as number | undefined;
    metaLine = `音频 · ${dur ? `${dur.toFixed(0)} 秒 · ` : ""}${chars ?? "?"} 字转写 (${meta.model ?? "whisper"})`;
    // 音频专属格式: 不要 "用 execute_code 读完整" 提示 (转录就是全文)
    return (
      `\n\n=== 附件: ${att.name} (${metaLine}) ===\n` +
      `--- 完整转录文字 (whisper.cpp 本地) ---\n` +
      `${att.previewText || "(空)"}\n` +
      `--- /转录 ---`
    );
  } else if (kind === "video") {
    // BL-I3.1 (5/8): 视频抽音轨转写
    const dur = meta.duration_sec as number | undefined;
    const chars = meta.transcript_chars as number | undefined;
    metaLine = `视频 · ${dur ? `${dur.toFixed(0)} 秒 · ` : ""}${chars ?? "?"} 字音轨转写 (画面没分析)`;
  } else {
    metaLine = "文件";
  }

  const codeHint = (() => {
    if (kind === "excel") {
      return (
        `\n# 推荐: 用 pandas 读完整数据\n` +
        `import pandas as pd\n` +
        `xls = pd.ExcelFile("${att.keptPath}")\n` +
        `print(xls.sheet_names)\n` +
        `df = pd.read_excel("${att.keptPath}", sheet_name="<sheet名>")\n` +
        `# 然后 df.head() / df.describe() / df.groupby() ...`
      );
    }
    if (kind === "pdf") {
      // 5/6 BL-D17: 有 structured_path → 直接读 JSON, 不要再啃 raw text 爆 context.
      // (pypdfium2 全文读对大表格 PDF 一定爆: 5万+ 字 = 8万+ tokens)
      const sp = meta.structured_path as string | undefined;
      if (sp && typeof meta.structured_count === "number") {
        return (
          `\n# 已自动抽出 ${meta.structured_count} 条结构化记录, ` +
          `直接读 JSON 写 Excel — 不要再用 pypdfium2 读 raw text (大 PDF 5万+字会爆 context)\n` +
          `import pandas as pd\n` +
          `df = pd.read_json("${sp}")  # 完整 ${meta.structured_count} 条\n` +
          `# df 列: ${(meta.structured_columns as string[] | undefined)?.join(", ") || "见 df.columns"}\n` +
          `# 子记录在 _sub 列 (list of dict). 要展平成行式 Excel:\n` +
          `import pandas as pd, json\n` +
          `recs = json.load(open("${sp}", encoding="utf-8"))\n` +
          `flat = [{**{k: v for k, v in r.items() if k != "_sub"}, **s}\n` +
          `        for r in recs for s in (r.get("_sub") or [{}])]\n` +
          `pd.DataFrame(flat).to_excel("output.xlsx", index=False)`
        );
      }
      return (
        `\n# 推荐: 用 pypdfium2 读全文\n` +
        `import pypdfium2 as pdfium\n` +
        `pdf = pdfium.PdfDocument("${att.keptPath}")\n` +
        `for i in range(len(pdf)):\n` +
        `    print(pdf[i].get_textpage().get_text_range())`
      );
    }
    if (kind === "word") {
      return (
        `\n# 推荐: 用 python-docx 读全文 + 表格\n` +
        `from docx import Document\n` +
        `doc = Document("${att.keptPath}")\n` +
        `for p in doc.paragraphs: print(p.text)\n` +
        `for t in doc.tables: ...`
      );
    }
    if (kind === "csv") {
      return (
        `\n# 推荐: pandas 读\n` +
        `import pandas as pd\n` +
        `df = pd.read_csv("${att.keptPath}")\n` +
        `# df.describe() / df.groupby() ...`
      );
    }
    return (
      `\n# 推荐: 直接 open 读\n` +
      `with open("${att.keptPath}", encoding="utf-8") as f:\n` +
      `    data = f.read()`
    );
  })();

  // BL-L26 (5/7): 大文件且有 BM25 段落 → 用相关段落替代 preview, 信息密度高 30-50%
  if (att.bm25Passages && att.bm25Passages.length > 0) {
    const passages = att.bm25Passages
      .map((p, i) => `[${i + 1}] ${p.text}`)
      .join("\n\n");
    return (
      `\n\n=== 附件: ${att.name} (${metaLine}) ===\n` +
      `[完整文件: ${att.keptPath}]\n\n` +
      `--- 跟你问题相关的 ${att.bm25Passages.length} 个段落 (BM25 检索, 大文件不全部塞 prompt) ---\n` +
      `${passages}\n` +
      `--- /段落 ---\n\n` +
      `🔧 **必须**用 execute_code 读完整数据再回答, 上面只是关键段, 后面可能还有相关内容.` +
      `${codeHint}`
    );
  }

  return (
    `\n\n=== 附件: ${att.name} (${metaLine}) ===\n` +
    `[完整文件: ${att.keptPath}]\n\n` +
    `--- preview (仅前部分, 用 execute_code 读完整) ---\n` +
    `${att.previewText || "(空)"}\n` +
    `--- /preview ---\n\n` +
    `🔧 **必须**用 execute_code 读完整数据再回答, 不要基于 preview 推测后面.` +
    `${codeHint}`
  );
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

      // 拼文档内容到第一个 text part. 用 === 分隔便于模型识别边界.
      // 5/5 重构 (preview-only mode): file attach 永远只给 preview + 完整路径,
      // LLM 100% 用 execute_code 调 pandas/openpyxl/pypdfium2 读完整数据.
      // 不再有"截断"概念 — 任何大小文件都 scalable.
      let textContent = m.content || "";
      if (fileAttachments.length > 0) {
        const fileBlocks = fileAttachments
          .map((a) => formatFileAttachment(a))
          .join("");
        textContent = `${textContent}${fileBlocks}`;
      }
      // BL-HERMES013-RED-1B: /steer 中途插话 — 拼 STEER prefix 给 LLM 看,
      // 防止 LLM 误以为前一轮 assistant 是它正常说完的.
      textContent = applySteerPrefix(textContent, m._steered);

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
    // user 无附件 — 也要 detect _steered 拼 STEER prefix
    if (m.role === "user") {
      return {
        role: "user",
        content: applySteerPrefix(m.content || "", m._steered),
      };
    }
    return {
      role: m.role,
      content: m.content,
    };
  });
}

// applySteerPrefix 提到 lib/steer.ts 单独存放, 让 steer.test.ts 不用拉 env.ts.

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

  // 5/19 BL-COMPANION-CHAT-SWITCH-TO-HERMES Phase 2-2B: 切 hermes API server
  // (端口 8642) 灰度路径. 跟 BL-MEMORY-OWNERSHIP-FIX 对齐 — hermes 当 agent
  // runtime, 内部 memory inject (含 catfish-memory plugin) + tool calling +
  // 调下游 gateway. Companion 退化成纯 UI client.
  //
  // 灰度逻辑: hermes_api.enabled=true → 走 hermes 8642; false → 走老 gateway.
  // 配置在 ~/.catfish/companion.yaml `hermes_api:` 段, 详见
  // docs/HERMES-OPENAI-SERVER-RESEARCH.md + COMPANION-HERMES-AUTH-DESIGN.md.
  //
  // 安全: hermes auth header 由 Rust 端拼 ("Bearer <API_SERVER_KEY>"), JS 只
  // 看到组合好的字符串, raw key 不暴露.
  let hermesCfg: { enabled: boolean; url: string; has_key: boolean } | null = null;
  let hermesAuth: string | null = null;
  try {
    hermesCfg = await hermesApiConfigGet();
    if (hermesCfg.enabled && hermesCfg.has_key) {
      hermesAuth = await hermesApiAuthHeader();
    }
  } catch {
    // Tauri 命令挂 — 走老 gateway 路径 (灰度安全降级)
  }
  const useHermes = hermesCfg !== null && hermesCfg.enabled && hermesAuth !== null;

  const url = useHermes
    ? `${hermesCfg!.url}/v1/chat/completions`
    : `${config.gatewayUrl}/v1/chat/completions`;

  // hermes API server 期望 model="hermes-agent" (固定, hermes 内部决定真 model).
  // 老 gateway 期望真实 model 名 (catfish-private-main / catfish-public-qwen-flash / 等).
  const effectiveModel = useHermes ? "hermes-agent" : model;

  const body: Record<string, unknown> = {
    model: effectiveModel,
    messages: toWire(messages),
    stream: true,
  };
  // 5/19 切 hermes 后**不再传 tools** — hermes 内部管 tool calling, 拼好结果返.
  // 老 gateway 路径仍传 tools.
  if (!useHermes && tools && tools.length > 0) {
    body.tools = tools;
    // tool_choice 默认 auto, 让 LLM 自己决定要不要调
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

  // BL-LEAN-SESSION (5/13 鸿波拍板 "客户无法跑命令行"): 教学模式 toggle 开时
  // 带 X-Catfish-Teaching-Mode: 1, gateway 关 9 个干扰 inject + feedback retry.
  // 关时不带 header, 走完整注入 (副手"懂员工"). 跨 session 隔离, 不重启 gateway.
  //
  // 5/18 BL-COMPANION-VITE-CHUNK-WARN: 改 static import (顶部) — useAgentStore 已经
  // 顶部 static 进来了, teaching 也 static 没有循环依赖风险, 老 dynamic 注释 (zustand
  // 加载顺序) 是历史防御性代码, 实际不需要. Rollup 见 dynamic+static 混用挂 chunk warn.
  if (useTeachingStore.getState().on) {
    agentHeaders["X-Catfish-Teaching-Mode"] = "1";
  }

  // BL-GATEWAY-SOFT-HANDOFF (5/18): 切 model 后下一次请求带 X-Catfish-Prev-Model,
  // gateway 据此把历史 tool_calls 转 inline 文本 (新 model 不支持 tools 时), 防炸.
  // 同 model 续聊 → prevSentModel === current model, gateway 看相同就 no-op.
  // 没发过任何消息 (prevSentModel === null) → 不发 header, 跟老行为一样.
  const _prevSent = useChatStore.getState().prevSentModel;
  if (_prevSent && _prevSent !== model) {
    agentHeaders["X-Catfish-Prev-Model"] = _prevSent;
  }
  // 发完后 (无论成功 / 失败) 更新 prevSentModel = 本次 model, 下一次发送对比基准.
  // 放在最早 — 即使 fetch 抛错, 下次再发也能继续追踪 (老 model 已经"用过"了).
  useChatStore.getState().markModelSent();

  // BL-FIX45 A+ (5/11): 走 fetchWithAuth — 401 自动 reauth + retry, 不再 inline 处理.
  // Authorization header 由 wrapper 自动加.
  //
  // 5/19 Phase 2-2B: hermes 路径**不走 fetchWithAuth** (它假设 OIDC token 401
  // 后 reauth, 但 hermes 用 API_SERVER_KEY 静态 token, 401 reauth 没意义). 改
  // 直接 fetch + hermes auth header (Rust 端拼好的 "Bearer <key>").
  let resp: Response;
  try {
    if (useHermes) {
      resp = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: hermesAuth!,
          ...agentHeaders,
        },
        body: JSON.stringify(body),
        signal,
      });
    } else {
      resp = await fetchWithAuth(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...agentHeaders,
        },
        body: JSON.stringify(body),
        signal,
      });
    }
  } catch (e) {
    onError(`无法连接 ${useHermes ? "hermes API" : "gateway"}: ${stringify(e)}`);
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

    // ── BL-FIX45 A+ (5/11): 401 由 fetchWithAuth wrapper 自动 reauth + retry ──
    // 走到这里说明 wrapper retry 一次仍 401 — IdP 真挂或员工取消登录.
    if (resp.status === 401) {
      onError(
        "🔐 登录刷新后仍 401. 可能 IdP 不可达 / SSO 配置错 / 你关闭了浏览器登录窗. " +
          "检查 catfish-identity 服务或点右上角头像手动登录."
      );
      return;
    }

    // ── 5/18 BL-CHAT-FALLBACK-MODEL-REVERT: 500/502/503/504 单 model 重试 ─
    //
    // 老逻辑 (BL-FIX45 B 5/11): 上游 500 → 静默 fallback 切到 catalog 里下一个
    // 可达 model 最多 2 次. **撤回**, 跟 5/13 BL-FIX23/24 "gateway 不替员工做主"
    // 同精神:
    //   1. 你选 Qwen 因为它中文/国产/合规, 切到 nemotron / gemini 答案质量 +
    //      合规属性都变了, 你不知道. 公司可能配"只用国产 model", 切别的违反 RBAC
    //   2. 用户语义违背: 选了 model = "我要这个", 不是"任何能用的 model"
    //   3. quality 降级不可预测, 5/13 BL-FIX23 撤回的同型问题
    //
    // 新逻辑: 500 → sleep 5s → retry **同 model** 一次. 上游 LLM (Qwen vLLM /
    // DeepSeek) 间歇性挂常 5s 内恢复. retry 仍挂 → 诚实报错让用户决定 (换 model
    // 或稍后再试). upstreamFinalRetry 限 1 次, signal abort 能取消.
    if ([500, 502, 503, 504].includes(resp.status)) {
      const finalRetryCount = params._retryCounters?.upstreamFinalRetry ?? 0;
      // BL-C8 (5/16): 优先用 backend errors.py friendly_upstream_error 翻译过的中文短文案
      let detail = "";
      try {
        const errJson = await resp.json();
        detail =
          errJson?.detail?.friendly ||
          errJson?.detail?.message ||
          JSON.stringify(errJson);
      } catch {
        detail = await resp.text().catch(() => "");
      }

      if (finalRetryCount < 1) {
        onDelta(
          `\n⏳ \`${model}\` 上游 ${resp.status} 暂时不可达, ` +
            `5 秒后自动重试一次 (这段时间按 ⏸ 取消)...\n`,
        );
        try {
          await new Promise<void>((resolve, reject) => {
            const t = setTimeout(resolve, 5000);
            if (signal) {
              const onAbort = () => {
                clearTimeout(t);
                reject(new Error("aborted"));
              };
              if (signal.aborted) onAbort();
              else signal.addEventListener("abort", onAbort, { once: true });
            }
          });
        } catch {
          onError(`⚠️ \`${model}\` 上游 ${resp.status} 不可达. 用户取消自动重试.`);
          return;
        }
        return streamChat({
          ...params,
          _retryCounters: {
            ...params._retryCounters,
            upstreamFinalRetry: 1,
          },
        });
      }

      // retry 仍失败 → 诚实报错, 不替员工做主切别的 model
      onError(
        `⚠️ \`${model}\` 上游 ${resp.status} 不可达 (5s 后重试仍失败). ` +
          `上游 LLM 服务挂了, 你可以: (1) 换一个 model 重发 ` +
          `(2) 稍后再试 (3) 排查 gateway log + 上游 LLM 服务状态.` +
          (detail ? ` 详细: ${detail.slice(0, 200)}` : ""),
      );
      return;
    }

    // BL-C8 (5/16): 底层 fallback 同样优先 backend friendly 翻译.
    let detail = "";
    try {
      const errJson = await resp.json();
      detail =
        errJson?.detail?.friendly ||
        errJson?.detail?.message ||
        JSON.stringify(errJson);
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
  // BL-TASK-ASSESS-2-CLIENT: gateway 在 [DONE] 前发 task_assessment 事件,
  // Companion 缓存在 stream loop 内, 在 onDone 时一起传给 caller.
  let taskAssessment: TaskAssessment | undefined;
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
            onDone({ finish_reason: finishReason, usage, task_assessment: taskAssessment });
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

          // BL-TASK-ASSESS-2-CLIENT: 嗅 task_assessment 事件 (object 字段标记).
          // OpenAI 兼容客户端看到 unknown object 一般忽略, Companion 拿来做断言.
          if (
            typeof parsed === "object" &&
            parsed !== null &&
            (parsed as { object?: unknown }).object === "task_assessment"
          ) {
            taskAssessment = parsed as TaskAssessment;
            continue; // task_assessment 事件不走后面的 choices 解析
          }

          const obj = parsed as {
            choices?: Array<{
              delta?: {
                content?: string;
                // BL-FIX23 L2 (5/9): Qwen3.5 / DeepSeek thinking / Claude Sonnet 4.5+
                // 推理模式 stream 时 delta.content=null, delta.reasoning_content="...".
                // 之前只看 content 导致鲶鱼"半截就停" — 思考阶段全丢, 员工只看到
                // 开头 plan 句被截. 现在落到 onDelta 一起渲染 (后续 BL-FE3 区分思考).
                reasoning_content?: string;
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
          // BL-FIX23 L2 (5/9): reasoning_content 也渲染, 防"半截就停"的核心修法.
          // 鸿波 5/9 抱怨 Qwen3.5 122B 长 context 推理时输出 reasoning_content 不是
          // content, Companion 只读 content 导致看着像"嘴说完话停了". 先让员工看到
          // 思考过程, 不丢内容. demo 后 BL-FE3 加折叠 UI 把思考分开展示.
          if (delta?.reasoning_content) onDelta(delta.reasoning_content);

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
    onDone({ finish_reason: finishReason, usage, task_assessment: taskAssessment });
  } catch (e) {
    if ((e as Error).name === "AbortError") {
      onDone({ finish_reason: "abort", usage, task_assessment: taskAssessment });
      return;
    }
    onError(`stream 中断: ${stringify(e)}`);
  }
}

function stringify(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}
