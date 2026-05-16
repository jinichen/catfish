/** Dashboard 卡 — "鲶鱼对你的画像" (BL-MM7, 5/6 ship)
 *
 * 跟 RelationCard / MemoryHistoryCard 配对的隐私透明卡:
 *   - RelationCard: 鲶鱼总结的对话主题 (像日记)
 *   - MemoryHistoryCard: 鲶鱼记的硬事实 (像便签贴)
 *   - 本卡: 鲶鱼累积的**长期画像** (writing_style / work_pattern / personality)
 *          满 3 次 evidence 才 propose, 员工 confirm 后落盘
 *
 * 设计立场:
 *   - 列每个字段 + 当前值 + evidence_count + 锁/未锁 + 最后确认时间
 *   - 单字段 "锁定" / "解锁" / "改一下" / "删掉这条"
 *   - 全部 "清空" — 隐私逃生口
 *   - 调 catfish_user_profile_* 工具 (走 tool-bridge)
 */

import { useEffect, useState, useCallback } from "react";

import { toolBridgeCallTool } from "../../lib/tauri";
import { useAgentStore } from "../../store/agent";

interface FieldSummary {
  value: string | null;
  evidence_count: number;
  locked: boolean;
  last_confirmed: number | null;
  proposed_value: string | null;
}

type ProfileMap = Record<string, FieldSummary>;

const REFRESH_MS = 30_000;

