/**
 * 工作台「找谁」+ 专家快捷问法 + 小鲶推荐的「切到 X」按钮 (10/18, docs/EXPERT-PACK-PLAN.md §3.3)。
 *
 * 换人 = 新开一个会话 (onSwitch 让 ChatTab 开新对话), 不把小鲶和专家的对话混在一起。
 * 推荐按钮只在员工点了之后才切 —— 小鲶不会替员工去专家那边办事。
 */
import { useEffect } from "react";

import { refreshExperts, selectExpert, useExpertChat } from "../../lib/expertChat";

export function ExpertPicker({ disabled, onSwitch }: { disabled?: boolean; onSwitch: () => void }) {
  const { experts, selected } = useExpertChat();
  useEffect(() => { void refreshExperts(); }, []);
  if (experts.length === 0 && !selected) return null;
  return (
    <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }} title="跟小鲶聊, 还是找某个专家">
      <span style={{ color: "var(--catfish-text-muted)" }}>找谁</span>
      <select
        value={selected ?? ""}
        disabled={disabled}
        aria-label="找谁"
        onChange={(e) => {
          selectExpert(e.target.value || null);
          onSwitch();
        }}
        style={{ fontSize: 12, padding: "3px 6px", borderRadius: "var(--radius-sm)", border: "1px solid var(--catfish-border)",
                 background: "var(--catfish-bg)", color: "var(--catfish-text)" }}
      >
        <option value="">小鲶</option>
        {experts.map((e) => (
          <option key={e.profileId} value={e.profileId} disabled={!e.ready}>
            {e.name}{e.ready ? "" : " (还没准备好)"}
          </option>
        ))}
      </select>
    </label>
  );
}

/** 跟专家的新会话里, 先给几句它擅长的问法, 点一下就发。 */
export function ExpertQuickPrompts() {
  const { experts, selected } = useExpertChat();
  const e = experts.find((x) => x.profileId === selected);
  if (!e) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, alignItems: "center", padding: "24px 16px" }}>
      <div style={{ fontSize: 15, fontWeight: 600 }}>{e.name}</div>
      {e.description && <div style={{ fontSize: 12, color: "var(--catfish-text-muted)", maxWidth: 520, textAlign: "center" }}>{e.description}</div>}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "center", maxWidth: 640 }}>
        {e.quickPrompts.map((q) => (
          <button key={q} type="button"
                  onClick={() => window.dispatchEvent(new CustomEvent("catfish:approval-send", { detail: { text: q } }))}
                  style={{ fontSize: 12, padding: "6px 12px", borderRadius: 16, border: "1px solid var(--catfish-border)",
                           background: "transparent", color: "var(--catfish-text)", cursor: "pointer" }}>
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}

/** catfish_expert suggest 的结果: 画成「切到 X」按钮。点了才切, 而且是新开一个跟它的会话。 */
export function ExpertSuggestButton({ resultText }: { resultText: string }) {
  let s: { profile_id?: string; name?: string; reason?: string } | undefined;
  try {
    s = (JSON.parse(resultText) as { suggestion?: typeof s }).suggestion;
  } catch {
    return null;
  }
  if (!s?.profile_id || !s.name) return null;
  const profile = s.profile_id;
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12, padding: "6px 0" }}>
      <button type="button"
              onClick={() => {
                selectExpert(profile);
                window.dispatchEvent(new CustomEvent("catfish:new-chat"));
              }}
              style={{ padding: "4px 12px", fontSize: 12, borderRadius: "var(--radius-sm)", border: "none",
                       background: "var(--catfish-cyan)", color: "white", cursor: "pointer" }}>
        切到{s.name}
      </button>
      <span style={{ color: "var(--catfish-text-muted)" }}>{s.reason ? `${s.reason} · ` : ""}会新开一个跟它的对话</span>
    </div>
  );
}
