/**
 * hermes 运行状态提示的中文 (10/15)。
 *
 * hermes v0.22 在流里发 `event: hermes.status` {kind, text}: 压缩上下文、自动重试、等别的任务、
 * 切备用模型…… 这些 text 是 hermes 代码里写死的英文 (不走语言包, 员工界面是中文也照样英文),
 * kind 又只有 lifecycle / warn / compacted 几大类, 不够细 —— 只能按原文认。
 * 原文出处: agent/conversation_compression.py (COMPACTION_*), chat_completion_helpers / turn_* (重试)。
 *
 * 认不出的英文不直接给员工看 (10/15 鸿波: 英文 + 长时间没进度的提示让人焦虑), 显示"小鲶还在处理…",
 * 原文留在 raw 里 (界面放鼠标悬停提示, 排查用)。本来就是中文的原样显示。
 */

export interface HermesStatusView {
  /** 主文案 (中文) */
  text: string;
  /** 补一句说明: 在做什么 / 大概要多久 (可选) */
  hint?: string;
  /** hermes 发来的原文 */
  raw: string;
}

const CJK = /[一-鿿]/;

type Rule = [RegExp, (m: RegExpMatchArray) => Omit<HermesStatusView, "raw">];

const secs = (s: string) => Math.max(1, Math.round(Number(s)));

const RULES: Rule[] = [
  [/Context compaction complete/i, () => ({ text: "整理完了, 接着处理你的消息…" })],
  [
    /Compacting context/i,
    () => ({
      text: "对话太长了, 小鲶正在把前面的内容整理成摘要, 好接着往下聊",
      hint: "要几分钟, 对话越长越久; 对话内容不会丢, 不用重发",
    }),
  ],
  [
    /Compression (refused|aborted|summary failed|returned an empty)|Skipping concurrent compression|compaction failed/i,
    () => ({ text: "这次没整理成摘要, 对话内容没变, 继续处理" }),
  ],
  [
    /Provider temporarily unavailable — retrying automatically in (\d+)s \(cycle (\d+)\/(\d+)\)/i,
    (m) => ({ text: `模型服务暂时不可用, ${secs(m[1])} 秒后自动重试 (第 ${m[2]}/${m[3]} 轮)` }),
  ],
  [/Automatic recovery gave up/i, () => ({ text: "模型服务一直不可用, 已停止重试, 稍后再试" })],
  [
    /Retrying in ([\d.]+)s \(attempt (\d+)\/(\d+)\)/i,
    (m) => ({ text: `模型那边没响应, ${secs(m[1])} 秒后自动重试 (第 ${m[2]}/${m[3]} 次)` }),
  ],
  [/retrying in ([\d.]+)s/i, (m) => ({ text: `${secs(m[1])} 秒后自动重试` })],
  [
    /Still waiting for the other Hermes process on this session \((\d+)s\)/i,
    (m) => ({ text: `这段对话上一个任务还没做完, 等它结束 (已等 ${m[1]} 秒)` }),
  ],
  [/Another Hermes process kept this session busy too long/i, () => ({ text: "上一个任务占用太久, 这条消息没发出去, 请稍后重发" })],
  [/Another Hermes process is using this session/i, () => ({ text: "这段对话上一个任务还没做完, 等它结束再处理" })],
  [/Session is free; loading the latest transcript/i, () => ({ text: "上一个任务结束了, 接着处理你的消息" })],
  [/Switched to fallback|switching to fallback provider|Trying fallback/i, () => ({ text: "主模型没响应, 换用备用模型" })],
  [/loading (.+?) into memory — (\d+)%/i, (m) => ({ text: `正在加载模型 ${m[2]}%` })],
  [
    /Thinking-only response|reasoning with no final answer|stuck on internal reasoning/i,
    () => ({ text: "模型还在思考, 让它接着把答案说完" }),
  ],
  [/Stream interrupted/i, () => ({ text: "回复中途断了, 先用已收到的内容" })],
  [/Empty response after tool calls|signaled a tool call but sent none/i, () => ({ text: "模型没再回话, 用前面的内容作答" })],
  [/stopped making progress|liveness watchdog/i, () => ({ text: "这一轮卡住太久, 已停止, 可以重发一次" })],
];

export function translateHermesStatus(raw: string): HermesStatusView {
  const text = (raw || "").trim();
  for (const [re, make] of RULES) {
    const m = text.match(re);
    if (m) return { ...make(m), raw: text };
  }
  if (CJK.test(text)) return { text, raw: text };
  return { text: "小鲶还在处理…", raw: text };
}
