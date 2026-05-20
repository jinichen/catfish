/** 早安播报 (Daily Briefing) — BL-COMPANION-DAILY-BRIEFING-MVP (5/20 鸿波).
 *
 * 主组件 — 4 行总览 + 详情区. 子组件 / helpers 拆到 src/tabs/Briefing/components/
 * (5/20 鸿波"文件太长一定要拆开" 强制拆 1471 → ~300 行规则).
 *
 * 文件位置历史: BriefingCard 第一个版本放在 Dashboard/ (5/20 MVP), 后续抽 BriefingTab
 * 复用 BriefingCard. 现在仍在 Dashboard/ — 改 import path 要改 BriefingTab, 保留位置
 * 不动. 子组件全在 src/tabs/Briefing/components/.
 *
 * MVP 结构:
 *   step1 (5/20): 骨架 + 4 行总览 + 真数据接入 + LLM merged briefing
 *   step2 (5/20): 详情区展开 (邮件 body / TODO done/del / 日程参会人/描述)
 *   step3 (5/20): GoalInput "今日重点" — 写 ~/.catfish/session_goal.txt
 *                跟 gateway inject_session_goal 共享同文件, /goal UI 路径
 */

import { useEffect, useState } from "react";

import {
  fetchBriefingSuggestion,
  fetchLlmJournalTodos,
  fetchMergedBriefing,
} from "../../lib/briefing";
import {
  calendarTodayFetch,
  calendarWeekFetch,
  emailClassifyNow,
  emailDigestFetch,
  journalTodosFetch,
  type CalendarEvent,
  type EmailDigestItem,
  type JournalTodo,
} from "../../lib/tauri";
import { useAgentStore } from "../../store/agent";
import { useChatStore } from "../../store/chat";
import { useEmailStore } from "../../store/email";
import { useUIStore } from "../../store/ui";

import BriefingRow from "../Briefing/components/BriefingRow";
import EmailsDetailSection from "../Briefing/components/EmailsDetail";
import { EventsDetailSection, WeekEventsDetailSection } from "../Briefing/components/EventsDetail";
import GoalInput from "../Briefing/components/GoalInput";
import {
  calendarSummary,
  emailSummary,
  getGreeting,
  suggestionSummary,
  todoSummary,
} from "../Briefing/components/helpers";
import TodosDetailSection from "../Briefing/components/TodosDetail";

// 30 分钟刷一次未读数 (跟 ProactiveCard 同步, 不主动打扰但保新鲜)
const REFRESH_MS = 30 * 60 * 1000;

