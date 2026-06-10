/** P3.3.6 (2026-06-10 鸿波) — 早安 tab 左右两栏 view.
 * P3.3.7 Phase 1 (6/10 鸿波): DetailPane 砍'目标/进展/建议' 静态段, 改成 in-memory
 * task-scoped chat. system prompt 注入 task 上下文 (title / reason / contextRefs /
 * flags / 早晨 options). 历史不持久 — 切 task / 刷新都重置. 不调 tool.
 *
 * 左 sidebar 按 urgency 分组 (急/中/低/已默认处理) 保持不变.
 *
 * 数据 0 后端改动: 复用 MainTask / HandledSilentlyItem / streamChat / advisorTaskState.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import {
  advisorTaskStateClear,
  advisorTaskStateSet,
  type TaskStateFetch,
  type TaskStatus,
} from "../../../lib/advisor_cache";
import { streamChat } from "../../../lib/chat";
import type {
  HandledSilentlyItem,
  MainTask,
} from "../../../lib/briefing_advisor";
import { taskChatAppend, taskChatClear, taskChatGet } from "../../../lib/task_chat";
import type { ChatMessage } from "../../../types/chat";
import { useChatStore } from "../../../store/chat";

interface BriefingTwoColumnViewProps {
  tasks: MainTask[];                                  // 已 filter ignored
  handledItems: HandledSilentlyItem[];
  taskState: TaskStateFetch;
  wasSnoozedYesterday: (title: string) => boolean;
  onStatusChange: (taskTitle: string, status: TaskStatus | null) => void;
}

type UrgencyGroup = "high" | "medium" | "low";

const URGENCY_META: Record<UrgencyGroup, { label: string; color: string; bg: string }> = {
  high:   { label: "急", color: "#c2410c", bg: "rgba(194,65,12,0.08)" },
  medium: { label: "中", color: "#a16207", bg: "rgba(161,98,7,0.08)" },
  low:    { label: "低", color: "#6b7280", bg: "rgba(107,114,128,0.06)" },
};

export default function BriefingTwoColumnView({
  tasks,
  handledItems,
  taskState,
  wasSnoozedYesterday,
  onStatusChange,
}: BriefingTwoColumnViewProps) {
  // 按 urgency 分组
  const grouped = useMemo(() => {
    const g: Record<UrgencyGroup, MainTask[]> = { high: [], medium: [], low: [] };
    for (const t of tasks) {
      const u = (t.urgency === "high" || t.urgency === "medium") ? t.urgency : "low";
      g[u].push(t);
    }
    return g;
  }, [tasks]);

  // 默认选中第一个 high; 没 high 就第一个; 都没就 null
  const defaultId = tasks.length > 0
    ? (grouped.high[0]?.id ?? grouped.medium[0]?.id ?? grouped.low[0]?.id ?? null)
    : null;
  const [selectedId, setSelectedId] = useState<number | null>(defaultId);

  // tasks 变了 (refresh) 同步默认选中
  useEffect(() => {
    if (selectedId == null || !tasks.find((t) => t.id === selectedId)) {
      setSelectedId(defaultId);
    }
  }, [defaultId, tasks, selectedId]);

  const selected = tasks.find((t) => t.id === selectedId) ?? null;

  if (tasks.length === 0 && handledItems.length === 0) {
    return (
      <div className="briefing-2col__empty">
        今天的主菜都处理完了, 喝杯茶吧 ☕
      </div>
    );
  }

  return (
    <div className="briefing-2col">
      <aside className="briefing-2col__sidebar">
        <div className="briefing-2col__sidebar-label">分类</div>

        {(["high", "medium", "low"] as UrgencyGroup[]).map((u) => {
          const items = grouped[u];
          if (items.length === 0) return null;
          const meta = URGENCY_META[u];
          return (
            <div key={u} style={{ marginBottom: 8 }}>
              <div
                className="briefing-2col__group-header"
                style={{ color: meta.color }}
              >
                {meta.label} · {items.length}
              </div>
              {items.map((t) => {
                const status = taskState.today[t.title]?.status as TaskStatus | undefined;
                const isSelected = t.id === selectedId;
                return (
                  <button
                    key={t.id}
                    type="button"
                    className={
                      "briefing-2col__task-row" +
                      (isSelected ? " briefing-2col__task-row--selected" : "") +
                      (status === "done" ? " briefing-2col__task-row--done" : "") +
                      (status === "snoozed" ? " briefing-2col__task-row--snoozed" : "")
                    }
                    style={isSelected ? { background: meta.bg, borderLeftColor: meta.color } : undefined}
                    onClick={() => setSelectedId(t.id)}
                  >
                    <div className="briefing-2col__task-title">
                      {status === "done" && "✓ "}
                      {status === "snoozed" && "⏰ "}
                      {t.title}
                    </div>
                    {t.reason && (
                      <div className="briefing-2col__task-subtitle">
                        {t.reason.length > 24 ? t.reason.slice(0, 24) + "…" : t.reason}
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
          );
        })}

        {handledItems.length > 0 && (
          <div className="briefing-2col__handled">
            <div className="briefing-2col__group-header" style={{ color: "var(--catfish-text-muted)" }}>
              已默认处理 · {handledItems.length}
            </div>
            {handledItems.map((h, i) => (
              <div key={i} className="briefing-2col__handled-row">
                {h.category} {h.count > 0 && <span style={{ opacity: 0.7 }}>· {h.count}</span>}
              </div>
            ))}
          </div>
        )}
      </aside>

      <main className="briefing-2col__detail">
        {selected ? (
          <DetailPane
            key={selected.id}
            task={selected}
            status={(taskState.today[selected.title]?.status ?? null) as TaskStatus | null}
            wasSnoozedYesterday={wasSnoozedYesterday(selected.title)}
            onStatusChange={(s) => onStatusChange(selected.title, s)}
          />
        ) : (
          <div className="briefing-2col__detail-empty">选个待办看详情</div>
        )}
      </main>
    </div>
  );
}

// ─── 右侧详情 (Phase 1 6/10): task-scoped chat ────────────────────

function buildTaskSystemPrompt(task: MainTask): string {
  const lines: string[] = [
    "你是 catfish, 员工的工作参谋. 现在跟员工讨论一条具体待办.",
    "",
    "## 待办",
    `标题: ${task.title}`,
    `紧急度: ${task.urgency === "high" ? "急" : task.urgency === "medium" ? "中" : "低"}`,
  ];
  if (task.reason) {
    lines.push(`理由: ${task.reason}`);
  }
  if (task.contextRefs.length > 0) {
    lines.push("", "## 历史上下文");
    task.contextRefs.forEach((r) => lines.push(`- ${r}`));
  }
  if (task.complianceFlags.length > 0) {
    lines.push("", "## 合规提示");
    task.complianceFlags.forEach((f) => {
      lines.push(`- ${f.severity} 合规 (${f.type}): ${f.reason}${f.suggestion ? ` — 建议: ${f.suggestion}` : ""}`);
    });
  }
  if (task.politicalFlags.length > 0) {
    lines.push("", "## 关键关系");
    task.politicalFlags.forEach((f) => {
      lines.push(`- ${f.severity}: ${f.reason}`);
    });
  }
  if (task.options.length > 0) {
    lines.push("", "## 早晨 LLM 给的 3 个口径建议 (参考, 你可以反驳或调整)");
    task.options.forEach((o) => {
      lines.push(`${o.label} (${o.tone}): ${o.summary}${o.aiLean ? " [早晨 AI 倾向]" : ""}`);
    });
  }
  lines.push(
    "",
    "## 你的工作",
    "- 员工现在跟你直接说. 回答她关于这条待办的具体问题.",
    "- 起草内容 / 帮她做决策 / 给具体下一步.",
    "- 如果她说 '我准备做 A' / '已经做完' / '推迟' 之类的, 提醒她用底部按钮记录状态.",
    "- 简洁回答, 不要重复早晨已给过的建议.",
  );
  return lines.join("\n");
}

function DetailPane({
  task,
  status,
  wasSnoozedYesterday,
  onStatusChange,
}: {
  task: MainTask;
  status: TaskStatus | null;
  wasSnoozedYesterday: boolean;
  onStatusChange: (s: TaskStatus | null) => void;
}) {
  const accent =
    task.urgency === "high" ? "#c2410c" :
    task.urgency === "medium" ? "#a16207" : "#6b7280";

  // Phase 2 — chat state. mount 时从 ~/.catfish/task_chat/<key>.jsonl load 历史.
  // 切 task 自动 reset 因 parent key={task.id}. 跟 in-memory 差别: 重启 / 刷新保留.
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [backendError, setBackendError] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const model = useChatStore((s) => s.model);

  // Phase 2: mount 时 load 历史
  useEffect(() => {
    let cancelled = false;
    setHistoryLoading(true);
    void taskChatGet(task.title)
      .then((hist) => {
        if (cancelled) return;
        // 把 TaskChatMsg 转 ChatMessage (加 id + status="done")
        const loaded: ChatMessage[] = hist.map((m, i) => ({
          id: `hist-${i}-${m.ts}`,
          role: m.role as ChatMessage["role"],
          content: m.content,
          ts: m.ts,
          status: "done",
        }));
        setMessages(loaded);
      })
      .catch((e) => {
        console.warn("[BriefingTwoColumn] load task chat 历史失败:", e);
      })
      .finally(() => {
        if (!cancelled) setHistoryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [task.title]);

  // 新消息进来自动滚到底
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const handleSend = async () => {
    const text = input.trim();
    if (!text || streaming) return;
    setChatError(null);
    setInput("");

    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
      ts: new Date().toISOString(),
      status: "done",
    };
    const assistantMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      content: "",
      ts: new Date().toISOString(),
      status: "streaming",
    };
    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setStreaming(true);

    // Phase 2: 立即把 user 消息持久化 (不等 assistant 完成)
    void taskChatAppend(task.title, "user", text).catch((e) => {
      console.warn("[BriefingTwoColumn] append user 失败:", e);
    });

    // 构造发给 LLM 的 messages: system + 历史 + 新 user
    const systemMsg: ChatMessage = {
      id: "task-system",
      role: "system",
      content: buildTaskSystemPrompt(task),
      ts: new Date().toISOString(),
      status: "done",
    };
    const wireMessages = [systemMsg, ...messages, userMsg];

    abortRef.current = new AbortController();
    let assistantFinalContent = "";
    try {
      await streamChat({
        model,
        messages: wireMessages,
        onDelta: (chunk) => {
          assistantFinalContent += chunk;
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsg.id ? { ...m, content: m.content + chunk } : m,
            ),
          );
        },
        onDone: () => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsg.id ? { ...m, status: "done" } : m,
            ),
          );
          setStreaming(false);
          // Phase 2: assistant 完成后持久化完整 content (空 content 不写)
          if (assistantFinalContent.trim().length > 0) {
            void taskChatAppend(task.title, "assistant", assistantFinalContent).catch((e) => {
              console.warn("[BriefingTwoColumn] append assistant 失败:", e);
            });
          }
        },
        onError: (msg) => {
          setChatError(msg);
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsg.id ? { ...m, status: "error", error: msg } : m,
            ),
          );
          setStreaming(false);
        },
        signal: abortRef.current.signal,
      });
    } catch (e) {
      setChatError(String(e));
      setStreaming(false);
    }
  };

  // Phase 2: 清掉这个 task 的 chat 历史
  const handleClearChat = async () => {
    if (!window.confirm("清掉这条待办的所有对话历史? 不可撤销.")) return;
    try {
      await taskChatClear(task.title);
      setMessages([]);
    } catch (e) {
      console.warn("[BriefingTwoColumn] clear task chat 失败:", e);
    }
  };

  const handleStatusChange = async (s: TaskStatus | null) => {
    setBackendError(null);
    try {
      if (s === null) await advisorTaskStateClear(task.title);
      else await advisorTaskStateSet(task.title, s);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setBackendError(`状态没存 (Rust 后端没 build?): ${msg.slice(0, 100)}`);
    }
    onStatusChange(s);
  };

  return (
    <div className="briefing-2col__detail-inner">
      <div className="briefing-2col__detail-header" style={{ borderLeftColor: accent }}>
        <div className="briefing-2col__detail-badges">
          <span style={{ color: accent, fontSize: 12, fontWeight: 600 }}>
            {task.urgency === "high" ? "急" : task.urgency === "medium" ? "中" : "低"}
          </span>
          {task.reason && (
            <span className="briefing-2col__detail-reason">{task.reason}</span>
          )}
          {wasSnoozedYesterday && (
            <span className="briefing-2col__badge-warn">⏰ 昨天推过</span>
          )}
          {status === "done" && <span className="briefing-2col__badge-done">已完成</span>}
          {status === "snoozed" && <span className="briefing-2col__badge-warn">已推迟</span>}
        </div>
        <h3 className="briefing-2col__detail-title">{task.title}</h3>
      </div>

      <div ref={scrollRef} className="briefing-2col__chat-thread">
        {historyLoading && (
          <div className="briefing-2col__chat-empty">
            <div style={{ opacity: 0.7 }}>加载历史对话…</div>
          </div>
        )}
        {!historyLoading && messages.length === 0 && (
          <div className="briefing-2col__chat-empty">
            <div>跟 AI 直接说这条待办 — 报进度 / 起草 / 问下一步.</div>
            <div className="briefing-2col__chat-empty-hint">
              AI 已经知道: 标题 · 紧急度 · 历史{task.complianceFlags.length > 0 ? " · 合规提示" : ""}
              {task.politicalFlags.length > 0 ? " · 关键关系" : ""}
              {task.options.length > 0 ? ` · ${task.options.length} 个早晨口径` : ""}.
              对话历史会保留, 关 Companion / 切 task 再回来都还在.
            </div>
          </div>
        )}
        {!historyLoading && messages.map((m) => (
          <ChatMsg key={m.id} msg={m} />
        ))}
        {chatError && (
          <div className="briefing-2col__chat-err">⚠️ {chatError}</div>
        )}
      </div>

      {messages.length > 0 && (
        <div className="briefing-2col__chat-toolbar">
          <button
            type="button"
            className="briefing-2col__chat-clear-btn"
            onClick={() => void handleClearChat()}
            title="清掉这个待办的所有对话历史 (不可撤销)"
          >
            清空对话
          </button>
        </div>
      )}

      <div className="briefing-2col__chat-input-row">
        <textarea
          className="briefing-2col__chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void handleSend();
            }
          }}
          placeholder={streaming ? "AI 回答中…" : "Enter 发送 · Shift+Enter 换行"}
          rows={2}
          disabled={streaming}
        />
        <button
          type="button"
          className="briefing-2col__chat-send"
          onClick={() => void handleSend()}
          disabled={!input.trim() || streaming}
        >
          {streaming ? "…" : "发送"}
        </button>
      </div>

      <div className="briefing-2col__actions">
        {status === "done" ? (
          <button type="button" className="briefing-2col__action-btn" onClick={() => void handleStatusChange(null)}>
            撤销完成
          </button>
        ) : status === "snoozed" ? (
          <button type="button" className="briefing-2col__action-btn" onClick={() => void handleStatusChange(null)}>
            撤销推迟
          </button>
        ) : (
          <>
            <button
              type="button"
              className="briefing-2col__action-btn briefing-2col__action-btn--done"
              onClick={() => void handleStatusChange("done")}
            >
              ✓ 标记完成
            </button>
            <button
              type="button"
              className="briefing-2col__action-btn briefing-2col__action-btn--snooze"
              onClick={() => void handleStatusChange("snoozed")}
            >
              ⏰ 推迟到明天
            </button>
            <button
              type="button"
              className="briefing-2col__action-btn briefing-2col__action-btn--ignore"
              onClick={() => void handleStatusChange("ignored")}
            >
              ✗ 不做
            </button>
          </>
        )}
      </div>

      {backendError && (
        <div className="briefing-2col__backend-err">⚠️ {backendError}</div>
      )}
    </div>
  );
}

function ChatMsg({ msg }: { msg: ChatMessage }) {
  if (msg.role === "user") {
    return (
      <div className="briefing-2col__msg briefing-2col__msg--user">
        {msg.content}
      </div>
    );
  }
  return (
    <div
      className={
        "briefing-2col__msg briefing-2col__msg--assistant" +
        (msg.status === "streaming" ? " briefing-2col__msg--streaming" : "") +
        (msg.status === "error" ? " briefing-2col__msg--error" : "")
      }
    >
      {msg.content || (msg.status === "streaming" ? "…" : "")}
    </div>
  );
}

// (P3.3.7 Phase 1: Section / OptionRow / ComplianceFlagInline / PoliticalFlagInline
//  砍掉, 因为 detail pane 改成 chat. flag / option 信息已经在 system prompt 里注入,
//  LLM 会主动用. 老 helper 在 git history 6/10 之前的 commit 找得回.)
