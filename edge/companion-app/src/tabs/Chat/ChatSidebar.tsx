/** 对话 tab 左侧 sidebar —— 会话列表 + 新建按钮。
 *
 * 数据源:
 *   - `sessions_list` Tauri command 拉 ~/.hermes/state.db 的 sessions 表
 *   - 区分 cli / companion source 加 badge
 *   - 已激活的会话项 highlight (背景 + 左竖线)
 *
 * 交互:
 *   - 点列表项 → onSelect(id) → ChatTab 调 sessions_get + chat store loadSession
 *   - 点 "+ 新对话" → onNew() → ChatTab 调 reset()
 *
 * 性能:
 *   - 列表只显示 100 条 (Rust 端 LIMIT 100)
 *   - 不实时订阅 db 变更, 父组件提供 refresh 时机 (新发消息 / 新建 session)
 *
 * 样式独立:
 *   - 不引外部 CSS module, 避免又加配置项
 *   - 暗色 / 亮色靠 CSS vars 自动适配
 */

import { useEffect, useState, useCallback } from "react";
import { listSessions, sessionSoftDelete } from "../../lib/tauri";
import { groupSessionsByTitle, type SessionGroupEntry } from "../../lib/sessionGroup";
import * as streamRegistry from "../../lib/streamRegistry";
import type { SessionMeta } from "../../types/session";

interface Props {
  /** 当前激活的会话 id (来自 ChatStore.persistedSessionId), null 表示新对话 */
  activeId: string | null;
  /** 点列表项 */
  onSelect: (id: string) => void;
  /** 点 "+ 新对话" */
  onNew: () => void;
  /** 父组件可以在新发消息后主动 bump 这个值, 强制重拉列表 */
  refreshKey?: number;
  /** BL-COMPANION-UX2 (5/12): 不再禁用切换. 仅用于 UI 提示 ("切换会停止当前").
   * 切换 / 新建逻辑由 ChatTab 处理 (cancel + 等 cleanup + loadSession). */
  busy?: boolean;
}

