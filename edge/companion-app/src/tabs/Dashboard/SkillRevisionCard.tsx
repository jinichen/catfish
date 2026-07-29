/** Skill 改进提议卡 — BL-MM14 / MM15 (5/8).
 *
 * 跟 ProposedSkillsCard (BL-MM9) 区别:
 *   - ProposedSkillsCard: 抽**新** skill 提议
 *   - SkillRevisionCard (本卡): 改**老** skill 提议 + BL-MM15 有效性跟踪
 *
 * 数据源: ~/.catfish/skill_revisions.jsonl (BL-MM13 工具写)
 *
 * UI 三段:
 *   1. 待处理 (pending): 新 propose, 员工 click ✅ 采纳 / ❌ 拒绝
 *   2. 待评估 (effectiveness_due): 已采纳 ≥14d, 该看看是否有效
 *   3. 历史 (recent_resolved): 已 accept / reject 最近 20 条
 */

import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

interface RevisionView {
  revision_id: string;
  skill_path: string;
  current_version: string;
  proposed_version: string;
  reason: string;
  diff_summary: string;
  evidence_summary: string;
  status: string; // "proposed" | "accepted" | "rejected"
  proposed_ts: number;
  proposed_ts_iso: string;
  baseline_quality_score?: number;
  accepted_days_ago?: number;
}

interface RevisionSummary {
  total_proposed: number;
  total_accepted: number;
  total_rejected: number;
  pending: RevisionView[];
  recent_resolved: RevisionView[];
  effectiveness_due: RevisionView[];
  file_size_bytes: number;
}

interface EffectivenessReport {
  revision_id: string;
  skill_path: string;
  baseline: number;
  current: number;
  delta: number;
  verdict: string;
  recommendation: string;
}

/** Skill 当前 quality_score map (从 SkillAuditCard 拉的副本简化版).
 * 这里只在 accept 调用时拿当前 score 做 baseline, 评估时同样.
 * 复用 BL-MM12 命令.
 */
async function fetchCurrentQualityScore(skillPath: string): Promise<number | null> {
  try {
    const auditSummary = (await invoke("skill_audit_summary")) as {
      quality_scores?: Array<{ skill_path: string; score: number }>;
    };
    const found = auditSummary.quality_scores?.find((s) => s.skill_path === skillPath);
    return found ? found.score : null;
  } catch {
    return null;
  }
}

function formatTsAgo(ts: number): string {
  const elapsedSec = Math.max(0, Date.now() / 1000 - ts);
  const min = Math.floor(elapsedSec / 60);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const days = Math.floor(hr / 24);
  return `${days} 天前`;
}

