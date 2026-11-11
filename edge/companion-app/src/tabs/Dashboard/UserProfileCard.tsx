/** 「小鲶对你的画像」—— 9 项长期偏好 (语气 / 长短 / 列表还是段落 / 节奏 ...)。
 *
 * 11/12 鸿波「画像为什么一直在变」「现在的画像功能合理吗」→ 按 3 条改:
 *   1. 确认过的值每轮自动带进对话 (catfish-memory 插件 catfish_memory_profile.py), 这页就是那份东西;
 *   2. 证据只认两种: 你在对话里明确说的 (小鲶记成「待确认」), 你在这页自己改的 (直接生效)。
 *      原来拿关键词套工作日志、每次重扫, 「正在观察」在几个值之间来回跳 —— 那条路停了;
 *   3. 这页只显示当前值 + 待你确认的提议, 不再显示「x/3 次 evidence」。
 *
 * 写都走 tool-bridge catfish_user_profile_* (confirm = 你改 / 你确认; clear proposal_only = 「不用」)。
 */
import { useCallback, useEffect, useState } from "react";

import { toolBridgeCallTool } from "../../lib/tauri";
import { useAgentStore } from "../../store/agent";

interface FieldSummary {
  value: string | null;
  /** null = 自由文本 */
  options: string[] | null;
  locked: boolean;
  last_confirmed: number | null;
  proposed_value: string | null;
  /** 提议的依据: 你说过的原话 */
  proposed_quote: string | null;
}
type ProfileMap = Record<string, FieldSummary>;

const REFRESH_MS = 30_000;

const FIELD_LABEL: Record<string, string> = {
  "writing_style.tone": "语气",
  "writing_style.length_pref": "回答长短",
  "writing_style.bullet_pref": "列表还是段落",
  "work_pattern.task_pref": "任务怎么呈现",
  "work_pattern.review_pref": "看材料的习惯",
  "work_pattern.peak_hours": "工作黄金时段",
  "personality.pace": "节奏",
  "personality.feedback_style": "反馈方式",
  "personality.deference": "称呼和口吻",
};
const ORDER = Object.keys(FIELD_LABEL);
const VALUE_LABEL: Record<string, string> = { formal: "正式", casual: "随意" };
const show = (v: string) => VALUE_LABEL[v] ?? v;

const smallBtn = (color = "var(--catfish-text-muted)", border = "var(--catfish-border)") => ({
  fontSize: 11, padding: "2px 8px", background: "transparent", border: `1px solid ${border}`,
  borderRadius: 3, cursor: "pointer", color, whiteSpace: "nowrap" as const,
});
const selectStyle = {
  fontSize: 13, padding: "3px 6px", borderRadius: 4, border: "1px solid var(--catfish-border)",
  background: "var(--catfish-bg)", color: "var(--catfish-text)", minWidth: 120,
};

async function call(name: string, args: Record<string, unknown>) {
  const r = await toolBridgeCallTool(name, args);
  if (!r.ok) throw new Error(r.error || `${name} 失败`);
  const out = r.result as { type: string; result?: unknown; error?: string };
  if (out?.type === "error") throw new Error(out.error || `${name} 失败`);
  return out;
}