export default function BriefingCard() {
  const [emails, setEmails] = useState<EmailDigestItem[] | null>(null);
  const [unreadCount, setUnreadCount] = useState<number | null>(null);
  // BL-COMPANION-EMAIL-DIGEST-STEP5 (5/20): urgencyMap 从 useEmailStore 拉
  const urgencyMap = useEmailStore((s) => s.urgencyMap);
  const reconcileUrgency = useEmailStore((s) => s.reconcileFromRust);
  const setUrgencyMap = useEmailStore((s) => s.setUrgencyMap);
  const [emailError, setEmailError] = useState<string | null>(null);
  // BL-CALENDAR-INTEGRATION (5/20): 真日历数据
  const [calEvents, setCalEvents] = useState<CalendarEvent[] | null>(null);
  const [calError, setCalError] = useState<string | null>(null);
  // BL-CALENDAR-WEEK (5/20): 未来 7 天 events
  const [weekEvents, setWeekEvents] = useState<CalendarEvent[] | null>(null);
  // BL-JOURNAL-TODO-EXTRACT (5/20)
  const [todos, setTodos] = useState<JournalTodo[] | null>(null);
  const [todoError, setTodoError] = useState<string | null>(null);
  // BL-BRIEFING-LLM-RANK step2 (5/20)
  const [llmSuggestion, setLlmSuggestion] = useState<string | null>(null);
  const [llmLoading, setLlmLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [lastSync, setLastSync] = useState<Date | null>(null);

  const setActiveTab = useUIStore((s) => s.setActiveTab);
  const model = useChatStore((s) => s.model);
  const personality = useAgentStore((s) => s.personality);

  // forceRefresh=true → ⟳ 按钮主动刷, 跳 calendar Rust 缓存. 自动 interval 走缓存.
  const loadAll = async (forceRefresh = false) => {
    setLoading(true);
    setEmailError(null);
    setCalError(null);
    setTodoError(null);
    // 4 源并发拉, 互不阻塞 (一个挂了另三个仍显)
    const [emailRes, calRes, weekRes, todoRes] = await Promise.allSettled([
      emailDigestFetch(50),
      calendarTodayFetch(forceRefresh),
      calendarWeekFetch(forceRefresh),
      journalTodosFetch(),
    ]);

    // ── email ──
    if (emailRes.status === "fulfilled") {
      try {
        const arr = JSON.parse(emailRes.value);
        if (Array.isArray(arr)) {
          setEmails(arr as EmailDigestItem[]);
          setUnreadCount(arr.length);
        } else {
          setEmails([]);
          setUnreadCount(0);
        }
      } catch {
        setEmails([]);
        setUnreadCount(0);
      }
    } else {
      const e = emailRes.reason;
      setEmailError(e instanceof Error ? e.message : String(e));
      setEmails(null);
      setUnreadCount(null);
    }

    // ── calendar (今日) ──
    if (calRes.status === "fulfilled") {
      try {
        const arr = JSON.parse(calRes.value);
        setCalEvents(Array.isArray(arr) ? (arr as CalendarEvent[]) : []);
      } catch {
        setCalEvents([]);
      }
    } else {
      const e = calRes.reason;
      setCalError(e instanceof Error ? e.message : String(e));
      setCalEvents(null);
    }

    // ── calendar (本周) — 失败不挡卡 ──
    if (weekRes.status === "fulfilled") {
      try {
        const arr = JSON.parse(weekRes.value);
        setWeekEvents(Array.isArray(arr) ? (arr as CalendarEvent[]) : []);
      } catch {
        setWeekEvents([]);
      }
    } else {
      setWeekEvents(null);
    }

    // ── journal TODO ──
    if (todoRes.status === "fulfilled") {
      try {
        const arr = JSON.parse(todoRes.value);
        setTodos(Array.isArray(arr) ? (arr as JournalTodo[]) : []);
      } catch {
        setTodos([]);
      }
    } else {
      const e = todoRes.reason;
      setTodoError(e instanceof Error ? e.message : String(e));
      setTodos(null);
    }

    setLastSync(new Date());
    setLoading(false);

    // ── LLM 邮件评级 (BL-COMPANION-EMAIL-DIGEST-STEP5 5/20 走 store) ──
    if (emailRes.status === "fulfilled") {
      try {
        const arr = JSON.parse(emailRes.value);
        if (Array.isArray(arr) && arr.length > 0) {
          const cached = await reconcileUrgency();
          const unrated = (arr as EmailDigestItem[]).filter((m) => !cached[m.id]);
          if (unrated.length > 0) {
            void emailClassifyNow(unrated).then((newMap) => setUrgencyMap(newMap)).catch(() => {});
          }
        }
      } catch {
        /* ignore */
      }
    }

    // ── LLM 合并建议 (BL-BRIEFING-LLM-MERGE 5/20) ──
    setLlmLoading(true);
    const unread = emailRes.status === "fulfilled"
      ? (() => { try { const a = JSON.parse(emailRes.value); return Array.isArray(a) ? a.length : 0; } catch { return 0; } })()
      : 0;
    const evts = calRes.status === "fulfilled"
      ? (() => { try { const a = JSON.parse(calRes.value); return Array.isArray(a) ? (a as CalendarEvent[]) : []; } catch { return []; } })()
      : [];
    const regexTodos = todoRes.status === "fulfilled"
      ? (() => { try { const a = JSON.parse(todoRes.value); return Array.isArray(a) ? (a as JournalTodo[]) : []; } catch { return []; } })()
      : [];
    const localEmails = emailRes.status === "fulfilled"
      ? (() => { try { const a = JSON.parse(emailRes.value); return Array.isArray(a) ? (a as EmailDigestItem[]) : []; } catch { return []; } })()
      : [];
    const urgentHints = localEmails
      .filter((m) => {
        const u = urgencyMap[m.id];
        return u === "急" || u?.toLowerCase() === "urgent";
      })
      .map((m) => ({ subject: m.subject, sender: m.sender }));

    let suggestion: string | null = null;
    let llmTodos: JournalTodo[] = [];
    const merged = await fetchMergedBriefing(unread, evts, regexTodos, model, urgentHints, personality);
    if (merged) {
      suggestion = merged.suggestion;
      llmTodos = merged.todos;
      console.log("[BriefingCard] merged LLM ✅", { suggestionLen: suggestion.length, todoCount: llmTodos.length });
    } else {
      console.log("[BriefingCard] merged LLM 挂, fallback 两次独立调用");
      const [suggestionRes, llmTodosRes] = await Promise.allSettled([
        fetchBriefingSuggestion(unread, evts, regexTodos, model, personality),
        fetchLlmJournalTodos(regexTodos, model, personality),
      ]);
      suggestion = suggestionRes.status === "fulfilled" ? suggestionRes.value : null;
      llmTodos = llmTodosRes.status === "fulfilled" ? llmTodosRes.value : [];
    }
    setLlmSuggestion(suggestion);

    // TODO 合并: regex (确切) + LLM (推断), 按 text 去重
    if (llmTodos.length > 0) {
      const seen = new Set(regexTodos.map((t) => t.text.trim().toLowerCase()));
      const newOnes = llmTodos.filter((t) => !seen.has(t.text.trim().toLowerCase()));
      if (newOnes.length > 0) {
        setTodos([...regexTodos, ...newOnes]);
      }
    }

    setLlmLoading(false);
  };

  useEffect(() => {
    void loadAll();
    const t = window.setInterval(() => void loadAll(), REFRESH_MS);
    return () => window.clearInterval(t);
  }, []);

  const greeting = getGreeting();
  const today = new Date().toLocaleDateString("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "long",
  });

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        alignSelf: "start",
      }}
    >
      {/* Header: 问候 + 日期 + ⟳ */}
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          marginBottom: "var(--space-3)",
        }}
      >
        <span style={{ fontSize: 18 }}>☀️</span>
        <strong style={{ fontSize: 14 }}>{greeting}</strong>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>{today}</span>
        <button
          type="button"
          onClick={() => void loadAll(true)}
          disabled={loading}
          title="刷新今日总览 (跳缓存强制刷日历)"
          style={{
            marginLeft: "auto",
            background: "transparent",
            border: "1px solid var(--catfish-border)",
            borderRadius: "var(--radius-sm)",
            color: "var(--catfish-text-muted)",
            cursor: loading ? "wait" : "pointer",
            fontSize: 11,
            padding: "2px 8px",
            fontFamily: "inherit",
          }}
        >
          {loading ? "…" : "⟳"}
        </button>
      </header>

      {/* BL-BRIEFING-GOAL-INPUT (5/20): 今日重点输入框 — 写 ~/.catfish/session_goal.txt,
          gateway 端 inject_session_goal 共享同文件, chat 链路自动 inject 让 LLM 锚定. */}
      <GoalInput />

      {/* 4 行总览 */}
      <ul
        style={{
          listStyle: "none",
          padding: 0,
          margin: 0,
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-2)",
        }}
      >
        <BriefingRow
          icon="📬"
          label="邮件"
          summary={emailSummary(unreadCount, loading, emailError, emails, urgencyMap)}
          actionable={(unreadCount ?? 0) > 0}
          onClick={() => setActiveTab("email")}
          actionLabel="查看 →"
        />

        <BriefingRow
          icon="📅"
          label="日历"
          summary={calendarSummary(calEvents, loading, calError)}
          muted={calError !== null || (calEvents?.length ?? 0) === 0}
          hint={calError ? "点⟳重试 / 看授权" : undefined}
        />

        <BriefingRow
          icon="✅"
          label="工作计划"
          summary={todoSummary(todos, loading, todoError)}
          muted={todoError !== null || (todos?.length ?? 0) === 0}
          hint={todoError ? "看 ~/.catfish/employee_journal.md" : undefined}
        />

        <BriefingRow
          icon="💡"
          label="优先建议"
          summary={
            llmLoading
              ? "🤔 小鲶在想…"
              : llmSuggestion ?? suggestionSummary(unreadCount, calEvents, todos)
          }
          muted={
            !llmLoading &&
            llmSuggestion === null &&
            (unreadCount ?? 0) === 0 &&
            (calEvents?.length ?? 0) === 0 &&
            (todos?.length ?? 0) === 0
          }
          hint={llmSuggestion === null && !llmLoading ? "rule-based fallback (LLM 挂)" : undefined}
        />
      </ul>

      {/* 详情区 (5/20 BL-COMPANION-BRIEFING-DETAIL + v2 sub-task 1 可展开) */}
      {(emails?.length ?? 0) > 0 && (
        <EmailsDetailSection
          emails={emails!}
          urgencyMap={urgencyMap}
          onGoEmailTab={() => setActiveTab("email")}
        />
      )}
      {(calEvents?.length ?? 0) > 0 && <EventsDetailSection events={calEvents!} />}
      {(weekEvents?.length ?? 0) > 0 && (
        <WeekEventsDetailSection weekEvents={weekEvents!} todayEvents={calEvents ?? []} />
      )}
      {(todos?.length ?? 0) > 0 && (
        <TodosDetailSection todos={todos!} onChanged={() => void loadAll(true)} />
      )}

      {/* footer: 同步时间 */}
      {lastSync && !loading && (
        <div
          style={{
            fontSize: 10,
            color: "var(--catfish-text-muted)",
            opacity: 0.6,
            textAlign: "right",
            marginTop: "var(--space-2)",
          }}
        >
          上次同步 {lastSync.toLocaleTimeString("zh-CN", {
            hour: "2-digit",
            minute: "2-digit",
            hour12: false,
          })}
        </div>
      )}
    </div>
  );
}
