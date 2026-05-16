/** Dashboard 卡 — 改鲶鱼名字 + 人设 (BL-E11 五一 sprint 5/3 晚).
 *
 * Onboarding 走完后想改也能改这里. 跟 Onboarding StepName 共用 store + lib/agent.
 */

import { useState, useEffect } from "react";
import { getVersion } from "@tauri-apps/api/app";

import { PERSONALITY_LABELS, type Personality } from "../../lib/agent";
import { useAgentStore } from "../../store/agent";
import { petShow, petHide } from "../../lib/tauri";

export default function AgentPrefsCard() {
  const name = useAgentStore((s) => s.name);
  const personality = useAgentStore((s) => s.personality);
  const updateAgentPrefs = useAgentStore((s) => s.updateAgentPrefs);

  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState(name);
  const [draftPers, setDraftPers] = useState<Personality>(personality);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const startEdit = () => {
    setDraftName(name);
    setDraftPers(personality);
    setErr(null);
    setEditing(true);
  };

  const cancel = () => {
    setEditing(false);
    setErr(null);
  };

  const save = async () => {
    setSaving(true);
    setErr(null);
    try {
      await updateAgentPrefs(draftName.trim() || "小鲶", draftPers);
      setEditing(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

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
          justifyContent: "space-between",
          marginBottom: "var(--space-3)",
        }}
      >
        <h3 style={{ margin: 0, display: "inline-flex", alignItems: "center", gap: 8 }}>
          <img src="/catfish-avatar.svg" alt="" width={20} height={20} style={{ display: "block" }} />
          {/* BL-E11 后续: 卡标题用员工自定义名, 强化"是你的同事"参与感 */}
          {name}的名字 + 风格
        </h3>
        {!editing && (
          <button
            type="button"
            onClick={startEdit}
            style={{
              background: "transparent",
              border: "1px solid var(--catfish-border)",
              borderRadius: 4,
              fontSize: 11,
              padding: "3px 10px",
              color: "var(--catfish-text-muted)",
              cursor: "pointer",
            }}
          >
            改
          </button>
        )}
      </div>

      {!editing ? (
        <>
          <Row label="名字" value={name} />
          <Row label="风格" value={PERSONALITY_LABELS[personality]?.label ?? personality} />
          <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: 6, lineHeight: 1.5 }}>
            {PERSONALITY_LABELS[personality]?.desc}
          </div>
          {/* BL-E27 spike (5/5 凌晨): 桌宠开关. 临时放这里, 后续 BL-E27.1 ship 时
              移到 Onboarding consent toggle. */}
          <div
            style={{
              marginTop: "var(--space-3)",
              paddingTop: "var(--space-2)",
              borderTop: "1px dashed var(--catfish-border)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
                🐟 桌宠 (实验):
              </span>
              <button
                type="button"
                onClick={() => void petShow().catch((e) => alert("show 失败: " + e))}
                style={{
                  fontSize: 11,
                  padding: "3px 10px",
                  border: "1px solid var(--catfish-border)",
                  borderRadius: 4,
                  background: "var(--catfish-bg-cream)",
                  color: "var(--catfish-text)",
                  cursor: "pointer",
                }}
              >
                显示
              </button>
              <button
                type="button"
                onClick={() => void petHide().catch((e) => alert("hide 失败: " + e))}
                style={{
                  fontSize: 11,
                  padding: "3px 10px",
                  border: "1px solid var(--catfish-border)",
                  borderRadius: 4,
                  background: "transparent",
                  color: "var(--catfish-text-muted)",
                  cursor: "pointer",
                }}
              >
                隐藏
              </button>
              <span style={{ fontSize: 10, color: "var(--catfish-text-muted)", marginLeft: 4 }}>
                (开/关 ⌘⇧P)
              </span>
            </div>
            <div style={{ fontSize: 10, color: "var(--catfish-text-muted)", marginTop: 4, lineHeight: 1.5 }}>
              点鲶鱼唤主窗 · 空白处穿透到桌面 · ⌥⇧1/2/3/4 切 4 屏角
            </div>
          </div>
        </>
      ) : (
        <>
          <label style={{ display: "block", fontSize: 11, color: "var(--catfish-text-muted)", marginBottom: 4 }}>
            名字
          </label>
          <input
            type="text"
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            maxLength={32}
            placeholder="小鲶"
            style={{
              width: "100%",
              padding: "8px 10px",
              fontSize: 13,
              border: "1px solid var(--catfish-border)",
              borderRadius: "var(--radius-sm)",
              background: "var(--catfish-bg)",
              color: "var(--catfish-text)",
              boxSizing: "border-box",
              marginBottom: "var(--space-3)",
            }}
          />

          <label style={{ display: "block", fontSize: 11, color: "var(--catfish-text-muted)", marginBottom: 6 }}>
            说话风格
          </label>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {(Object.keys(PERSONALITY_LABELS) as Personality[]).map((p) => {
              const meta = PERSONALITY_LABELS[p];
              const selected = draftPers === p;
              return (
                <button
                  key={p}
                  type="button"
                  onClick={() => setDraftPers(p)}
                  style={{
                    textAlign: "left",
                    padding: "8px 10px",
                    border: `1.5px solid ${selected ? "var(--catfish-cyan)" : "var(--catfish-border)"}`,
                    borderRadius: "var(--radius-sm)",
                    background: selected ? "var(--catfish-bg-cream)" : "var(--catfish-bg)",
                    cursor: "pointer",
                    color: "var(--catfish-text)",
                    fontSize: 12,
                  }}
                >
                  <div style={{ fontWeight: 500 }}>{meta.label}</div>
                  <div style={{ color: "var(--catfish-text-muted)", marginTop: 2, lineHeight: 1.4 }}>
                    {meta.desc}
                  </div>
                </button>
              );
            })}
          </div>

          {err && (
            <div style={{ color: "var(--status-err)", fontSize: 11, marginTop: 6 }}>
              {err}
            </div>
          )}

          <div style={{ display: "flex", gap: 8, marginTop: "var(--space-3)" }}>
            <button
              type="button"
              onClick={save}
              disabled={saving}
              style={{
                flex: 1,
                padding: "7px 12px",
                background: "var(--catfish-cyan)",
                color: "white",
                border: "none",
                borderRadius: "var(--radius-sm)",
                fontSize: 12,
                cursor: saving ? "default" : "pointer",
                opacity: saving ? 0.6 : 1,
              }}
            >
              {saving ? "保存中…" : "保存"}
            </button>
            <button
              type="button"
              onClick={cancel}
              disabled={saving}
              style={{
                padding: "7px 12px",
                background: "transparent",
                color: "var(--catfish-text-muted)",
                border: "1px solid var(--catfish-border)",
                borderRadius: "var(--radius-sm)",
                fontSize: 12,
                cursor: saving ? "default" : "pointer",
              }}
            >
              取消
            </button>
          </div>
        </>
      )}
      {/* BL-IDENTITY-CARD-KILL (5/16): 砍 IdentityCard 后, 鲶鱼版本号挪这里 (售后报 bug 时用).
          其余诊断信息 (员工名 / SOUL 路径 / 活动会话 ID) 员工真需要时去"控制台" tab. */}
      <CatfishVersionFooter />
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        marginBottom: "var(--space-2)",
        fontSize: 13,
      }}
    >
      <span style={{ color: "var(--catfish-text-muted)" }}>{label}</span>
      <span style={{ color: "var(--catfish-text)" }}>{value}</span>
    </div>
  );
}

/** BL-IDENTITY-CARD-KILL (5/16): 卡底部小字版本号. 售后/客服场景报 bug 用. */
function CatfishVersionFooter() {
  const [version, setVersion] = useState<string>("…");
  useEffect(() => {
    getVersion()
      .then((v) => setVersion(`v${v}`))
      .catch(() => setVersion("(未知)"));
  }, []);
  return (
    <div
      style={{
        marginTop: "var(--space-3)",
        paddingTop: "var(--space-2)",
        borderTop: "1px dashed var(--catfish-border)",
        fontSize: 10,
        color: "var(--catfish-text-muted)",
        textAlign: "right",
      }}
    >
      鲶鱼 {version}
    </div>
  );
}
