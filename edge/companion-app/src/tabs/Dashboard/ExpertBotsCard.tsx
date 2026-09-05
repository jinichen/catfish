import { useEffect, useMemo, useState } from "react";

import { useCatalog } from "../../hooks/useCatalog";
import {
  expertBotBind,
  expertBotCreate,
  expertBotDelete,
  expertBotRegisterExisting,
  expertBotSoulGet,
  expertBotsList,
  expertBotsSetEnabled,
  expertBotUnregister,
  expertBotUpdate,
  type ExpertBotModelPolicy,
  type ExpertBotSummary,
  type ExpertBotsSnapshot,
} from "../../lib/tauri";

const fieldStyle = {
  border: "1px solid var(--catfish-border)",
  borderRadius: 5,
  background: "var(--catfish-bg)",
  color: "var(--catfish-text)",
  fontFamily: "inherit",
  fontSize: 12,
  padding: "6px 8px",
} as const;

const buttonStyle = {
  border: "1px solid var(--catfish-border)",
  borderRadius: 5,
  background: "var(--catfish-bg-cream)",
  color: "var(--catfish-text)",
  fontFamily: "inherit",
  fontSize: 11,
  padding: "5px 10px",
  cursor: "pointer",
} as const;

function errorText(value: unknown): string {
  return value instanceof Error ? value.message : String(value);
}

function ModelPolicyEditor({
  bot,
  models,
  busy,
  save,
}: {
  bot: ExpertBotSummary;
  models: Array<{ id: string; display_name: string }>;
  busy: boolean;
  save: (policy: ExpertBotModelPolicy) => Promise<unknown>;
}) {
  const [mode, setMode] = useState(bot.modelPolicy.mode);
  const [modelId, setModelId] = useState(bot.modelPolicy.model_id ?? "");
  useEffect(() => {
    setMode(bot.modelPolicy.mode);
    setModelId(bot.modelPolicy.model_id ?? "");
  }, [bot.modelPolicy]);

  return (
    <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
      <select value={mode} onChange={(e) => setMode(e.target.value as typeof mode)} style={fieldStyle}>
        <option value="inherit_picker">继承员工 Picker</option>
        <option value="fixed">固定模型</option>
      </select>
      {mode === "fixed" && (
        <select value={modelId} onChange={(e) => setModelId(e.target.value)} style={fieldStyle}>
          <option value="">选择模型…</option>
          {models.map((model) => (
            <option key={model.id} value={model.id}>{model.display_name}</option>
          ))}
        </select>
      )}
      <button
        type="button"
        disabled={busy || (mode === "fixed" && !modelId)}
        onClick={() => void save(mode === "fixed"
          ? { mode, model_id: modelId }
          : { mode: "inherit_picker" })}
        style={buttonStyle}
      >
        保存模型策略
      </button>
    </div>
  );
}

