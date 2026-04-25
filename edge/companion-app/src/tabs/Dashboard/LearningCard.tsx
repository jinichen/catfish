/** 鲶鱼今天学到的 —— Self-Evolution 可见化 M1
 *
 * 展示:
 *   - 一句人话 summary
 *   - 4 个数字: 对话 / 工具调用 / token / memory 更新 / 新 skill
 *   - 今天新增的 skill 列表 (有则展开)
 *   - 今天更新的 memory 列表 (有则展开)
 */

import { useLearning } from "../../hooks/useLearning";
import { formatTokens } from "../../lib/format";

export default function LearningCard() {
  const { stats, error } = useLearning();

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        gridColumn: "1 / -1", // 占两列宽 (4 卡片 grid 里的 5th 跨整行)
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          marginBottom: "var(--space-3)",
        }}
      >
        <h3 style={{ margin: 0 }}>🐟 鲶鱼今天学到的</h3>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          每 30s 自动刷新
        </span>
      </div>

      {error && (
        <div style={{ color: "var(--status-err)", fontSize: 12 }}>
          读取失败: {error}
        </div>
      )}
      {!stats && !error && <div>加载中…</div>}

      {stats && (
        <>
          {/* 一句话 summary */}
          <div
            style={{
              fontSize: 14,
              color: "var(--catfish-text)",
              marginBottom: "var(--space-4)",
              lineHeight: 1.5,
            }}
          >
            {stats.summary}
          </div>

          {/* 四联数字 */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(4, 1fr)",
              gap: "var(--space-3)",
              marginBottom: "var(--space-4)",
              paddingBottom: "var(--space-3)",
              borderBottom: "1px solid var(--catfish-border)",
            }}
          >
            <Stat label="对话" value={stats.sessionsToday.toString()} />
            <Stat label="工具调用" value={stats.toolCallsToday.toString()} />
            <Stat
              label="Token 消耗"
              value={formatTokens(stats.totalTokensToday)}
            />
            <Stat
              label="新增 skill"
              value={stats.newSkillsCount.toString()}
              highlight={stats.newSkillsCount > 0}
            />
          </div>

          {/* 今天新增的 skill */}
          {stats.newSkills.length > 0 && (
            <Section
              title={`今天新增 / 修改的 skill (${stats.newSkills.length})`}
            >
              <ul style={listStyle}>
                {stats.newSkills.map((s) => (
                  <li
                    key={s.fullName}
                    style={{
                      display: "flex",
                      gap: "var(--space-2)",
                      padding: "var(--space-1) 0",
                      fontSize: 12,
                      borderBottom: "1px dashed var(--catfish-border)",
                    }}
                  >
                    <span
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontWeight: 600,
                        flexShrink: 0,
                      }}
                    >
                      {s.fullName}
                    </span>
                    <span
                      style={{
                        color: "var(--catfish-text-muted)",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                      title={s.description}
                    >
                      {s.description}
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {/* memory 文件状态 */}
          {stats.memories.length > 0 && (
            <Section
              title={`Memory (${stats.memoriesUpdatedToday}/${stats.memories.length} 今天更新)`}
            >
              <ul style={listStyle}>
                {stats.memories.map((m) => (
                  <li
                    key={m.name}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      padding: "var(--space-1) 0",
                      fontSize: 12,
                    }}
                  >
                    <span style={{ display: "flex", gap: "var(--space-2)", alignItems: "center" }}>
                      <span
                        style={{
                          display: "inline-block",
                          width: 6,
                          height: 6,
                          borderRadius: "50%",
                          background: m.modifiedToday
                            ? "var(--status-ok)"
                            : "var(--status-idle)",
                        }}
                      />
                      <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}>
                        {m.name}.md
                      </span>
                      <span style={{ color: "var(--catfish-text-muted)" }}>
                        {formatBytes(m.size)}
                      </span>
                    </span>
                    <span
                      style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}
                    >
                      {m.modifiedToday ? "今天更新" : timeAgo(m.modifiedAt)}
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {/* 教育性 footer */}
          <div
            style={{
              marginTop: "var(--space-3)",
              paddingTop: "var(--space-3)",
              borderTop: "1px solid var(--catfish-border)",
              fontSize: 11,
              color: "var(--catfish-text-muted)",
              lineHeight: 1.5,
            }}
          >
            小鲶在用 hermes 的 memory tool 自动记录你的偏好,用 skill_manage
            自动把复杂工作流抽象成可复用 skill。这块每天都在变, 是 catfish
            "self-evolution" 的真证据。
          </div>
        </>
      )}
    </div>
  );
}

const listStyle: React.CSSProperties = {
  listStyle: "none",
  padding: 0,
  margin: 0,
};

function Stat({
  label,
  value,
  highlight,
}: {
  label: string;
  value: string;
  highlight?: boolean;
}) {
  return (
    <div>
      <div style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
        {label}
      </div>
      <div
        style={{
          fontSize: 22,
          fontWeight: 600,
          fontFamily: "var(--font-mono)",
          color: highlight
            ? "var(--catfish-cyan-dim)"
            : "var(--catfish-text)",
        }}
      >
        {value}
      </div>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ marginBottom: "var(--space-3)" }}>
      <h4
        style={{
          fontSize: 11,
          color: "var(--catfish-text-muted)",
          marginBottom: "var(--space-2)",
          textTransform: "uppercase",
          letterSpacing: 0.5,
          fontWeight: 600,
        }}
      >
        {title}
      </h4>
      {children}
    </div>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function timeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = (Date.now() - then) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)}m 前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h 前`;
  return `${Math.floor(diff / 86400)}d 前`;
}
