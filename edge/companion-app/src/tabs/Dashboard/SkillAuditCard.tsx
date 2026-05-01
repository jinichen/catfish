/** Skill 审计卡片 — 五一 sprint Day 2.
 *
 * 展示:
 *   - 今日 skill 调用次数 (成功 / 失败)
 *   - 调用最多的 skill (top 3)
 *   - 失败率 + 失败 skill 列表
 *   - 30 天未用 skill (建议员工删 / 留)
 *   - 平均执行耗时 (慢 skill 识别)
 *
 * 数据源: ~/.catfish/skill_audit.jsonl (tool-bridge run_skill / skill_delete 写)
 *
 * 不展示:
 *   - skill 调用的 params (可能含 PII, audit 也只记 key 列表不记 value)
 *   - 输出文件内容 (路径在 jsonl 里有但卡片不读文件)
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
  unused_30d: string[];
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
        padding: "var(--space-4)",
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
        <span style={{ fontSize: 18 }}>📑</span>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>
          Skill 调用审计
        </h3>
        <span
          style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}
          title="本地 ~/.catfish/skill_audit.jsonl, 客户 IT 可 grep 审"
        >
          (本地 audit jsonl)
        </span>
      </div>

      {error && (
        <div style={{ color: "var(--status-err)", fontSize: 12 }}>
          ⚠ {error}
        </div>
      )}

      {!summary && !error && (
        <div style={{ color: "var(--catfish-text-muted)", fontSize: 12 }}>
          加载中…
        </div>
      )}

      {summary && (
        <div style={{ fontSize: 12, lineHeight: 1.7 }}>
          {/* 今日 概览 */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(3, 1fr)",
              gap: "var(--space-2)",
              marginBottom: "var(--space-3)",
            }}
          >
            <Stat label="今日调用" value={`${summary.today_count}`} />
            <Stat label="成功" value={`${summary.today_ok_count}`} positive />
            <Stat
              label="失败"
              value={`${summary.today_error_count}`}
              negative={summary.today_error_count > 0}
            />
          </div>

          {/* Top skills */}
          {summary.top_skills.length > 0 && (
            <div style={{ marginBottom: "var(--space-3)" }}>
              <div style={{ fontWeight: 500, marginBottom: 4 }}>调用最多</div>
              {summary.top_skills.map((s) => (
                <div
                  key={s.skill_path}
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    color: "var(--catfish-text-muted)",
                  }}
                >
                  <span style={{ fontFamily: "var(--font-mono)" }}>{s.skill_path}</span>
                  <span>{s.count} 次</span>
                </div>
              ))}
            </div>
          )}

          {/* 失败 skill */}
          {summary.failed_skills.length > 0 && (
            <div style={{ marginBottom: "var(--space-3)" }}>
              <div
                style={{
                  fontWeight: 500,
                  marginBottom: 4,
                  color: "var(--status-warn)",
                }}
              >
                ⚠ 最近失败 ({summary.failed_skills.length})
              </div>
              {summary.failed_skills.slice(0, 3).map((f, i) => (
                <div
                  key={i}
                  style={{ color: "var(--catfish-text-muted)", fontSize: 11 }}
                  title={f.error_msg}
                >
                  · <span style={{ fontFamily: "var(--font-mono)" }}>{f.skill_path}</span>:{" "}
                  {f.error_msg.slice(0, 40)}
                </div>
              ))}
            </div>
          )}

          {/* 30 天未用 */}
          {summary.unused_30d.length > 0 && (
            <div style={{ marginBottom: "var(--space-2)" }}>
              <div
                style={{
                  fontWeight: 500,
                  marginBottom: 4,
                  color: "var(--catfish-text-muted)",
                }}
                title="跟员工讨论是否还需要"
              >
                💤 30 天未用 ({summary.unused_30d.length})
              </div>
              <div style={{ color: "var(--catfish-text-muted)", fontSize: 11 }}>
                {summary.unused_30d.slice(0, 3).join(", ")}
                {summary.unused_30d.length > 3 && ` +${summary.unused_30d.length - 3}`}
              </div>
            </div>
          )}

          {/* 平均耗时 */}
          {summary.avg_duration_ms > 0 && (
            <div style={{ color: "var(--catfish-text-muted)", fontSize: 11 }}>
              平均执行耗时: {(summary.avg_duration_ms / 1000).toFixed(1)}s
            </div>
          )}

          {summary.today_count === 0 && (
            <div style={{ color: "var(--catfish-text-muted)" }}>
              今天还没调用 skill. 跟小鲶说"写一份月度汇报" 试试.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  positive,
  negative,
}: {
  label: string;
  value: string;
  positive?: boolean;
  negative?: boolean;
}) {
  return (
    <div>
      <div style={{ color: "var(--catfish-text-muted)", fontSize: 11 }}>
        {label}
      </div>
      <div
        style={{
          fontSize: 18,
          fontWeight: 600,
          color: positive
            ? "var(--catfish-cyan)"
            : negative
            ? "var(--status-err)"
            : "var(--catfish-text)",
        }}
      >
        {value}
      </div>
    </div>
  );
}
