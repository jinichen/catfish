/** BL-BRIEFING-GOAL-INPUT (5/20 鸿波): BriefingCard "🎯 今日重点" 输入框.
 *
 * 写 ~/.catfish/session_goal.txt — gateway 端 inject_session_goal (228 LOC) 仍读
 * 同一文件, chat 链路自动 inject 进 system 末尾让 LLM 锚定. CLI `/goal xxx` 仍
 * 工作, 鸿波想用 UI / CLI 随便选.
 *
 * 三态:
 *   - 未设 goal: 显输入框 + "锁定" 按钮
 *   - 已设 goal: 显 "🎯 锁定: <text>" + "改" / "清除" 按钮
 *   - 编辑中: 输入框预填当前 goal + "保存" / "取消"
 */

import { useEffect, useState } from "react";

import { sessionGoalClear, sessionGoalRead, sessionGoalWrite } from "../../../lib/tauri";

const MAX_GOAL_LEN = 500;

export default function GoalInput() {
  const [goal, setGoal] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 启动加载
  useEffect(() => {
    void (async () => {
      try {
        const current = await sessionGoalRead();
        setGoal(current);
        setDraft(current ?? "");
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    })();
  }, []);

  const startEdit = () => {
    setDraft(goal ?? "");
    setEditing(true);
    setError(null);
  };

  const handleSave = async () => {
    const trimmed = draft.trim();
    if (!trimmed) {
      setError("目标不能为空 (要清除请按下面按钮)");
      return;
    }
    if (trimmed.length > MAX_GOAL_LEN) {
      setError(`目标不能超 ${MAX_GOAL_LEN} 字 (当前 ${trimmed.length})`);
      return;
    }
    setBusy(true);
    try {
      await sessionGoalWrite(trimmed);
      setGoal(trimmed);
      setEditing(false);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleClear = async () => {
    setBusy(true);
    try {
      await sessionGoalClear();
      setGoal(null);
      setDraft("");
      setEditing(false);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  // ── 已锁定状态 ─────────────────────────────────────
  if (goal && !editing) {
    return (
      <div
        style={{
          marginTop: "var(--space-2)",
          marginBottom: "var(--space-3)",
          padding: "8px 12px",
          background: "var(--catfish-bg)",
          border: "1px solid var(--catfish-cyan-dim)",
          borderRadius: "var(--radius-sm)",
          fontSize: 12,
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <span style={{ fontSize: 14 }}>🎯</span>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)", minWidth: 56 }}>
          今日锁定
        </span>
        <span
          style={{
            flex: 1,
            color: "var(--catfish-text)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={goal}
        >
          {goal}
        </span>
        <button
          type="button"
          onClick={startEdit}
          disabled={busy}
          style={btnStyle(false)}
        >
          改
        </button>
        <button
          type="button"
          onClick={() => void handleClear()}
          disabled={busy}
          style={btnStyle(true)}
          title="清除 goal (回到无锁定状态)"
        >
          清
        </button>
      </div>
    );
  }

  // ── 编辑 / 新设状态 ───────────────────────────────
  return (
    <div
      style={{
        marginTop: "var(--space-2)",
        marginBottom: "var(--space-3)",
        padding: "8px 12px",
        background: "var(--catfish-bg)",
        border: "1px dashed var(--catfish-border)",
        borderRadius: "var(--radius-sm)",
        fontSize: 12,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <span style={{ fontSize: 14 }}>🎯</span>
        <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          今日重点
        </span>
        <span style={{ fontSize: 10, color: "var(--catfish-text-muted)", opacity: 0.6 }}>
          (锁定后 LLM 每轮 inject 防跑偏 · 等同 chat 里 /goal xxx)
        </span>
      </div>
      <div style={{ display: "flex", gap: 6, alignItems: "stretch" }}>
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !busy && draft.trim()) {
              void handleSave();
            }
            if (e.key === "Escape" && editing) {
              setEditing(false);
              setDraft(goal ?? "");
              setError(null);
            }
          }}
          placeholder="例: 写完季度汇报 / 拿下张三那单"
          disabled={busy}
          maxLength={MAX_GOAL_LEN + 50}  // 给点 buffer 让用户看到 > 500 报错
          style={{
            flex: 1,
            padding: "4px 8px",
            fontSize: 12,
            background: "var(--catfish-bg-elevated)",
            border: "1px solid var(--catfish-border)",
            borderRadius: 3,
            color: "var(--catfish-text)",
            fontFamily: "inherit",
            outline: "none",
          }}
        />
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={busy || !draft.trim()}
          style={{
            ...btnStyle(false),
            background: draft.trim() ? "var(--catfish-cyan)" : undefined,
            color: draft.trim() ? "#fff" : undefined,
            padding: "4px 12px",
          }}
        >
          {busy ? "…" : editing ? "保存" : "锁定"}
        </button>
        {editing && (
          <button
            type="button"
            onClick={() => {
              setEditing(false);
              setDraft(goal ?? "");
              setError(null);
            }}
            disabled={busy}
            style={btnStyle(false)}
          >
            取消
          </button>
        )}
      </div>
      {error && (
        <div
          style={{
            marginTop: 4,
            fontSize: 10,
            color: "#dc2626",
            padding: "2px 4px",
          }}
        >
          ⚠ {error}
        </div>
      )}
    </div>
  );
}

function btnStyle(danger: boolean): React.CSSProperties {
  return {
    background: "transparent",
    color: danger ? "#dc2626" : "var(--catfish-text-muted)",
    border: `1px solid ${danger ? "#fecaca" : "var(--catfish-border)"}`,
    borderRadius: 3,
    padding: "3px 10px",
    cursor: "pointer",
    fontSize: 11,
    fontFamily: "inherit",
  };
}
