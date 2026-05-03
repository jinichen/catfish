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
import type { TodayLearningStats } from "../../types/learning";

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
        {/* 五一 sprint 5/3 BL-D11: 占位 🐟 → 小尺寸正式头像 */}
        <h3 style={{ margin: 0, display: "inline-flex", alignItems: "center", gap: 8 }}>
          <img src="/catfish-avatar.svg" alt="" width={20} height={20} style={{ display: "block" }} />
          鲶鱼今天学到的
        </h3>
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

          {/* 软技能维度 (#46) */}
          <SoftSkillSection stats={stats} />

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
              <div
                style={{
                  fontSize: 11,
                  color: "var(--catfish-text-muted)",
                  marginBottom: "var(--space-2)",
                  lineHeight: 1.5,
                }}
              >
                📌 顶层 = 稳定身份档案 (小鲶启动时自动加载, 永远记得)
                <br />
                📝 memories/ = 对话中动态学到的, 按主题片段 (小鲶按需检索)
              </div>
              <ul style={listStyle}>
                {stats.memories.map((m) => {
                  const isToplevel = !m.name.includes("/");
                  const icon = isToplevel ? "📌" : "📝";
                  const label = isToplevel
                    ? `${m.name} · 身份档案`
                    : m.name.replace("memories/", "memories/");
                  return (
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
                        <span style={{ fontSize: 13 }} title={isToplevel ? "稳定身份档案" : "动态学到的记忆片段"}>
                          {icon}
                        </span>
                        <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}>
                          {label}.md
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
                  );
                })}
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
            小鲶自动记录你的偏好, 把复杂工作流抽象成可复用 skill。
            这块每天都在变, 是鲶鱼 "self-evolution" 的真证据。
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

// ============================================================
// 软技能维度 section (#46 沟通能力进步追踪)
//
// 设计取舍 (跟 backend 对齐):
//   - 不显示"情绪触发次数" (隐私)
//   - 不打分 / 不打级
//   - 周对比用 ↑↓→ 三态, 不显示百分比 (太冷)
//   - methodologies 是开放性"接触过", 不是"掌握度"
// 数据全空时整段不渲染, 不打扰新员工
// ============================================================

function SoftSkillSection({ stats }: { stats: TodayLearningStats }) {
  const hasAnything =
    stats.coachingSessionsToday > 0 ||
    stats.coachingSessionsThisWeek > 0 ||
    stats.emailsDraftedToday > 0 ||
    stats.methodologiesThisWeek.length > 0;

  if (!hasAnything) return null;

  const trend = trendIndicator(
    stats.coachingSessionsThisWeek,
    stats.coachingSessionsPrevWeek,
  );

  return (
    <div
      style={{
        marginBottom: "var(--space-4)",
        paddingBottom: "var(--space-3)",
        borderBottom: "1px solid var(--catfish-border)",
      }}
    >
      <h4
        style={{
          fontSize: 11,
          color: "var(--catfish-text-muted)",
          marginBottom: "var(--space-3)",
          textTransform: "uppercase",
          letterSpacing: 0.5,
          fontWeight: 600,
        }}
      >
        软技能维度
      </h4>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gap: "var(--space-3)",
          marginBottom: "var(--space-3)",
        }}
      >
        <Stat
          label="今日演练"
          value={stats.coachingSessionsToday.toString()}
          highlight={stats.coachingSessionsToday > 0}
        />
        <Stat
          label="今日起草邮件"
          value={stats.emailsDraftedToday.toString()}
          highlight={stats.emailsDraftedToday > 0}
        />
        <div>
          <div style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
            本周演练 {trend.icon}
          </div>
          <div
            style={{
              fontSize: 22,
              fontWeight: 600,
              fontFamily: "var(--font-mono)",
              color: trend.color,
            }}
            title={`本周 ${stats.coachingSessionsThisWeek} 次, 上周 ${stats.coachingSessionsPrevWeek} 次`}
          >
            {stats.coachingSessionsThisWeek}
            <span
              style={{
                fontSize: 11,
                color: "var(--catfish-text-muted)",
                marginLeft: 6,
                fontWeight: 400,
              }}
            >
              / 上周 {stats.coachingSessionsPrevWeek}
            </span>
          </div>
        </div>
      </div>

      {/* 本周接触的方法论 */}
      {stats.methodologiesThisWeek.length > 0 && (
        <div>
          <div
            style={{
              fontSize: 11,
              color: "var(--catfish-text-muted)",
              marginBottom: 4,
            }}
          >
            本周接触的沟通方法论
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {stats.methodologiesThisWeek.map((m) => (
              <span
                key={m}
                style={{
                  fontSize: 11,
                  padding: "2px 8px",
                  border: "1px solid var(--catfish-border)",
                  borderRadius: 999,
                  fontFamily: "var(--font-mono)",
                  color: "var(--catfish-text)",
                  background: "var(--catfish-bg)",
                }}
              >
                {m}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function trendIndicator(
  thisWeek: number,
  prevWeek: number,
): { icon: string; color: string } {
  if (thisWeek > prevWeek) {
    return { icon: "↑", color: "var(--status-ok, #16a34a)" };
  }
  if (thisWeek < prevWeek) {
    return { icon: "↓", color: "var(--catfish-text-muted)" };
  }
  return { icon: "→", color: "var(--catfish-text-muted)" };
}