export default function UserProfileCard() {
  const agentName = useAgentStore((s) => s.name);
  const [profile, setProfile] = useState<ProfileMap>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmingClearAll, setConfirmingClearAll] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const refresh = useCallback(async () => {
    try {
      const r = await call("catfish_user_profile_get", {});
      setProfile((r.result as ProfileMap) || {});
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

  async function run(field: string, fn: () => Promise<unknown>) {
    setBusy(field);
    try {
      await fn();
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      await refresh();
      setBusy(null);
    }
  }
  /** 你自己改 = 直接生效; 改成「还没定」= 清掉这项 */
  const setValue = (field: string, value: string) =>
    run(field, () => (value
      ? call("catfish_user_profile_confirm", { field, value, locked: profile[field]?.locked ?? false })
      : call("catfish_user_profile_clear", { field })));
  const accept = (field: string) => {
    const f = profile[field];
    if (f?.proposed_value) void run(field, () => call("catfish_user_profile_confirm", { field, value: f.proposed_value, locked: f.locked }));
  };
  const dismiss = (field: string) => void run(field, () => call("catfish_user_profile_clear", { field, proposal_only: true }));
  const toggleLock = (field: string) => {
    const f = profile[field];
    if (f?.value) void run(field, () => call("catfish_user_profile_confirm", { field, value: f.value, locked: !f.locked }));
  };
  async function clearAll() {
    await run("__all", () => call("catfish_user_profile_clear", {}));
    setConfirmingClearAll(false);
  }

  const fields = [...ORDER.filter((k) => k in profile), ...Object.keys(profile).filter((k) => !ORDER.includes(k))];
  const pending = fields.filter((k) => profile[k].proposed_value && profile[k].proposed_value !== profile[k].value).length;
  const anySet = fields.some((k) => profile[k].value);

  return (
    <div style={{
      background: "var(--catfish-bg-elevated)", border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-md)",
      padding: "var(--space-4)", height: "min(480px, 50vh)", boxSizing: "border-box", display: "flex", flexDirection: "column",
    }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: "var(--space-2)", marginBottom: "var(--space-2)" }}>
        <h3 style={{ margin: 0 }}>👤 {agentName}对你的画像</h3>
        {pending > 0 && <span style={{ fontSize: 12, color: "var(--status-warn)" }}>{pending} 项等你确认</span>}
      </div>
      <div style={{ fontSize: 12, color: "var(--catfish-text-muted)", marginBottom: "var(--space-3)", lineHeight: 1.6 }}>
        这里定下的偏好, {agentName}每次回答都会照着做。你在对话里说「以后回答短一点」这类话, {agentName}会记成待确认, 你点确认才算数;
        也可以直接在这里改。锁定 = {agentName}不再提议改这一项。
      </div>

      {error && <div style={{ fontSize: 12, color: "var(--status-err)", marginBottom: "var(--space-2)" }}>{error}</div>}

      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", paddingRight: 4 }}>
        {fields.map((field) => {
          const f = profile[field];
          const proposal = f.proposed_value && f.proposed_value !== f.value ? f.proposed_value : null;
          const isBusy = busy === field;
          const draft = drafts[field] ?? f.value ?? "";
          return (
            <div key={field} style={{ padding: "8px 0", borderTop: "1px dashed var(--catfish-border)", fontSize: 13 }}>
              <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
                <span style={{ fontWeight: 500, width: 104, flexShrink: 0 }}>{FIELD_LABEL[field] ?? field}</span>
                {f.options ? (
                  <select aria-label={FIELD_LABEL[field] ?? field} value={f.value ?? ""} disabled={isBusy}
                          onChange={(e) => void setValue(field, e.target.value)} style={selectStyle}>
                    <option value="">还没定</option>
                    {f.options.map((o) => <option key={o} value={o}>{show(o)}</option>)}
                  </select>
                ) : (
                  <input aria-label={FIELD_LABEL[field] ?? field} value={draft} disabled={isBusy} placeholder="还没定, 例 9:00-12:00"
                         onChange={(e) => setDrafts((d) => ({ ...d, [field]: e.target.value }))}
                         onBlur={() => { if (draft.trim() !== (f.value ?? "")) void setValue(field, draft.trim()); setDrafts((d) => { const n = { ...d }; delete n[field]; return n; }); }}
                         onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }}
                         style={{ ...selectStyle, minWidth: 160 }} />
                )}
                <span style={{ flex: 1 }} />
                {f.value && (
                  <button type="button" onClick={() => toggleLock(field)} disabled={isBusy}
                          title={f.locked ? `解锁后${agentName}可以提议改这一项` : `锁定后${agentName}不再提议改这一项`}
                          style={smallBtn(f.locked ? "var(--catfish-cyan)" : undefined, f.locked ? "var(--catfish-cyan)" : undefined)}>
                    {f.locked ? "🔒 已锁定" : "锁定"}
                  </button>
                )}
              </div>
              {proposal && (
                <div style={{ marginTop: 6, marginLeft: 112, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap",
                              color: "var(--status-warn)", fontSize: 12 }}>
                  <span style={{ minWidth: 0 }}>
                    {f.value ? "想改成" : "想记成"}「{show(proposal)}」
                    {f.proposed_quote && <span style={{ color: "var(--catfish-text-muted)" }}> · 你说过: “{f.proposed_quote}”</span>}
                  </span>
                  <button type="button" onClick={() => accept(field)} disabled={isBusy} style={smallBtn("var(--status-warn)", "var(--status-warn)")}>确认</button>
                  <button type="button" onClick={() => dismiss(field)} disabled={isBusy} style={smallBtn()}>不用</button>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {anySet && (
        <div style={{ marginTop: "var(--space-3)", display: "flex", justifyContent: "flex-end" }}>
          {!confirmingClearAll ? (
            <button type="button" onClick={() => setConfirmingClearAll(true)} style={smallBtn()}>清空全部画像</button>
          ) : (
            <div style={{ display: "flex", gap: 8, fontSize: 12, alignItems: "center" }}>
              <span style={{ color: "var(--status-warn)" }}>确认清空全部?</span>
              <button type="button" onClick={() => void clearAll()} disabled={busy === "__all"}
                      style={{ background: "var(--status-err)", color: "white", border: "none", borderRadius: 3, padding: "2px 8px", cursor: "pointer" }}>清空</button>
              <button type="button" onClick={() => setConfirmingClearAll(false)} style={smallBtn()}>取消</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
