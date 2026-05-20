/** OpenAI 兼容 wire format helpers — 抽自 chat.ts (5/20 拆分).
 *
 * toWire: ChatMessage[] → OpenAIWireMessage[] (gateway 接的格式)
 * formatFileAttachment: 单个 attachment → text 段 (PDF body / Excel meta / audio transcript 等)
 *
 * OpenAI multimodal content part —— text 或 image_url. Gemini / Qwen3-VL /
 * Qwen-Flash 都接受这个 shape (LiteLLM 透传).
 */

import type { ChatMessage } from "../types/chat";
import { applySteerPrefix } from "./steer";


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

export function toWire(messages: ChatMessage[]): OpenAIWireMessage[] {
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