// BL-SESSIONS-FILTER-PROACTIVE (5/31 鸿波): 工作台 sessions 列表里, proactive
// trigger / time anchor / IMPORTANT user-context 这些自动生成的 "会话" 不该跟
// 员工真聊的混. 按 title / firstUserMessage 前缀识别, 默认隐藏 + toggle 显示.
//
// 识别模式 (覆盖现观察到的所有 auto trigger):
//   - "# 时间锚点" — useProactiveTriggers 启动时插的时间锚消息
//   - "现在 HH:MM. 日历:" — proactive scheduler 周期触发的状态摘要
//   - "[IMPORTANT:" / "[IMPORTANT" — user-context wrapper (5/26 BL-IDENTITY-INJECT)
//   - "📅 " / "⏰ " 前缀的 emoji starter — early proactive prototype 残留
const AUTO_TRIGGER_PATTERNS: RegExp[] = [
  /^#\s*时间锚点/,
  /^现在\s+\d{1,2}[:：]\d{2}/,
  /^\[IMPORTANT[:\s]/i,
  /^📅\s/,
  /^⏰\s/,
];

function isAutoTriggerSession(s: SessionMeta): boolean {
  const probe = (s.title?.trim() || s.firstUserMessage?.trim() || "").slice(0, 60);
  if (!probe) return false;
  return AUTO_TRIGGER_PATTERNS.some((re) => re.test(probe));
}

const SHOW_AUTO_LS_KEY = "catfish:sessions:show_auto_trigger";

export default function ChatSidebar({
  activeId,
  onSelect,
  onNew,
  refreshKey = 0,
  busy = false,
}: Props) {
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  // 7/24 (BL-COMPANION-SIDEBAR-SIMPLIFY): 老逻辑显 "会话 · N / 共 M" · M 是 state.db
  // 裸 COUNT(*) 含软删+后台 session · 员工看着困惑 · tooltip 解释复杂又误导.
  // 简化 · 只显能看见的 (visibleSessions.length) · 差额不管 · countSessions 也不调.
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // BL-SESSIONS-FILTER-PROACTIVE (5/31): 默认隐藏自动 trigger
  const [showAuto, setShowAuto] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem(SHOW_AUTO_LS_KEY) === "1";
    } catch {
      return false;
    }
  });
  // BL-COMPANION-SESSION-SEARCH (7/24): 会话检索 · 按 title / firstUserMessage 子串
  // case-insensitive · 支持中文. state.db 会话上千时 · 手动翻找不到 · 键盘 Cmd+K
  // 常见交互 · 但先做最简 · header 下面加 input · 输入立刻过滤.
  const [searchQuery, setSearchQuery] = useState<string>("");

  const refresh = useCallback(async () => {
    try {
      const list = await listSessions();
      setSessions(list);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  // 应用 filter: showAuto=false 时滤掉 auto trigger 会话
  const visibleSessions = showAuto
    ? sessions
    : sessions.filter((s) => !isAutoTriggerSession(s));
  const hiddenAutoCount = sessions.length - visibleSessions.length;

  // BL-COMPANION-SESSION-SEARCH (7/24): 检索层 · 在 auto-trigger 过滤之后再叠 · title
  // + firstUserMessage 子串 · toLowerCase 兼容英文 · 中文原样 (JS 大小写不区分中文).
  // 空 query · 直接返 visibleSessions · 无性能开销.
  const searchedSessions = searchQuery.trim()
    ? visibleSessions.filter((s) => {
        const q = searchQuery.trim().toLowerCase();
        const title = (s.title || "").toLowerCase();
        const first = (s.firstUserMessage || "").toLowerCase();
        return title.includes(q) || first.includes(q);
      })
    : visibleSessions;

  const toggleShowAuto = useCallback(() => {
    setShowAuto((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(SHOW_AUTO_LS_KEY, next ? "1" : "0");
      } catch {
        /* localStorage 不可用 (隐私模式) — 不致命 */
      }
      return next;
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    refresh().finally(() => {
      if (cancelled) return;
    });
    return () => {
      cancelled = true;
    };
  }, [refresh, refreshKey]);

  // 5/7 BL-D14: 自动 polling sidebar (5s 一次), 让微信 / 飞书 / 企微进来的
  // 新消息自动出现, 不用员工手动点刷新.
  // 5s 间隔权衡:
  //   - 太快 (1s) → CPU/IO 浪费, sidebar 抖动
  //   - 太慢 (30s) → 跨 IM 切换时员工感觉卡 (微信发完切 Companion 没看到)
  //   - 5s → 跟人手动点刷新差不多速度, 不卡
  // SQLite 读 ~10K 条 sessions 200ms 内, 5s 一次完全 OK.
  useEffect(() => {
    const POLL_MS = 5000;
    const t = window.setInterval(() => {
      void refresh();
    }, POLL_MS);
    return () => window.clearInterval(t);
  }, [refresh]);

  // BL-MULTI-SESSION-STREAM (5/24): 订阅 streamRegistry, 后台还在跑的 session
  // 实时显 ⏳. registry.subscribeInflight 在任何 start/finish 时触发, 不是 polling.
  const [inflightIds, setInflightIds] = useState<Set<string>>(
    () => new Set(streamRegistry.getInflightSessions()),
  );
  useEffect(() => {
    const update = () =>
      setInflightIds(new Set(streamRegistry.getInflightSessions()));
    update(); // 挂载时同步一次
    return streamRegistry.subscribeInflight(update);
  }, []);

  return (
    <aside
      style={{
        width: 240,
        minWidth: 200,
        maxWidth: 320,
        height: "100%",
        display: "flex",
        flexDirection: "column",
        borderRight: "1px solid var(--catfish-border)",
        background: "var(--catfish-bg)",
      }}
    >
      <header
        style={{
          padding: "var(--space-3) var(--space-3)",
          borderBottom: "1px solid var(--catfish-border)",
          fontSize: 12,
          fontWeight: 600,
          color: "var(--catfish-text-muted)",
          letterSpacing: "0.04em",
          textTransform: "uppercase",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <span
          title={
            `共 ${visibleSessions.length} 条会话` +
            (hiddenAutoCount > 0
              ? ` · 已隐藏 ${hiddenAutoCount} 条自动触发`
              : "")
          }
        >
          会话 · {searchQuery.trim() ? `${searchedSessions.length} 匹配` : visibleSessions.length}
        </span>
        <span style={{ display: "flex", gap: 4, alignItems: "center" }}>
          {/* BL-SESSIONS-FILTER-PROACTIVE (5/31): toggle 显隐自动 trigger */}
          {hiddenAutoCount > 0 && (
            <button
              onClick={toggleShowAuto}
              title={showAuto ? "隐藏自动触发会话" : `显示 ${hiddenAutoCount} 条自动触发会话`}
              style={{
                background: "transparent",
                border: 0,
                color: showAuto ? "var(--catfish-cyan)" : "var(--catfish-text-muted)",
                cursor: "pointer",
                fontSize: 11,
                padding: "2px 4px",
                fontFamily: "var(--font-mono)",
              }}
            >
              {showAuto ? "−自动" : `+${hiddenAutoCount}自动`}
            </button>
          )}
          <button
            onClick={() => refresh()}
            title="刷新"
            style={{
              background: "transparent",
              border: 0,
              color: "var(--catfish-text-muted)",
              cursor: "pointer",
              fontSize: 12,
              padding: 2,
            }}
          >
            ↻
          </button>
        </span>
      </header>

      {/* BL-COMPANION-SESSION-SEARCH (7/24 达华 POC 会话上千 · 需检索): 搜索框 · 输入立即过滤 title + firstUserMessage */}
      <div
        style={{
          padding: "var(--space-2) var(--space-3)",
          borderBottom: "1px solid var(--catfish-border)",
          position: "relative",
        }}
      >
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="🔍 搜索会话 · title / 首条消息"
          style={{
            width: "100%",
            padding: "6px 28px 6px 10px",
            fontSize: 12,
            border: "1px solid var(--catfish-border)",
            borderRadius: 4,
            background: "var(--catfish-bg)",
            color: "var(--catfish-text)",
            outline: "none",
            boxSizing: "border-box",
            fontFamily: "inherit",
          }}
          onFocus={(e) => {
            e.currentTarget.style.borderColor = "var(--catfish-cyan)";
          }}
          onBlur={(e) => {
            e.currentTarget.style.borderColor = "var(--catfish-border)";
          }}
        />
        {searchQuery && (
          <button
            type="button"
            onClick={() => setSearchQuery("")}
            title="清除搜索"
            style={{
              position: "absolute",
              right: "calc(var(--space-3) + 6px)",
              top: "50%",
              transform: "translateY(-50%)",
              background: "transparent",
              border: 0,
              color: "var(--catfish-text-muted)",
              cursor: "pointer",
              fontSize: 14,
              padding: 2,
              lineHeight: 1,
            }}
          >
            ×
          </button>
        )}
      </div>

      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: "auto",
          padding: "var(--space-2) 0",
        }}
      >
        {loading && (
          <div
            style={{
              padding: "var(--space-3)",
              color: "var(--catfish-text-muted)",
              fontSize: 12,
            }}
          >
            加载中…
          </div>
        )}
        {error && (
          <div
            style={{
              padding: "var(--space-3)",
              color: "#dc2626",
              fontSize: 12,
            }}
          >
            读 state.db 失败: {error}
          </div>
        )}
        {!loading && !error && searchedSessions.length === 0 && (
          <div
            style={{
              padding: "var(--space-3)",
              color: "var(--catfish-text-muted)",
              fontSize: 12,
              lineHeight: 1.5,
            }}
          >
            {searchQuery.trim()
              ? `无匹配 "${searchQuery.trim()}" 的会话`
              : "还没会话 —— 起个新对话试试。"}
          </div>
        )}
        {/* BL-COMPANION-SESSION-DEDUP (5/20 鸿波): 同 title 会话堆叠为一组,
            点同名 chip 展开 sub-session 列表. session id 不变 — 鸿波点的就是
            那个 sub. LLM 生成 title 算法对相似 prompt 出同名, 没去堆叠 sidebar
            一眼看不出哪条是哪条. */}
        {!loading && !error && searchedSessions.length > 0 && (() => {
          const groups = groupSessionsByTitle(searchedSessions);
          // 当 activeId 在某 group 的 sibling 里, 自动展开那组
          const autoExpanded = new Set<string>();
          if (activeId) {
            for (const g of groups) {
              if (g.sessions.some((s) => s.id === activeId) && g.sessions.length > 1) {
                autoExpanded.add(g.key);
              }
            }
          }
          const onDeleteHandler = async (id: string) => {
            try {
              await sessionSoftDelete(id);
              setSessions((prev) => prev.filter((x) => x.id !== id));
              if (id === activeId) {
                const remaining = sessions.filter((x) => x.id !== id);
                if (remaining.length > 0) {
                  onSelect(remaining[0].id);
                }
              }
            } catch (e) {
              console.error("[session-delete] 失败:", e);
              alert(`删除失败: ${e}`);
            }
          };
          return groups.map((g) => (
            <SessionGroup
              key={g.key}
              group={g}
              activeId={activeId}
              autoExpanded={autoExpanded.has(g.key)}
              onSelect={onSelect}
              onDelete={onDeleteHandler}
              streaming={busy}
              inflightIds={inflightIds}
            />
          ));
        })()}
      </div>

      <footer
        style={{
          padding: "var(--space-2)",
          borderTop: "1px solid var(--catfish-border)",
          background: "var(--catfish-bg-elevated)",
          display: "flex",
          flexDirection: "column",
          gap: 6,
        }}
      >
        {/* BL-MULTI-SESSION-STREAM (5/24): 替换老的"流式中, 切换会停止当前"
            提示文案 — 切走不再中断了. 改成"N 个会话进行中", 准确反映同时跑几个流. */}
        {inflightIds.size > 0 && (
          <div
            style={{
              fontSize: 11,
              color: "var(--catfish-text-muted)",
              padding: "2px 6px",
              textAlign: "center",
            }}
            title="后台正在运行的 LLM 流数量. 切走不再中断, 切回看实时状态"
          >
            ⏳ {inflightIds.size} 个会话进行中
          </div>
        )}
        <button
          onClick={() => onNew()}
          style={{
            width: "100%",
            padding: "8px 12px",
            border: "1px solid var(--catfish-border)",
            borderRadius: "var(--radius-sm)",
            background: "var(--catfish-bg)",
            color: "var(--catfish-text)",
            cursor: "pointer",
            fontSize: 13,
            fontWeight: 500,
          }}
          title="开新对话 (老对话继续在后台跑)"
        >
          + 新对话
        </button>
      </footer>
    </aside>
  );
}

// ── BL-COMPANION-SESSION-DEDUP (5/20 鸿波): 同 title 会话堆叠 ──
// 算法在 src/lib/sessionGroup.ts (纯函数 + 单测覆盖).
// SessionGroup 组件: 单条 → 退回 SessionRow; 多条 → 主条 + "+N" chip + 展开 sub-session.

function SessionGroup({
  group,
  activeId,
  autoExpanded,
  onSelect,
  onDelete,
  streaming,
  inflightIds,
}: {
  group: SessionGroupEntry;
  activeId: string | null;
  autoExpanded: boolean;
  onSelect: (id: string) => void;
  onDelete: (sessionId: string) => void;
  /** 全局 busy 标 (老语义保留, 显文案 hint) */
  streaming: boolean;
  /** BL-MULTI-SESSION-STREAM (5/24): 真正 per-session 在跑的 id 集合, 每条 row 看自己在不在里 */
  inflightIds: Set<string>;
}) {
  const [expanded, setExpanded] = useState(autoExpanded);
  // autoExpanded 跟 activeId 变化 — 鸿波点别处后再 active 切回组内仍展开
  useEffect(() => {
    if (autoExpanded) setExpanded(true);
  }, [autoExpanded]);

  // 单 session 情况 — 老 SessionRow 行为完全一致 (没 chip / 没展开)
  if (group.sessions.length === 1) {
    const s = group.sessions[0];
    return (
      <SessionRow
        session={s}
        active={s.id === activeId}
        disabled={false}
        onClick={() => onSelect(s.id)}
        onDelete={onDelete}
        streaming={streaming}
        isInflight={inflightIds.has(s.id)}
      />
    );
  }

  // 多 session 撞名 — 主条 (最新) + 折叠按钮 + 撞名 chip "+N"
  const main = group.sessions[0];
  const siblings = group.sessions.slice(1);
  // group 内总消息数 (鸿波想知道这组合共聊了多少)
  const totalMessages = group.sessions.reduce(
    (sum, s) => sum + (s.messageCount || 0),
    0,
  );
  // 组的 active = 任一 sub-session 是 active
  const anyActive = group.sessions.some((s) => s.id === activeId);

  return (
    <div
      style={{
        // 整组用左边竖线连起来视觉成一组. active 时换浅高亮.
        borderLeft: anyActive
          ? "3px solid var(--catfish-accent, #2563eb)"
          : "3px solid transparent",
      }}
    >
      {/* 主条 — 复用 SessionRow 但传 onClick = 点主条切到 main session */}
      <div style={{ position: "relative" }}>
        <SessionRow
          session={main}
          active={main.id === activeId}
          disabled={false}
          onClick={() => onSelect(main.id)}
          onDelete={onDelete}
          streaming={streaming}
          isInflight={inflightIds.has(main.id)}
        />
        {/* 撞名 chip + 展开按钮覆盖在主条右上角 */}
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setExpanded((v) => !v);
          }}
          title={
            expanded
              ? `收起 ${siblings.length} 条同名会话`
              : `展开 ${siblings.length} 条同名会话 (合计 ${totalMessages} 条消息)`
          }
          style={{
            position: "absolute",
            right: 30, // 留位置给 × 按钮 (SessionRow hover 时显)
            top: 8,
            fontSize: 10,
            padding: "1px 6px",
            border: "1px solid var(--catfish-border)",
            borderRadius: 8,
            background: "var(--catfish-bg-elevated)",
            color: "var(--catfish-text-muted)",
            cursor: "pointer",
            fontFamily: "inherit",
            lineHeight: 1.4,
            display: "flex",
            alignItems: "center",
            gap: 3,
          }}
        >
          <span style={{ fontSize: 9 }}>{expanded ? "▼" : "▶"}</span>
          +{siblings.length} 同名
        </button>
      </div>
      {/* 展开区: sub-sessions */}
      {expanded && siblings.map((s) => (
        <div key={s.id} style={{ paddingLeft: 14, opacity: 0.92 }}>
          <SessionRow
            session={s}
            active={s.id === activeId}
            disabled={false}
            onClick={() => onSelect(s.id)}
            onDelete={onDelete}
            streaming={streaming}
            isSubRow
            isInflight={inflightIds.has(s.id)}
          />
        </div>
      ))}
    </div>
  );
}

function SessionRow({
  session,
  active,
  disabled,
  onClick,
  onDelete,
  streaming = false,  // BL-COMPANION-UX2 (5/12): 提示用, 不再禁用
  isSubRow = false,    // BL-COMPANION-SESSION-DEDUP (5/20): 撞名展开里的 sub-session
  isInflight = false,  // BL-MULTI-SESSION-STREAM (5/24): 这条 session 有 stream 在跑
}: {
  session: SessionMeta;
  active: boolean;
  disabled: boolean;
  onClick: () => void;
  /** BL-SESSION-MGMT C (5/15): hover × 点了调, 父级处理软删 + refresh */
  onDelete: (sessionId: string) => void;
  streaming?: boolean;
  isSubRow?: boolean;
  isInflight?: boolean;
}) {
  const [hover, setHover] = useState(false);
  // BL-SESSION-MGMT C (5/15): 二次确认状态. 首次点 × → confirming=true (按钮变 "确定?"),
  // 2 秒内再点 → 真删. 超时自动 reset. 替代 confirm() 浏览器原生对话框 (Tauri WebView 不稳).
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  // BL-SESSION-MGMT A (5/15): title 没生成时优先用首条 user message, 比裸 timestamp 友好.
  // 短 session (≤2 条) summarizer 不跑, title 永远 null, 之前显 (20260515_xxx) 你都不知道聊啥.
  const title =
    session.title?.trim()
    || session.firstUserMessage?.trim()
    || `(${session.id.slice(0, 17)})`;
  const subtitle = formatRelativeTime(session.startedAt);

  // streaming hint: 鼠标悬停时提示 "点会停止当前流"
  const titleHint = streaming && !active
    ? "切换会话 — 自动停止当前 LLM 流"
    : title;
  return (
    <div
      role="button"
      tabIndex={disabled ? -1 : 0}
      onClick={disabled ? undefined : onClick}
      title={titleHint}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onKeyDown={(e) => {
        if (disabled) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      style={{
        position: "relative",
        // BL-COMPANION-SESSION-DEDUP (5/20): sub-row 比主条 padding 略减 + 字体小
        padding: isSubRow ? "6px 12px 6px 10px" : "8px 12px 8px 14px",
        cursor: disabled ? "not-allowed" : "pointer",
        background: active
          ? "var(--catfish-bg-elevated)"
          : "transparent",
        // sub-row 不画 borderLeft 避免跟父 group 的 borderLeft 撞 (双竖线)
        borderLeft: isSubRow
          ? "none"
          : active
            ? "3px solid var(--catfish-accent, #2563eb)"
            : "3px solid transparent",
        opacity: disabled ? 0.5 : 1,
        userSelect: "none",
        fontSize: isSubRow ? 12 : undefined,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontSize: 13,
          fontWeight: active ? 600 : 500,
          color: "var(--catfish-text)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        <SourceBadge source={session.source} />
        {/* BL-MULTI-SESSION-STREAM (5/24): 在跑的会话显 ⏳ 转圈, 让员工知道
            "我已经切走但小鲶还在那边干活". active 也显, 提示当前流不再因切走中断. */}
        {isInflight && (
          <span
            title="此会话有 LLM 流正在后台运行 (切走不再中断)"
            style={{
              fontSize: 11,
              flexShrink: 0,
              opacity: 0.9,
              animation: "catfish-inflight-pulse 1.4s ease-in-out infinite",
            }}
          >
            ⏳
          </span>
        )}
        {/* BL-LONG-RUNNING-V1-PHASE-E (6/1): 推断"可能仍在 hermes 后台跑"的标识.
            只在 isInflight=false (内存里没看到) 但 db 推断"未 end + 最近活动" 时显.
            灰色 ⌛, 不 pulse — 跟 isInflight ⏳ cyan pulse 区分. 提示员工切回看看. */}
        {!isInflight && session.isPossiblyStreaming && (
          <span
            title="此会话可能仍在 hermes 后台跑 (没正式结束 + 最近 5 分钟活动). 点开看看."
            style={{
              fontSize: 11,
              flexShrink: 0,
              opacity: 0.55,
              color: "var(--catfish-text-muted)",
            }}
          >
            ⌛
          </span>
        )}
        <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>
          {title}
        </span>
        {/* BL-SESSION-MGMT C (5/15): hover 显 × 软删按钮 + 两次点击确认 (Notion/Linear 模式).
            首次点击 → confirmingDelete=true, 按钮变"确定?" + 计时 2 秒.
            2 秒内再点 → 真调 onDelete.
            2 秒超时自动 reset.
            用 onMouseDown 而不是 onClick: 父 div 的 role=button + tabIndex 在 Tauri WebView
            里偶发吃 click 事件, mouseDown 100% 触发. stopPropagation 防切 session. */}
        {hover && !disabled && (
          <button
            type="button"
            onMouseDown={(e) => {
              e.stopPropagation();
              e.preventDefault();
              if (confirmingDelete) {
                console.log("[session-delete] confirm 真删 for", session.id);
                onDelete(session.id);
                setConfirmingDelete(false);
              } else {
                console.log("[session-delete] 第一次点, 等确认 for", session.id);
                setConfirmingDelete(true);
                // 2 秒超时自动 reset
                setTimeout(() => setConfirmingDelete(false), 2000);
              }
            }}
            onClick={(e) => {
              e.stopPropagation();
              e.preventDefault();
            }}
            title={confirmingDelete ? "再点一次真删" : "软删 (30 天内可 restore)"}
            style={{
              border: 0,
              background: confirmingDelete
                ? "var(--catfish-error, #dc2626)"
                : "rgba(220, 38, 38, 0.12)",
              color: confirmingDelete ? "white" : "var(--catfish-error, #dc2626)",
              cursor: "pointer",
              fontSize: confirmingDelete ? 11 : 16,
              fontWeight: 600,
              padding: confirmingDelete ? "3px 8px" : "2px 8px",
              borderRadius: 4,
              flexShrink: 0,
              lineHeight: 1,
              whiteSpace: "nowrap",
            }}
          >
            {confirmingDelete ? "确定?" : "×"}
          </button>
        )}
      </div>
      <div
        style={{
          fontSize: 11,
          color: "var(--catfish-text-muted)",
          marginTop: 2,
          display: "flex",
          gap: 8,
        }}
      >
        <span>{subtitle}</span>
        <span>·</span>
        <span>{session.messageCount} 条</span>
      </div>
    </div>
  );
}

// 5/7 BL-D14: 多入口区分 (Companion / CLI / 飞书 / 微信 / 企微 / Telegram)
const SOURCE_BADGE: Record<string, { color: string; label: string; emoji: string }> = {
  companion: { color: "#2563eb", label: "Companion 桌面", emoji: "🐟" },
  cli: { color: "#6b7280", label: "终端 CLI", emoji: "⌨" },
  weixin: { color: "#10b981", label: "微信", emoji: "💬" },
  feishu: { color: "#7c3aed", label: "飞书", emoji: "🪽" },
  lark: { color: "#7c3aed", label: "Lark", emoji: "🪽" },
  wecom: { color: "#f59e0b", label: "企业微信", emoji: "💼" },
  dingtalk: { color: "#0ea5e9", label: "钉钉", emoji: "📌" },
  telegram: { color: "#3b82f6", label: "Telegram", emoji: "📱" },
  discord: { color: "#5865f2", label: "Discord", emoji: "💬" },
  slack: { color: "#4a154b", label: "Slack", emoji: "💼" },
  qq: { color: "#1da1f2", label: "QQ", emoji: "🐧" },
};

function SourceBadge({ source }: { source?: string }) {
  if (!source) return null;
  const meta = SOURCE_BADGE[source.toLowerCase()] || {
    color: "var(--catfish-text-muted)",
    label: source,
    emoji: "❓",
  };
  return (
    <span
      title={meta.label + " 起的对话"}
      style={{
        display: "inline-block",
        width: 6,
        height: 6,
        borderRadius: "50%",
        background: meta.color,
        flexShrink: 0,
      }}
    />
  );
}

/** 简单的相对时间 —— 几分钟 / 几小时 / 几天前 */
function formatRelativeTime(iso: string): string {
  const t = Date.parse(iso);
  if (!t) return "";
  const diff = Date.now() - t;
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)} 天前`;
  // > 1 周直接显示日期
  return new Date(t).toLocaleDateString("zh-CN", {
    month: "numeric",
    day: "numeric",
  });
}
