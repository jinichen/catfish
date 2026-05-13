/** 聊天会话的核心类型 —— 给 useChat / Chat tab 内部用。
 *
 * 不直接对应 ~/.hermes/state.db 的 messages 表（那是 Hermes 的存储格式）；
 * 这里是 Companion 内存里的运行时表示，Week 3 持久化时再做映射。
 */

export type ChatRole = "user" | "assistant" | "system" | "tool";

/** 工具调用 —— assistant 消息里可能含一个或多个,每个独立 status。 */
export interface ToolCall {
  /** OpenAI 兼容的 call id, 如 "call_abc123" */
  id: string;
  /** 工具名,如 "read_file" / "browser_navigate" / "session_search" */
  name: string;
  /** 解析后的参数。如果 LLM 返回的 arguments JSON 还在累积中, 可能是 {} */
  args: Record<string, unknown>;
  /** UI 状态 —— 不发给 LLM, 只用于 Companion 渲染 */
  status: "pending" | "running" | "done" | "error";
  /** 执行结果(tool_bridge 返回的 result 字段, 通常是 JSON 字符串) */
  result?: unknown;
  /** 执行失败时的错误描述 */
  error?: string;
}

/** 用户附件 —— image / file (PDF/Excel/Word/CSV/TXT/MD).
 *
 * MVP 选型:
 *   - image: data 走 base64 完整放内存, 跟 vision LLM 协议直接对齐
 *   - file: 文件提取的纯文本 (text 字段), 后端 (Tauri Rust → Python helper) 解析
 *           不传 base64 给 LLM (LLM 看不懂 PDF 二进制)
 *
 * 都不写磁盘也不进 state.db (state.db 只存"[📎 N]"占位).
 * 切会话回来附件消失 (只剩文字).
 */
export type AttachmentKind = "image" | "file";

export interface Attachment {
  kind: AttachmentKind;
  /** MIME 类型, 如 "image/png" / "application/pdf" */
  mimeType: string;
  /** 文件名(显示用), 例 "汇报模板.docx" */
  name: string;
  /** 字节大小, 给 UI 显示用 */
  sizeBytes: number;

  /** image 才有: base64 编码 (不含 data URI 前缀, gateway 那边拼) */
  base64?: string;

  // 5/5 重构 (preview-only mode): 不再塞全文进 prompt. file attach 只放 preview
  // (~5K 字), 完整数据 LLM 调 execute_code 走 pandas/openpyxl/pypdfium2 读.

  /** file 才有: parser 类型 — "excel" | "pdf" | "word" | "csv" | "text" */
  fileKind?: string;
  /** file 才有: preview 文本 (~5K 字, sheet 列表 + 列头 + 前 N 行 / 前 N 页 / 前 N 段) */
  previewText?: string;
  /** file 才有: 结构化元信息. excel: {sheets, row_counts}; pdf: {page_count}; etc */
  meta?: Record<string, unknown>;
  /** file 才有: 原文件 absolute path (~/.catfish/uploads/<ts>-<name>),
   *  LLM 用 execute_code 调 pandas/openpyxl 读完整数据. 永远有 (preview-only mode 下没截断概念). */
  keptPath?: string;

  /** BL-L26 (5/7): 大文件 (≥50KB 全文) 的 BM25 sidecar 路径.
   *  发消息时调 attachment_bm25_search(parsedTextPath, query) 取 top-K 段落,
   *  在 toWire() 里替换 previewText 注入 user message. 小文件没这字段. */
  parsedTextPath?: string;

  /** BL-L26: BM25 检索结果 (上面 parsedTextPath 跑出来的 top-K 段落).
   *  useChat send 时填充, toWire 拼进 user message 文本块. */
  bm25Passages?: Array<{ text: string; score: number; ord: number }>;
}

export interface ChatMessage {
  /** 客户端生成的 uuid,渲染 React key 用 */
  id: string;
  role: ChatRole;
  /** 主文本内容;assistant 在 streaming 时这里持续 append */
  content: string;
  /** user 消息的图片/文件附件 (in-memory, 不持久化到 state.db) */
  attachments?: Attachment[];
  /** assistant 消息可能伴随多个 tool_calls(并行 / 串行都有可能) */
  tool_calls?: ToolCall[];
  /** tool 角色消息携带的 call id —— 关联到对应 assistant 的 tool_calls[i].id */
  tool_call_id?: string;
  /** ISO-8601;客户端打的本地时间,纯展示用 */
  ts: string;
  /** streaming 中、完成、出错的状态 */
  status?: "streaming" | "done" | "error";
  /** 错误消息(status=error 时填) */
  error?: string;
  /** BL-AUTO-CONTINUE (5/13 鸿波"长程任务咋办"): 这条 user msg 是 Companion
   *  自动续跑发的, UI 渲染要标记 (淡色 + "🔄 自动续 N/M" 角标). 用户原发的没这字段. */
  _autoContinue?: { round: number; max: number };
  /** BL-HERMES013-RED-1B (5/13 借鉴 Hermes 0.13 ACP /steer): 这条 user msg 是
   *  用户在 LLM 还在 stream 时按 [🎯 改主意] 触发的 in-flight 插话 (路径 A:
   *  断流 + 续接). atContent = LLM 被打断时已生成的内容 (前 200 字给 LLM 上下文).
   *  toWire 拼 "[STEER · 用户中途插话] (我打断你时你正说到 ...)" prefix 给 LLM,
   *  UI 渲染时 user bubble 显角标 "🎯 已插话改方向". 跟 /queue (排队等下一轮)
   *  互补 — /steer 是当前轮就改, /queue 是等当前完了才发. */
  _steered?: { atContent: string };
}

/** 一次会话的运行时状态(目前 store 直接展开到顶层,这个 type 留给 Week 3 持久化) */
export interface ChatSession {
  id: string;
  created_at: string;
  model: string;
  messages: ChatMessage[];
}
