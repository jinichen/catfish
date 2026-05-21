/** 早安播报 — 日历详情区 (今日 + 未来 7 天) — 抽自 BriefingCard.tsx (5/20 拆分).
 *
 * v2 (5/20 BL-COMPANION-BRIEFING-V2 sub-step 1.3): 单条日程点击展开, 显完整
 * ISO duration + 完整 location + calendar 全名 + 参会人 + 描述 (JXA e.attendees
 * + e.description 已扩字段).
 */

import { useState } from "react";

import type { CalendarEvent } from "../../../lib/tauri";
import { formatDayLabel, formatFullDateRange, formatTime } from "./helpers";

// ── 今日 events 时间表详情 ────────────────────────────────────

/**
 * defaultOpen (5/21 加): 日历事件少 (≤ 3) 时直接展开, 不强制员工点开看. 鸿波 5/21
 * 反馈: '日历就这样一句话, 没什么用呀' — 1-3 件直接列出比折叠后更易扫.
 */
export function EventsDetailSection({ events, defaultOpen = false }: { events: CalendarEvent[]; defaultOpen?: boolean }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const toggle = (i: number) => {
    const next = new Set(expanded);
    if (next.has(i)) next.delete(i);
    else next.add(i);
    setExpanded(next);
  };

  return (
    <details
      open={defaultOpen}
      style={{
        marginTop: "var(--space-3)",
        fontSize: 12,
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-sm)",
        padding: "8px 12px",
      }}
    >
      <summary
        style={{
          cursor: "pointer",
          fontWeight: 500,
          color: "var(--catfish-text)",
          marginBottom: 6,
        }}
      >
        📅 日历详情 · {events.length} 件
        <span style={{ marginLeft: 6, fontSize: 10, color: "var(--catfish-text-muted)", opacity: 0.6, fontWeight: 400 }}>
          (点条目展开)
        </span>
      </summary>
      <ul
        style={{
          listStyle: "none",
          padding: 0,
          margin: "8px 0 0 0",
          display: "flex",
          flexDirection: "column",
          gap: 4,
        }}
      >
        {events.map((e, i) => {
          const isOpen = expanded.has(i);
          return (
            <li
              key={`${e.calendar}-${e.start}-${i}`}
              style={{
                borderLeft: e.all_day
                  ? "2px solid var(--catfish-text-muted)"
                  : "2px solid var(--catfish-cyan)",
              }}
            >
              <div
                onClick={() => toggle(i)}
                style={{
                  display: "flex",
                  gap: 8,
                  alignItems: "baseline",
                  padding: "4px 6px",
                  cursor: "pointer",
                }}
                title={isOpen ? "点击收起" : "点击展开"}
              >
                <span style={{ color: "var(--catfish-text-muted)", fontSize: 9, minWidth: 10 }}>
                  {isOpen ? "▼" : "▶"}
                </span>
                <span
                  style={{
                    fontVariantNumeric: "tabular-nums",
                    color: "var(--catfish-text-muted)",
                    minWidth: 70,
                    fontSize: 11,
                  }}
                >
                  {e.all_day ? "全天" : `${formatTime(e.start)}–${formatTime(e.end)}`}
                </span>
                <span style={{ flex: 1, color: "var(--catfish-text)" }}>{e.summary}</span>
                {e.location && !isOpen && (
                  <span
                    style={{
                      fontSize: 11,
                      color: "var(--catfish-text-muted)",
                      maxWidth: 200,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                    title={e.location}
                  >
                    📍 {e.location}
                  </span>
                )}
                <span style={{ fontSize: 10, color: "var(--catfish-text-muted)", opacity: 0.6 }}>
                  {e.calendar}
                </span>
              </div>
              {isOpen && (
                <div
                  style={{
                    padding: "6px 10px 8px 24px",
                    background: "var(--catfish-bg)",
                    fontSize: 10,
                    color: "var(--catfish-text-muted)",
                    lineHeight: 1.5,
                  }}
                >
                  <div>🕐 {e.all_day ? "全天事件" : formatFullDateRange(e.start, e.end)}</div>
                  {e.location && (
                    <div style={{ marginTop: 3, wordBreak: "break-all" }}>📍 {e.location}</div>
                  )}
                  <div style={{ marginTop: 3 }}>🗓️ 日历: {e.calendar}</div>
                  {/* BL-COMPANION-BRIEFING-V2 sub-step 1.3 (5/20): 参会人 */}
                  {e.attendees && e.attendees.length > 0 && (
                    <div style={{ marginTop: 3 }}>
                      👥 参会人 ({e.attendees.length}):{" "}
                      <span style={{ color: "var(--catfish-text)" }}>
                        {e.attendees.slice(0, 5).join(", ")}
                        {e.attendees.length > 5 && ` 等 ${e.attendees.length} 人`}
                      </span>
                    </div>
                  )}
                  {e.description && (
                    <div
                      style={{
                        marginTop: 4,
                        padding: "4px 6px",
                        background: "var(--catfish-bg-elevated)",
                        borderRadius: 3,
                        color: "var(--catfish-text)",
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                      }}
                    >
                      📝 {e.description}
                    </div>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </details>
  );
}

// ── 跨日 events 按日分组 (BL-CALENDAR-WEEK 5/20) ─────────────

export function WeekEventsDetailSection({
  weekEvents,
  todayEvents,
}: {
  weekEvents: CalendarEvent[];
  todayEvents: CalendarEvent[];
}) {
  // 排除已在今日 EventsDetailSection 显的 events (按 start+summary 比对)
  const todaySet = new Set(todayEvents.map((e) => `${e.start}|${e.summary}`));
  const futureEvents = weekEvents.filter((e) => !todaySet.has(`${e.start}|${e.summary}`));
  if (futureEvents.length === 0) return null;

  const groups = new Map<string, CalendarEvent[]>();
  for (const e of futureEvents) {
    const day = e.start.slice(0, 10);  // ISO 前 10 位 = YYYY-MM-DD
    if (!groups.has(day)) groups.set(day, []);
    groups.get(day)!.push(e);
  }

  return (
    <details
      style={{
        marginTop: "var(--space-2)",
        fontSize: 12,
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-sm)",
        padding: "8px 12px",
      }}
    >
      <summary
        style={{
          cursor: "pointer",
          fontWeight: 500,
          color: "var(--catfish-text)",
          marginBottom: 6,
        }}
      >
        🗓️ 未来 7 天 · {futureEvents.length} 件
      </summary>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}>
        {Array.from(groups.entries()).map(([day, events]) => (
          <div key={day}>
            <div
              style={{
                fontSize: 11,
                color: "var(--catfish-text-muted)",
                marginBottom: 4,
                fontWeight: 500,
              }}
            >
              {formatDayLabel(day)} · {events.length} 件
            </div>
            <ul
              style={{
                listStyle: "none",
                padding: 0,
                margin: 0,
                display: "flex",
                flexDirection: "column",
                gap: 3,
              }}
            >
              {events.map((e, i) => (
                <li
                  key={`${e.start}-${i}`}
                  style={{
                    display: "flex",
                    gap: 8,
                    alignItems: "baseline",
                    padding: "3px 6px",
                    borderLeft: e.all_day
                      ? "2px solid var(--catfish-text-muted)"
                      : "2px solid var(--catfish-cyan-dim)",
                    fontSize: 11,
                  }}
                >
                  <span style={{ color: "var(--catfish-text-muted)", minWidth: 60 }}>
                    {e.all_day ? "全天" : formatTime(e.start)}
                  </span>
                  <span style={{ flex: 1, color: "var(--catfish-text)" }}>{e.summary}</span>
                  {e.location && (
                    <span
                      style={{
                        color: "var(--catfish-text-muted)",
                        maxWidth: 120,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                      title={e.location}
                    >
                      📍 {e.location}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </details>
  );
}
