/** OpenAI 兼容 wire format helpers — 抽自 chat.ts (5/20 拆分).
 *
 * toWire: ChatMessage[] → OpenAIWireMessage[] (gateway 接的格式)
 * formatFileAttachment: 单个 attachment → text 段 (PDF body / Excel meta / audio transcript 等)
 *
 * OpenAI multimodal content part —— text 或 image_url. Gemini / Qwen3-VL /
 * Qwen-Flash 都接受这个 shape (LiteLLM 透传).
 */

import type { ChatMessage } from "../types/chat";
import { formatWeChatAttachment } from "./wechatImport";
// P3.5.20.1 (6/17): applySteerPrefix import 砍 — steer 整链退役.


// ── OpenAI 兼容线格式 ──

/** OpenAI multimodal content part —— text 或 image_url. Gemini / Qwen3-VL /
 * Qwen-Flash 都接受这个 shape (LiteLLM 透传)。 */
type OpenAIContentPart =
  | { type: "text"; text: string }
  | { type: "image_url"; image_url: { url: string } };

export interface OpenAIWireMessage {
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
export function formatFileAttachment(att: {
  name: string;
  fileKind?: string;
  previewText?: string;
  meta?: Record<string, unknown>;
  keptPath?: string;
  // BL-L26 (5/7): 大文件 BM25 检索结果 (top-K 跟员工问题相关的段落).
  // 有这个就替代 previewText 注入 — 信息密度比"前 5 页"高很多.
  bm25Passages?: Array<{ text: string; score: number; ord: number }>;
}, compact?: { previewChars: number }): string {
  const kind = att.fileKind || "file";
  const meta = att.meta || {};

  // 9/23: 微信聊天记录 —— 不是文件路径 + execute_code 那一套, 走读取器工具
  if (kind === "wechat") return formatWeChatAttachment(att);

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
    // BL-I4 (5/8) 预留 → BL-VOICE3 (5/10) 真接通; 10/1 起本机转写走会议组件包 (FunASR)
    // 跟其他 file kind 不同 — previewText 已经是**全文转录** (不是预览),
    // 没有 keptPath 给 LLM 读完整, 所以下面走 audio early return 不加 codeHint.
    const dur = meta.duration_sec as number | undefined;
    const chars = meta.transcript_chars as number | undefined;
    metaLine = `音频 · ${dur ? `${dur.toFixed(0)} 秒 · ` : ""}${chars ?? "?"} 字转写 (${meta.model ?? "本机转写"})`;
    // 音频专属格式: 不要 "用 execute_code 读完整" 提示 (转录就是全文)
    return (
      `\n\n=== 附件: ${att.name} (${metaLine}) ===\n` +
      `--- 完整转录文字 (本机转写) ---\n` +
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

  // 10/2: 一条消息附件很多 (一叠发票) 时每个只给短预览, 不重复代码提示 —— 见 toWire
  if (compact) {
    const text = att.previewText || "(空)";
    const short = text.length > compact.previewChars ? `${text.slice(0, compact.previewChars)}…` : text;
    return (
      `\n\n=== 附件: ${att.name} (${metaLine}) ===\n` +
      `[完整文件: ${att.keptPath}]\n` +
      `${short}\n`
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

/** 10/2: 一条消息超过这么多文档附件 → 紧凑模式 (发票这种一次几十张的场景)。
 *  原来上限 6, 每个附件 ~5000 字预览 + 一段代码提示; 30 张发票照这样拼是 15 万字。 */
export const COMPACT_FROM = 6;
/** 紧凑模式下所有文档预览加起来的上限 (字), 平均分给每个文件, 每个至少 PER_FILE_MIN */
export const MANY_FILES_PREVIEW_BUDGET = 40_000;
const PER_FILE_MIN = 600;

export function formatManyFiles(files: Parameters<typeof formatFileAttachment>[0][]): string {
  const previewChars = Math.max(PER_FILE_MIN, Math.floor(MANY_FILES_PREVIEW_BUDGET / files.length));
  const blocks = files.map((a) => formatFileAttachment(a, { previewChars })).join("");
  return (
    `\n\n[共 ${files.length} 个文件。每个只给了前 ${previewChars} 字预览; ` +
    `汇总 / 核对 / 填表要用 execute_code 逐个读上面的 [完整文件] 路径, 不要只凭预览。]` +
    blocks
  );
}

/** 11/24 考试那天连着三次 413: hermes 收的请求体上限 10MB, 而每一轮都把整段历史里
 *  所有截图的 base64 重发一遍 —— 截图一多, 后面每句话都发不出去, 只能开新会话。
 *  现在只把「最近的」图片原样带上: 最新一条用户消息的图永远带, 再往前的从新到旧
 *  累计到这个预算 / 张数为止; 更早的换成一行文字 (名字 + 原图路径), 要看再用工具读。 */
export const WIRE_IMAGE_BUDGET_CHARS = 6_000_000;
export const WIRE_IMAGE_MAX_COUNT = 10;

/** 哪些消息里的哪几张图原样发 (key = 消息下标, value = 那条里要带的图的下标) */
export function pickInlineImages(messages: ChatMessage[]): Map<number, Set<number>> {
  const keep = new Map<number, Set<number>>();
  let used = 0;
  let count = 0;
  let newest = true;
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role !== "user") continue;
    const imgs = (m.attachments ?? []).filter((a) => a.kind === "image");
    if (imgs.length === 0) { newest = false; continue; }
    const set = new Set<number>();
    imgs.forEach((a, j) => {
      const size = a.base64?.length ?? 0;
      if (newest || (used + size <= WIRE_IMAGE_BUDGET_CHARS && count < WIRE_IMAGE_MAX_COUNT)) {
        set.add(j); used += size; count += 1;
      }
    });
    keep.set(i, set);
    newest = false;
  }
  return keep;
}

function droppedImageNote(a: { name: string; keptPath?: string }): string {
  return `\n[图片「${a.name}」前面已经发过, 这轮没再附上` +
    (a.keptPath ? `; 原图在 ${a.keptPath}, 需要再看就用工具读这张图` : "") + "]";
}

export function toWire(messages: ChatMessage[]): OpenAIWireMessage[] {
  const inline = pickInlineImages(messages);
  return messages.map((m, idx) => {
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
      if (fileAttachments.length > COMPACT_FROM) {
        textContent = `${textContent}${formatManyFiles(fileAttachments)}`;
      } else if (fileAttachments.length > 0) {
        const fileBlocks = fileAttachments
          .map((a) => formatFileAttachment(a))
          .join("");
        textContent = `${textContent}${fileBlocks}`;
      }
      // P3.5.20.1 (6/17): applySteerPrefix 调用砍, _steered 字段已退役.

      const keepSet = inline.get(idx) ?? new Set<number>();
      const sentImages = imageAttachments.filter((_, j) => keepSet.has(j));
      for (const [j, att] of imageAttachments.entries()) {
        if (!keepSet.has(j)) textContent += droppedImageNote(att);
      }

      // 没图片要带: 直接返普通 string content (兼容非 vision 模型)
      if (sentImages.length === 0) {
        return { role: "user", content: textContent };
      }

      // 有图片附件: 走 multipart array
      const parts: OpenAIContentPart[] = [];
      parts.push({ type: "text", text: textContent });
      for (const att of sentImages) {
        parts.push({
          type: "image_url",
          image_url: { url: `data:${att.mimeType};base64,${att.base64}` },
        });
      }
      return { role: "user", content: parts };
    }
    // P3.5.20.1 (6/17): user 无附件 — steer prefix 调用砍.
    if (m.role === "user") {
      return {
        role: "user",
        content: m.content || "",
      };
    }
    return {
      role: m.role,
      content: m.content,
    };
  });
}

// P3.5.20.1 (6/17): steer 整链退役, lib/steer.ts → stub (待 git rm).