function humanTime(ts: number | null): string {
  if (!ts || ts <= 0) return "未确认";
  const now = Date.now() / 1000;
  const diff = now - ts;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 2) return "昨天";
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${(d.getMonth() + 1)
    .toString()
    .padStart(2, "0")}-${d.getDate().toString().padStart(2, "0")}`;
}

/** 把 dotted name "writing_style.tone" 翻成员工友好"文风.语气". */
function prettyField(field: string): string {
  const map: Record<string, string> = {
    "writing_style.tone": "文风 · 语气",
    "writing_style.length_pref": "文风 · 偏好长度",
    "writing_style.bullet_pref": "文风 · 列表 vs 段落",
    "work_pattern.peak_hours": "工作 · 黄金时段",
    "work_pattern.task_pref": "工作 · 任务呈现",
    "work_pattern.review_pref": "工作 · 看材料偏好",
    "personality.pace": "性格 · 节奏",
    "personality.feedback_style": "性格 · 反馈风格",
    "personality.deference": "性格 · 称呼正式度",
  };
  return map[field] || field;
}

export default function UserProfileCard() {
  const agentName = useAgentStore((s) => s.name);
  const [profile, setProfile] = useState<ProfileMap>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // 哪个字段在操作
  const [confirmingClearAll, setConfirmingClearAll] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const r = await toolBridgeCallTool("catfish_user_profile_get", {});
      if (!r.ok) {
        setError(r.error || "调用 catfish_user_profile_get 失败");
        return;
      }
      // result 嵌套: { type: 'result', result: ProfileMap }
      const inner = (r.result as { type: string; result: ProfileMap }) || {};
      const data = inner.result || {};
      setProfile(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const t = window.setInterval(() => void refresh(), REFRESH_MS);
    return () => window.clearInterval(t);
  }, [refresh]);

  const toggleLock = async (field: string, currentLocked: boolean) => {
    const cur = profile[field];
    if (!cur || cur.value == null) return;
    setBusy(field);
    try {
      await toolBridgeCallTool("catfish_user_profile_confirm", {
        field,
        value: cur.value,
        locked: !currentLocked,
      });
      await refresh();
    } finally {
      setBusy(null);
    }
  };

  const clearField = async (field: string) => {
    setBusy(field);
    try {
      await toolBridgeCallTool("catfish_user_profile_clear", { field });
      await refresh();
    } finally {
      setBusy(null);
    }
  };

  const clearAll = async () => {
    setBusy("__all");
    try {
      await toolBridgeCallTool("catfish_user_profile_clear", {});
      setConfirmingClearAll(false);
      await refresh();
    } finally {
      setBusy(null);
    }
  };

  const fields = Object.keys(profile);
  // 排序: confirmed 在前, proposed 中间, 空在后. 同档按 evidence_count 倒序.
  fields.sort((a, b) => {
    const fa = profile[a];
    const fb = profile[b];
    const sa = fa.value ? 0 : fa.proposed_value ? 1 : 2;
    const sb = fb.value ? 0 : fb.proposed_value ? 1 : 2;
    if (sa !== sb) return sa - sb;
    return fb.evidence_count - fa.evidence_count;
  });

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        // BL-USER-PROFILE-LIMIT-HEIGHT (5/16): 跟 RelationCard 视觉对称.
        // alignSelf:start 不被 grid row stretch, fields 内部滚动看更多.
        alignSelf: "start",
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-2)", marginBottom: "var(--space-2)" }}>
        <h3 style={{ margin: 0 }}>👤 {agentName}对你的画像</h3>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          {fields.length} 项 · 跨 session 长期画像 · 你随时改 / 锁 / 清
        </span>
      </div>

      <div style={{ fontSize: 12, color: "var(--catfish-text-muted)", marginBottom: "var(--space-3)" }}>
        累积 3 次 evidence 后{agentName}会跟你确认才记。锁定 = 它以后不再 propose 修改。
      </div>

      {error && (
        <div style={{ fontSize: 12, color: "var(--status-err)", marginBottom: "var(--space-2)" }}>
          {error}
        </div>
      )}

      {fields.length === 0 && !error && (
        <div style={{ fontSize: 13, color: "var(--catfish-text-muted)", padding: "var(--space-2) 0" }}>
          还没记过你的画像。多聊几次, {agentName}会主动观察 + 跟你确认。
        </div>
      )}

      {/* BL-USER-PROFILE-LIMIT-HEIGHT (5/16): fields 列表 maxHeight + 滚动, 跟
          RelationCard entries 区视觉对称, 不让卡无限撑高. */}
      <div
        style={{
          maxHeight: 360,
          overflowY: "auto",
          paddingRight: 4,
        }}
      >
      {fields.map((field) => {
        const f = profile[field];
        const display = f.value || f.proposed_value || "(空)";
        const isProposed = !f.value && !!f.proposed_value;
        return (
          <div
            key={field}
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-2)",
              padding: "6px 0",
              borderTop: "1px dashed var(--catfish-border)",
              fontSize: 13,
            }}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
                <span style={{ fontWeight: 500 }}>{prettyField(field)}</span>
                {f.locked && (
                  <span style={{ fontSize: 10, color: "var(--catfish-cyan)", border: "1px solid var(--catfish-cyan)", borderRadius: 3, padding: "0 4px" }}>
                    🔒 锁定
                  </span>
                )}
                {isProposed && (
                  <span style={{ fontSize: 10, color: "var(--status-warn)", border: "1px solid var(--status-warn)", borderRadius: 3, padding: "0 4px" }}>
                    待确认
                  </span>
                )}
              </div>
              <div style={{ marginTop: 2, color: isProposed ? "var(--catfish-text-muted)" : "var(--catfish-text)" }}>
                {display}
              </div>
              <div style={{ marginTop: 2, fontSize: 11, color: "var(--catfish-text-muted)" }}>
                {f.evidence_count} 次 evidence · {humanTime(f.last_confirmed)}
              </div>
            </div>
            <button
              onClick={() => void toggleLock(field, f.locked)}
              disabled={busy === field || !f.value}
              title={f.locked ? "解锁后允许 LLM propose 修改" : "锁定后 LLM 不再 propose"}
              style={{
                fontSize: 11, padding: "2px 6px", background: "transparent",
                border: "1px solid var(--catfish-border)", borderRadius: 3, cursor: "pointer",
                color: "var(--catfish-text-muted)",
              }}
            >
              {f.locked ? "解锁" : "锁定"}
            </button>
            <button
              onClick={() => void clearField(field)}
              disabled={busy === field}
              style={{
                fontSize: 11, padding: "2px 6px", background: "transparent",
                border: "1px solid var(--catfish-border)", borderRadius: 3, cursor: "pointer",
                color: "var(--catfish-text-muted)",
              }}
            >
              删掉
            </button>
          </div>
        );
      })}
      </div>{/* BL-USER-PROFILE-LIMIT-HEIGHT: 滚动 wrapper 闭合 */}

      {fields.length > 0 && (
        <div style={{ marginTop: "var(--space-3)", display: "flex", justifyContent: "flex-end" }}>
          {!confirmingClearAll ? (
            <button
              onClick={() => setConfirmingClearAll(true)}
              style={{
                fontSize: 11, padding: "4px 8px", background: "transparent",
                border: "1px solid var(--catfish-border)", borderRadius: 3, cursor: "pointer",
                color: "var(--catfish-text-muted)",
              }}
            >
              清空全部画像
            </button>
          ) : (
            <div style={{ display: "flex", gap: 8, fontSize: 12 }}>
              <span style={{ color: "var(--status-warn)" }}>确认清空全部?</span>
              <button onClick={() => void clearAll()} disabled={busy === "__all"}
                style={{ background: "var(--status-err)", color: "white", border: "none", borderRadius: 3, padding: "2px 8px", cursor: "pointer" }}>
                清
              </button>
              <button onClick={() => setConfirmingClearAll(false)}
                style={{ background: "transparent", border: "1px solid var(--catfish-border)", borderRadius: 3, padding: "2px 8px", cursor: "pointer" }}>
                取消
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