export default function SkillRevisionCard() {
  const [data, setData] = useState<RevisionSummary | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // revision_id 正在处理
  const [effectReport, setEffectReport] = useState<EffectivenessReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      const summary = (await invoke("skill_revision_summary")) as RevisionSummary;
      setData(summary);
      setError(null);
    } catch (e) {
      setError(`读 skill_revisions.jsonl 失败: ${e}`);
    }
  }

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 30_000);
    return () => clearInterval(id);
  }, []);

  async function handleAccept(rev: RevisionView) {
    setBusy(rev.revision_id);
    try {
      const baseline = await fetchCurrentQualityScore(rev.skill_path);
      await invoke("skill_revision_accept", {
        args: {
          revision_id: rev.revision_id,
          baseline_quality_score: baseline,
        },
      });
      await refresh();
    } catch (e) {
      setError(`采纳失败: ${e}`);
    } finally {
      setBusy(null);
    }
  }

  async function handleReject(rev: RevisionView) {
    setBusy(rev.revision_id);
    try {
      await invoke("skill_revision_reject", {
        args: {
          revision_id: rev.revision_id,
          comment: null,
        },
      });
      await refresh();
    } catch (e) {
      setError(`拒绝失败: ${e}`);
    } finally {
      setBusy(null);
    }
  }

  async function handleCheckEffectiveness(rev: RevisionView) {
    setBusy(rev.revision_id);
    try {
      const current = await fetchCurrentQualityScore(rev.skill_path);
      if (current === null) {
        setError(`找不到 ${rev.skill_path} 当前 quality_score`);
        return;
      }
      const report = (await invoke("skill_revision_check_effectiveness", {
        args: {
          revision_id: rev.revision_id,
          current_quality_score: current,
        },
      })) as EffectivenessReport;
      setEffectReport(report);
    } catch (e) {
      setError(`评估失败: ${e}`);
    } finally {
      setBusy(null);
    }
  }

  if (!data) {
    return (
      <div
        style={{
          background: "var(--catfish-bg-elevated)",
          border: "1px solid var(--catfish-border)",
          borderRadius: "var(--radius-md)",
          padding: "var(--space-4)",
          height: "100%",
          boxSizing: "border-box",
        }}
      >
        <h3 style={{ margin: 0, fontSize: "var(--text-md)" }}>🔧 Skill 改进提议</h3>
        <div style={{ fontSize: 12, color: "var(--catfish-text-muted)", marginTop: 8 }}>
          {error ?? "加载中..."}
        </div>
      </div>
    );
  }

  const totalAll = data.pending.length + data.effectiveness_due.length;
  const isEmpty = totalAll === 0 && data.total_proposed === 0;

  // BL-FIX21 (5/8): 空状态 collapsed 成 1 行 banner 全宽展示, 跟其他 card 不
  // 撞高度差. 鸿波反馈 'SKILL 改进提议半截很难看'.
  if (isEmpty) {
    return (
      <div
        style={{
          gridColumn: "1 / -1", // 全宽, 跨所有列
          background: "var(--catfish-bg)",
          border: "1px dashed var(--catfish-border)",
          borderRadius: "var(--radius-sm)",
          padding: "8px 14px",
          fontSize: 12,
          color: "var(--catfish-text-muted)",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <span style={{ fontSize: 14 }}>🔧</span>
        <span>
          <strong style={{ color: "var(--catfish-text)" }}>Skill 改进提议</strong> ·
          {/* P3.5.80 (7/28): 原文把 BL-MM11 / BL-MM12 两个内部编号摆给员工看,
              员工既不知道那是什么, 也查不到. 换成这些编号背后的实际含义. */}
          鲶鱼根据你的反馈、执行失败记录和质量评分，发现可改的技能会自动提议。暂时没有待处理的。
        </span>
      </div>
    );
  }

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        height: "100%",
        boxSizing: "border-box",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--space-3)" }}>
        <h3 style={{ margin: 0, fontSize: "var(--text-md)", fontWeight: 600 }}>
          🔧 Skill 改进提议
        </h3>
        <span style={{ fontSize: "var(--text-xs)", color: "var(--catfish-text-muted)" }}>
          {totalAll === 0
            ? "无待处理"
            : `${data.pending.length} 待采纳 / ${data.effectiveness_due.length} 待评估`}
        </span>
      </div>

      {error && (
        <div
          style={{
            fontSize: 11,
            color: "var(--status-error, #dc2626)",
            background: "rgba(220, 38, 38, 0.06)",
            padding: "4px 8px",
            borderRadius: 4,
            marginBottom: 8,
          }}
        >
          {error}
        </div>
      )}

      {effectReport && (
        <div
          style={{
            background: effectReport.verdict === "improved"
              ? "rgba(16, 185, 129, 0.06)"
              : effectReport.verdict === "regressed"
              ? "rgba(220, 38, 38, 0.08)"
              : "rgba(245, 158, 11, 0.08)",
            border: `1px solid ${
              effectReport.verdict === "improved"
                ? "#10b981"
                : effectReport.verdict === "regressed"
                ? "#dc2626"
                : "#f59e0b"
            }`,
            borderRadius: 4,
            padding: 8,
            fontSize: 12,
            marginBottom: 8,
          }}
        >
          <div style={{ fontWeight: 600 }}>
            {effectReport.skill_path} (Δ {effectReport.delta >= 0 ? "+" : ""}{effectReport.delta.toFixed(1)})
          </div>
          <div style={{ marginTop: 4, color: "var(--catfish-text-muted)" }}>
            {effectReport.recommendation}
          </div>
          <button
            type="button"
            onClick={() => setEffectReport(null)}
            style={{
              marginTop: 6,
              fontSize: 11,
              padding: "2px 8px",
              background: "transparent",
              border: "1px solid var(--catfish-border)",
              borderRadius: 3,
              cursor: "pointer",
            }}
          >
            知道了
          </button>
        </div>
      )}

      {/* BL-FIX21 (5/8): 全空状态走顶部 isEmpty 早返, 这里不再渲染 */}

      {/* 待采纳 pending */}
      {data.pending.length > 0 && (
        <div style={{ marginBottom: "var(--space-3)" }}>
          <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginBottom: 4 }}>
            待采纳 ({data.pending.length})
          </div>
          {data.pending.map((rev) => (
            <RevisionCardItem
              key={rev.revision_id}
              rev={rev}
              busy={busy === rev.revision_id}
              onAccept={() => handleAccept(rev)}
              onReject={() => handleReject(rev)}
            />
          ))}
        </div>
      )}

      {/* 待评估 effectiveness_due */}
      {data.effectiveness_due.length > 0 && (
        <div style={{ marginBottom: "var(--space-3)" }}>
          <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginBottom: 4 }}>
            待评估（采纳满 14 天，看看改得好不好）
          </div>
          {data.effectiveness_due.map((rev) => (
            <div
              key={rev.revision_id}
              style={{
                padding: "var(--space-2) var(--space-3)",
                border: "1px solid var(--catfish-border)",
                borderRadius: "var(--radius-sm)",
                fontSize: 12,
                marginBottom: 4,
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <span>
                {rev.skill_path} v{rev.proposed_version} (采纳 {rev.accepted_days_ago} 天前)
              </span>
              <button
                type="button"
                disabled={busy === rev.revision_id}
                onClick={() => handleCheckEffectiveness(rev)}
                style={{
                  fontSize: 11,
                  padding: "2px 8px",
                  background: "var(--catfish-cyan)",
                  color: "white",
                  border: "none",
                  borderRadius: 3,
                  cursor: busy === rev.revision_id ? "wait" : "pointer",
                }}
              >
                评估有效性
              </button>
            </div>
          ))}
        </div>
      )}

      {/* 历史 recent_resolved */}
      {data.recent_resolved.length > 0 && (
        <details style={{ marginTop: "auto", fontSize: 11, color: "var(--catfish-text-muted)" }}>
          <summary style={{ cursor: "pointer" }}>
            历史 ({data.total_accepted} 已采纳 / {data.total_rejected} 已拒绝)
          </summary>
          <div style={{ marginTop: 4 }}>
            {data.recent_resolved.slice(0, 10).map((rev) => (
              <div key={rev.revision_id} style={{ marginBottom: 2 }}>
                {rev.status === "accepted" ? "✅" : "❌"} {rev.skill_path} v
                {rev.current_version} → v{rev.proposed_version} · {formatTsAgo(rev.proposed_ts)}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function RevisionCardItem({
  rev,
  busy,
  onAccept,
  onReject,
}: {
  rev: RevisionView;
  busy: boolean;
  onAccept: () => void;
  onReject: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div
      style={{
        padding: "var(--space-2) var(--space-3)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-sm)",
        fontSize: 12,
        marginBottom: 4,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span style={{ fontWeight: 600 }}>
          {rev.skill_path}{" "}
          <code style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
            v{rev.current_version} → v{rev.proposed_version}
          </code>
        </span>
        <span style={{ fontSize: 10, color: "var(--catfish-text-muted)" }}>
          {formatTsAgo(rev.proposed_ts)}
        </span>
      </div>

      {expanded && (
        <div style={{ marginTop: 6, fontSize: 11, color: "var(--catfish-text-muted)" }}>
          <div>
            <strong>理由</strong>: {rev.reason}
          </div>
          <div style={{ marginTop: 4 }}>
            <strong>改动</strong>:{" "}
            <pre style={{ whiteSpace: "pre-wrap", fontSize: 10, margin: 0, fontFamily: "inherit" }}>
              {rev.diff_summary}
            </pre>
          </div>
          <div style={{ marginTop: 4 }}>
            <strong>数据依据</strong>: {rev.evidence_summary}
          </div>
        </div>
      )}

      <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          style={{
            fontSize: 11,
            padding: "2px 8px",
            background: "transparent",
            border: "1px solid var(--catfish-border)",
            borderRadius: 3,
            cursor: "pointer",
          }}
        >
          {expanded ? "收起" : "看 diff"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onAccept}
          style={{
            fontSize: 11,
            padding: "2px 8px",
            background: "#10b981",
            color: "white",
            border: "none",
            borderRadius: 3,
            cursor: busy ? "wait" : "pointer",
          }}
        >
          ✅ 采纳
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onReject}
          style={{
            fontSize: 11,
            padding: "2px 8px",
            background: "transparent",
            color: "var(--catfish-text-muted)",
            border: "1px solid var(--catfish-border)",
            borderRadius: 3,
            cursor: busy ? "wait" : "pointer",
          }}
        >
          ❌ 拒绝
        </button>
      </div>
    </div>
  );
}