export default function ExpertBotsCard() {
  const { catalog } = useCatalog();
  const models = catalog?.models ?? [];
  const [snapshot, setSnapshot] = useState<ExpertBotsSnapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState({
    id: "",
    displayName: "",
    description: "",
    soul: "",
  });

  const unregistered = useMemo(
    () => snapshot?.availableProfiles.filter((item) => !item.registered) ?? [],
    [snapshot],
  );

  const reload = async () => {
    setError("");
    try {
      setSnapshot(await expertBotsList());
    } catch (reason) {
      setError(errorText(reason));
    }
  };
  useEffect(() => { void reload(); }, []);

  const run = async (operation: () => Promise<ExpertBotsSnapshot>): Promise<boolean> => {
    setBusy(true);
    setError("");
    try {
      setSnapshot(await operation());
      return true;
    } catch (reason) {
      setError(errorText(reason));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const startEdit = async (bot: ExpertBotSummary) => {
    setEditing(bot.id);
    setDraft({
      id: bot.id,
      displayName: bot.displayName,
      description: bot.description,
      soul: bot.managedByCompanion ? await expertBotSoulGet(bot.id).catch(() => "") : "",
    });
  };

  const create = async () => {
    const saved = await run(() => expertBotCreate({
      ...draft,
      cloneFrom: "default",
      modelPolicy: { mode: "inherit_picker" },
    }));
    if (saved) {
      setShowCreate(false);
      setDraft({ id: "", displayName: "", description: "", soul: "" });
    }
  };

  const removeBot = (bot: ExpertBotSummary) => {
    if (bot.managedByCompanion && !window.confirm(
      `确认删除“${bot.displayName}”及其 Hermes Profile？该 Bot 的本地会话、记忆和技能也会由 Hermes 一并处理。`,
    )) return;
    void run(() => bot.managedByCompanion
      ? expertBotDelete(bot.id)
      : expertBotUnregister(bot.id));
  };

  return (
    <div style={{ background: "var(--catfish-bg-elevated)", border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-md)", padding: "var(--space-4)" }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <div style={{ flex: 1 }}>
          <h3 style={{ margin: 0 }}>专家 Bot 管理</h3>
          <div style={{ marginTop: 5, color: "var(--catfish-text-muted)", fontSize: 11 }}>
            每个 Bot 是隔离的 Hermes Profile；可独立设置人设、技能、模型策略和使用场景。
          </div>
        </div>
        <span style={{ fontSize: 11, color: snapshot?.enabled ? "var(--catfish-cyan)" : "var(--catfish-text-muted)" }}>
          {snapshot?.enabled ? "运行中" : "总开关关闭"}
        </span>
        <button
          type="button"
          disabled={busy || !snapshot}
          onClick={() => void expertBotsSetEnabled(!snapshot?.enabled).then(reload).catch((e) => setError(errorText(e)))}
          style={buttonStyle}
        >
          {snapshot?.enabled ? "关闭全部" : "开启"}
        </button>
        <button type="button" disabled={busy} onClick={() => setShowCreate(true)} style={buttonStyle}>+ 新建 Bot</button>
      </div>

      {error && <div style={{ color: "var(--status-err)", fontSize: 11, marginTop: 10 }}>{error}</div>}

      {showCreate && (
        <div style={{ marginTop: 14, padding: 12, border: "1px solid var(--catfish-border)", borderRadius: 7 }}>
          <strong style={{ fontSize: 12 }}>新建隔离 Profile</strong>
          <div style={{ display: "grid", gridTemplateColumns: "180px 1fr", gap: 8, marginTop: 9 }}>
            <input style={fieldStyle} placeholder="profile-id，如 finance-reviewer" value={draft.id} onChange={(e) => setDraft({ ...draft, id: e.target.value })} />
            <input style={fieldStyle} placeholder="显示名称，如 财务复核专家" value={draft.displayName} onChange={(e) => setDraft({ ...draft, displayName: e.target.value })} />
            <input style={{ ...fieldStyle, gridColumn: "1 / 3" }} placeholder="职责说明" value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
            <textarea style={{ ...fieldStyle, gridColumn: "1 / 3", minHeight: 100, resize: "vertical" }} placeholder="SOUL / 工作边界（留空使用安全默认值）" value={draft.soul} onChange={(e) => setDraft({ ...draft, soul: e.target.value })} />
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <button type="button" disabled={busy || !draft.id || !draft.displayName} onClick={() => void create()} style={buttonStyle}>创建</button>
            <button type="button" onClick={() => setShowCreate(false)} style={buttonStyle}>取消</button>
          </div>
        </div>
      )}

      {unregistered.length > 0 && (
        <div style={{ marginTop: 12, fontSize: 11, color: "var(--catfish-text-muted)" }}>
          已有 Hermes Profile：{unregistered.map((profile) => (
            <button key={profile.id} type="button" disabled={busy} onClick={() => void run(() => expertBotRegisterExisting(profile.id))} style={{ ...buttonStyle, marginLeft: 6 }}>
              注册 {profile.displayName}
            </button>
          ))}
        </div>
      )}

      <div style={{ marginTop: 14, display: "grid", gap: 10 }}>
        {snapshot?.bots.map((bot) => (
          <div key={bot.id} style={{ padding: 12, border: "1px solid var(--catfish-border)", borderRadius: 7 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <strong>{bot.displayName}</strong>
              <code style={{ fontSize: 10, color: "var(--catfish-text-muted)" }}>{bot.id}</code>
              <span style={{ fontSize: 10, color: bot.ready ? "var(--catfish-cyan)" : "var(--status-warn)" }}>{bot.ready ? "● 已就绪" : `○ ${bot.reason}`}</span>
              <span style={{ flex: 1 }} />
              <label style={{ fontSize: 11 }}>
                <input type="checkbox" checked={bot.enabled} disabled={busy} onChange={(e) => void run(() => expertBotUpdate({ id: bot.id, enabled: e.target.checked }))} /> 启用
              </label>
              <button type="button" onClick={() => void startEdit(bot)} style={buttonStyle}>{bot.managedByCompanion ? "编辑" : "查看"}</button>
              <button type="button" disabled={busy} onClick={() => removeBot(bot)} style={buttonStyle}>
                {bot.managedByCompanion ? "删除" : "取消注册"}
              </button>
            </div>
            <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", margin: "7px 0" }}>
              {bot.description || "未填写职责"} · {bot.skillCount} 个技能
              {bot.boundScenarios.length > 0 ? ` · 已绑定 ${bot.boundScenarios.join("、")}` : " · 未绑定场景"}
            </div>
            <ModelPolicyEditor bot={bot} models={models} busy={busy} save={(policy) => run(() => expertBotUpdate({ id: bot.id, modelPolicy: policy }))} />
            {editing === bot.id && (
              <div style={{ display: "grid", gap: 7, marginTop: 10 }}>
                <input style={fieldStyle} disabled={!bot.managedByCompanion} value={draft.displayName} onChange={(e) => setDraft({ ...draft, displayName: e.target.value })} />
                <input style={fieldStyle} disabled={!bot.managedByCompanion} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
                {bot.managedByCompanion && <textarea style={{ ...fieldStyle, minHeight: 120, resize: "vertical" }} value={draft.soul} onChange={(e) => setDraft({ ...draft, soul: e.target.value })} />}
                <div style={{ display: "flex", gap: 7 }}>
                  {bot.managedByCompanion && <button type="button" disabled={busy} onClick={() => void run(() => expertBotUpdate({ id: bot.id, displayName: draft.displayName, description: draft.description, soul: draft.soul })).then((saved) => { if (saved) setEditing(null); })} style={buttonStyle}>保存 Profile</button>}
                  <button type="button" onClick={() => setEditing(null)} style={buttonStyle}>收起</button>
                </div>
              </div>
            )}
          </div>
        ))}
        {snapshot && snapshot.bots.length === 0 && <div style={{ color: "var(--catfish-text-muted)", fontSize: 11 }}>还没有专家 Bot。开启会自动建立“早安工作参谋”，也可以先新建。</div>}
      </div>

      <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px dashed var(--catfish-border)" }}>
        <strong style={{ fontSize: 12 }}>场景绑定</strong>
        <div style={{ display: "grid", gridTemplateColumns: "180px minmax(220px, 420px)", gap: 8, marginTop: 8 }}>
          {snapshot?.scenarios.map((scenario) => (
            <label key={scenario.id} style={{ display: "contents", fontSize: 11 }}>
              <span>{scenario.label}</span>
              <select style={fieldStyle} value={scenario.profileId ?? ""} disabled={busy} onChange={(e) => void run(() => expertBotBind(scenario.id, e.target.value || null))}>
                <option value="">不使用专家 Bot</option>
                {snapshot.bots.map((bot) => <option key={bot.id} value={bot.id}>{bot.displayName}</option>)}
              </select>
            </label>
          ))}
        </div>
      </div>
    </div>
  );
}
