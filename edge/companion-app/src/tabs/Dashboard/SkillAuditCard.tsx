/** Skill 审计卡片 — 五一 sprint Day 2.
 *
 * 紧凑设计: 今日 0 调用时不展示三个大数字 (浪费空间), 改成一行小字 + 提示.
 * 区分:
 *   - 🆕 从没调用过 (新 ship 的 skill, 让员工试试)
 *   - 💤 30 天未用 (老 skill, 评估删/留)
 *
 * 数据源: ~/.catfish/skill_audit.jsonl (tool-bridge run_skill / skill_delete 写)
 */

import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

interface SkillAuditSummary {
  today_count: number;
  today_ok_count: number;
  today_error_count: number;
  total_count: number;
  top_skills: Array<{ skill_path: string; count: number }>;
  failed_skills: Array<{ skill_path: string; error_msg: string; ts: string }>;
  // 🆕 SKILL.md mtime < 7 天, 不论 audit. 没人提醒"用过没"
  recently_shipped: string[];
  // ⚠ ≥ 7 天 + audit 无记录. 真没人用, 评估删/留
  never_called_old: string[];
  // 💤 调用过但近 30 天没调. 老 skill 评估
  stale_30d: string[];
  avg_duration_ms: number;
}

export default function SkillAuditCard() {
  const [summary, setSummary] = useState<SkillAuditSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchSummary = async () => {
      try {
        const s = await invoke<SkillAuditSummary>("skill_audit_summary");
        setSummary(s);
        setError(null);
      } catch (e) {
        setError(String(e));
      }
    };
    fetchSummary();
    const intv = setInterval(fetchSummary, 30_000);
    return () => clearInterval(intv);
  }, []);

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-3) var(--space-4)",
        gridColumn: "1 / -1",  // 整行宽 (跟 AuditCard 一致, 上下连排)
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-2)",
          marginBottom: "var(--space-2)",
        }}
      >
        <span style={{ fontSize: 16 }}>📑</span>
        <h3 style={{ margin: 0, fontSize: 13, fontWeight: 600 }}>Skill 调用审计</h3>
        {summary && summary.today_count > 0 && (
          <span
            style={{
              fontSize: 11,
              color: "var(--catfish-cyan)",
              marginLeft: "auto",
            }}
          >
            今日 {summary.today_count}{" "}
            {summary.today_error_count > 0 && (
              <span style={{ color: "var(--status-err)" }}>
                ({summary.today_error_count} 失败)
              </span>
            )}
          </span>
        )}
        {summary && summary.today_count === 0 && (
          <span style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginLeft: "auto" }}>
            今日无调用
          </span>
        )}
      </div>

      {error && (
        <div style={{ color: "var(--status-err)", fontSize: 11 }}>⚠ {error}</div>
      )}

      {!summary && !error && (
        <div style={{ color: "var(--catfish-text-muted)", fontSize: 11 }}>加载中…</div>
      )}

      {summary && (
        <div style={{ fontSize: 11, lineHeight: 1.6, color: "var(--catfish-text-muted)" }}>
          {/* Top skills (近 30 天) — 只在有调用时显示 */}
          {summary.top_skills.length > 0 && (
            <div style={{ marginBottom: 4 }}>
              <span style={{ color: "var(--catfish-text)" }}>30 天 top: </span>
              {summary.top_skills.slice(0, 3).map((s, i) => (
                <span key={s.skill_path}>
                  {i > 0 && ", "}
                  <code style={{ fontSize: 11 }}>{s.skill_path}</code>
                  <span> ×{s.count}</span>
                </span>
              ))}
            </div>
          )}

          {/* 最近失败 — 只在有失败时显示 */}
          {summary.failed_skills.length > 0 && (
            <div style={{ marginBottom: 4, color: "var(--status-warn)" }}>
              ⚠ 近 24h 失败 ({summary.failed_skills.length}):{" "}
              {summary.failed_skills
                .slice(0, 2)
                .map((f) => f.skill_path)
                .join(", ")}
            </div>
          )}

          {/* 🆕 最近 7 天 ship — 不评判用过没用过, 鼓励试 */}
          {summary.recently_shipped.length > 0 && (
            <div style={{ marginBottom: 2 }}>
              <span style={{ color: "var(--catfish-cyan)" }}>
                🆕 最近 ship ({summary.recently_shipped.length})
              </span>
              : <code>{summary.recently_shipped.slice(0, 4).join(", ")}</code>
              {summary.recently_shipped.length > 4 &&
                ` +${summary.recently_shipped.length - 4}`}
            </div>
          )}

          {/* ⚠ 老 skill 真没用过 (audit 无记录, ≥7 天) — 评估删/留 */}
          {summary.never_called_old.length > 0 && (
            <div style={{ marginBottom: 2, color: "var(--status-warn)" }}>
              ⚠ 老 skill 没用过 ({summary.never_called_old.length}):{" "}
              <code>{summary.never_called_old.slice(0, 3).join(", ")}</code>
              {summary.never_called_old.length > 3 &&
                ` +${summary.never_called_old.length - 3}`}
              <span style={{ marginLeft: 4 }}>— 评估是否还需要</span>
            </div>
          )}

          {/* 💤 调过但 30 天没调 — 老 skill 评估 */}
          {summary.stale_30d.length > 0 && (
            <div style={{ marginBottom: 2 }}>
              💤 30 天未用 ({summary.stale_30d.length}):{" "}
              <code>{summary.stale_30d.slice(0, 3).join(", ")}</code>
              {summary.stale_30d.length > 3 && ` +${summary.stale_30d.length - 3}`}
            </div>
          )}

          {/* 平均耗时 — 只在有调用时显示 */}
          {summary.avg_duration_ms > 0 && (
            <div>平均耗时: {(summary.avg_duration_ms / 1000).toFixed(1)}s</div>
          )}

          {/* 完全空状态 — 一行简短提示 */}
          {summary.today_count === 0 &&
            summary.top_skills.length === 0 &&
            summary.failed_skills.length === 0 &&
            summary.recently_shipped.length === 0 &&
            summary.never_called_old.length === 0 &&
            summary.stale_30d.length === 0 && (
              <div>还没有 skill 调用记录</div>
            )}
        </div>
      )}
    </div>
  );
}
