/** ChatTab 顶部 task picker (P3.3.19 C Phase 3, 6/11).
 *
 * 拉 advisor cache mainTasks. 员工选了 task →
 *   1. sessionGetByTaskUid(taskUid) — 找 task 关联的 latest session
 *   2. 有: onSelect(sessionId) — 让 ChatTab.handleSelect 切走加载
 *   3. 无: sessionCreate({model, title, systemPrompt: buildTaskSystemPrompt(task)})
 *      + sessionSetTaskUid → onSelect(新 sessionId)
 *
 * 实现细节:
 *   - dropdown 用 native <select>, 不引 UI lib
 *   - 显急/中/低 prefix + task title
 *   - "+ 普通新对话" option = onSelect(null) 让 ChatTab.handleNew
 */

import { useEffect, useState } from "react";
import {
  sessionGetByTaskUid,
  sessionSetTaskUid,
  sessionCreate,
} from "../../lib/tauri";
import { advisorCacheGet } from "../../lib/advisor_cache";
import { buildTaskSystemPrompt } from "../../lib/taskSystemPrompt";
import type { MainTask } from "../../lib/briefing_advisor";

interface TaskPickerProps {
  /** 当前已选 task uid (null = 普通对话). 用于 dropdown current value. */
  currentTaskUid: string | null;
  /** 选了 task → resolved sessionId (新建 / 复用); 选"普通对话" → null */
  onSelectSession: (sessionId: string | null) => void;
  /** chat picker 当前 model (sessionCreate 要用) */
  model: string;
  /** 流中 disable, 防员工切走撞 */
  disabled?: boolean;
}

const URGENCY_LABEL: Record<string, string> = {
  high: "急",
  medium: "中",
  low: "低",
};

export default function TaskPicker({
  currentTaskUid,
  onSelectSession,
  model,
  disabled = false,
}: TaskPickerProps) {
  const [tasks, setTasks] = useState<MainTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // mount + 60s 周期拉 advisor cache (跟 BriefingTab 同步)
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const cached = await advisorCacheGet();
        if (cancelled) return;
        const main = cached?.result?.mainTasks ?? [];
        // 只显有 taskUid 的 (P3.3.9 之前老 cache 没 uid 跳过)
        const filtered = main.filter(
          (t) => typeof t.taskUid === "string" && t.taskUid.length > 0,
        );
        setTasks(filtered);
        setError(null);
      } catch (e) {
        if (!cancelled) {
          console.warn("[TaskPicker] advisorCacheGet 失败:", e);
          setError(String(e));
        }
      }
    };
    void load();
    const tid = window.setInterval(() => void load(), 60_000);
    return () => {
      cancelled = true;
      window.clearInterval(tid);
    };
  }, []);

  const handleChange = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const taskUid = e.target.value;
    if (taskUid === "") {
      // "普通对话" — 不切到任何 task session, 让 ChatTab handleNew
      onSelectSession(null);
      return;
    }
    const task = tasks.find((t) => t.taskUid === taskUid);
    if (!task) return;

    setLoading(true);
    setError(null);
    try {
      // 找 task 关联的 latest session
      let sid = await sessionGetByTaskUid(task.taskUid);
      if (!sid) {
        // 新建 + 关联
        const created = await sessionCreate({
          model,
          title: task.title,
          systemPrompt: buildTaskSystemPrompt(task),
        });
        sid = created.id;
        await sessionSetTaskUid(sid, task.taskUid);
      }
      onSelectSession(sid);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  if (tasks.length === 0 && !error) {
    // advisor 还没出主菜 — 不显 picker (workplace chat 普通用)
    return null;
  }

  return (
    /* P3.3.22 (6/11): flex-shrink: 0 + nowrap — 防工作台 header 一行挤撞时
       这块自己内部换行 ("🎯 task:" 跟 select 之间撕开). */
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontSize: 12,
        flexShrink: 0,
        whiteSpace: "nowrap",
      }}
    >
      <span
        style={{
          color: "var(--catfish-text-muted)",
          flexShrink: 0,
          whiteSpace: "nowrap",
        }}
        title="进任一早安主菜的 task 上下文 — 跟早安 DetailPane 共享同一 session"
      >
        🎯 task:
      </span>
      <select
        value={currentTaskUid ?? ""}
        onChange={(e) => void handleChange(e)}
        disabled={disabled || loading}
        style={{
          fontSize: 12,
          padding: "3px 6px",
          background: currentTaskUid ? "rgba(74,158,255,0.1)" : "var(--catfish-bg)",
          color: "var(--catfish-text)",
          border: "1px solid var(--catfish-border)",
          borderRadius: 4,
          cursor: disabled || loading ? "wait" : "pointer",
          // P3.3.22 (6/11): 240 → 180, 配合 option title 18 字截短让 dropdown 不挤
          maxWidth: 180,
        }}
      >
        <option value="">— 普通对话 —</option>
        {tasks.map((t) => {
          const u = URGENCY_LABEL[t.urgency] ?? "?";
          // P3.3.22 (6/11): 22 → 16 字, 配合 select maxWidth 180 不挤
          const title =
            t.title.length > 16 ? t.title.slice(0, 16) + "…" : t.title;
          return (
            <option key={t.taskUid} value={t.taskUid} title={t.title}>
              [{u}] {title}
            </option>
          );
        })}
      </select>
      {loading && <span style={{ color: "var(--catfish-text-muted)" }}>切换中…</span>}
      {error && (
        <span
          style={{ color: "#dc2626", fontSize: 11 }}
          title={error}
        >
          ⚠ 错
        </span>
      )}
    </div>
  );
}
