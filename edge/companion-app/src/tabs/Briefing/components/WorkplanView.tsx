/** 鲶鱼工作总结 · 周/日双层视图 (5/21 Phase 6).
 *
 * 鸿波 5/21 拍板: 央国企节奏 = 周/月, 不是天. 设计稿 docs/BL-WORKPLAN-DESIGN.md.
 *
 * 布局 D2 (上而下阅读, 跟央企周报习惯一致):
 *   本周 [3 子段: 工作内容 / 关键事件 / 鲶鱼建议]
 *   今日 [3 子段: 工作内容 / 关键事件 / 鲶鱼建议]
 *
 * 静态渲染, 0 按钮 0 展开. 想看明细 → 走下方 EventsDetail / TodosDetail 详情段 (Phase 4 留的).
 */

import type { Workplan, WorkplanEvent, WorkplanSection } from "../../../lib/briefing_workplan";

interface WorkplanViewProps {
  workplan: Workplan;
}

export default function WorkplanView({ workplan }: WorkplanViewProps) {
  return (
    <div style={{ marginTop: "var(--space-4)", display: "flex", flexDirection: "column", gap: 14 }}>
      <SectionBlock title="本周" tone="week" section={workplan.week} />
      <SectionBlock title="今日" tone="today" section={workplan.today} />
    </div>
  );
}

interface SectionBlockProps {
  title: string;
  tone: "week" | "today";
  section: WorkplanSection;
}

function SectionBlock({ title, tone, section }: SectionBlockProps) {
  const isWeek = tone === "week";
  const accentColor = isWeek ? "#2a3a5e" : "#c2410c";  // 周=深蓝, 今日=暖橙 (跟 brand 一致)
  const tintBg = isWeek ? "rgba(80,130,200,0.04)" : "rgba(244,123,61,0.04)";

  return (
    <div
      style={{
        background: tintBg,
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "14px 18px",
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 10,
          marginBottom: 10,
          paddingBottom: 8,
          borderBottom: `2px solid ${accentColor}`,
        }}
      >
        <strong style={{ fontSize: 15, color: accentColor, letterSpacing: 0.5 }}>{title}</strong>
      </header>

      {section.summary && (
        <SubBlock label="工作内容">
          <span style={{ color: "var(--catfish-text)", fontSize: 13, lineHeight: 1.6 }}>{section.summary}</span>
        </SubBlock>
      )}

      {section.events.length > 0 && (
        <SubBlock label="关键事件">
          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 5 }}>
            {section.events.map((e, i) => (
              <EventItem key={i} event={e} accentColor={accentColor} />
            ))}
          </ul>
        </SubBlock>
      )}

      {section.advice && (
        <SubBlock label="鲶鱼建议">
          <span style={{ color: "var(--catfish-text)", fontSize: 13, lineHeight: 1.65 }}>{section.advice}</span>
        </SubBlock>
      )}
    </div>
  );
}

function SubBlock({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div
        style={{
          fontSize: 11,
          fontWeight: 600,
          color: "var(--catfish-text-muted)",
          marginBottom: 4,
          letterSpacing: 0.5,
        }}
      >
        {label}
      </div>
      <div style={{ paddingLeft: 4 }}>{children}</div>
    </div>
  );
}

function EventItem({ event, accentColor }: { event: WorkplanEvent; accentColor: string }) {
  return (
    <li
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        padding: "6px 10px",
        background: "var(--catfish-bg-elevated)",
        borderRadius: 4,
        borderLeft: `2px solid ${accentColor}`,
        fontSize: 12.5,
      }}
    >
      <span
        style={{
          flexShrink: 0,
          minWidth: 90,
          color: accentColor,
          fontWeight: 600,
          fontSize: 12,
          whiteSpace: "nowrap",
        }}
      >
        {event.when}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ color: "var(--catfish-text)", lineHeight: 1.5 }}>{event.title}</div>
        {event.context && (
          <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: 2, lineHeight: 1.5 }}>
            {event.context}
          </div>
        )}
      </div>
    </li>
  );
}
