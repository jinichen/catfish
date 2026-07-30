/** /admin/models — 模型配置管理 (7/30).
 *
 * 在这之前, 改模型只能编辑 models.yaml 再重启 gateway。而那个文件在容器里
 * 是**只读挂载**, 所以客户现场根本没有改模型这条路 —— 只能等我们出新包。
 *
 * 现在模型存库 (gateway_models 表), 这一页是它的编辑界面。
 *
 * ## 界面上几处刻意的设计
 *
 * · 库没启用时整页只读, 并说明原因 —— 而不是让人改完点保存才发现 503
 * · upstream 那几项单独成组并标注"部署配置" —— 它们填错的后果是模型直接
 *   不可用, 跟改个显示名不是一个量级
 * · api_key_env 旁边明确写"这里填变量名, 不是 key 本身" —— 这是最容易
 *   填错的一格, 填了真 key 会把密钥写进数据库
 * · 删除要输入模型 ID 确认 —— 模型被删掉的话, 正在用它的员工会话会直接报错
 */

import { useCallback, useEffect, useState } from "react";

import { RoleGate } from "../../components/RoleGate";
import {
  emptyModel,
  modelConfigApi,
  validateModel,
  type ModelConfig,
  type ModelListResponse,
} from "../../lib/model_config";

const BOX: React.CSSProperties = {
  background: "var(--bg-elev)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-md)",
  padding: 10,
};
const INPUT: React.CSSProperties = {
  padding: "3px 6px",
  border: "1px solid var(--border)",
  borderRadius: 3,
  fontSize: 12,
  width: "100%",
  boxSizing: "border-box",
  fontFamily: "inherit",
};
const MONO: React.CSSProperties = { ...INPUT, fontFamily: "monospace" };
const BTN: React.CSSProperties = {
  padding: "3px 10px",
  border: "1px solid var(--border)",
  borderRadius: 3,
  background: "var(--bg)",
  color: "var(--text)",
  fontSize: 12,
  cursor: "pointer",
};
const BTN_PRIMARY: React.CSSProperties = {
  ...BTN,
  background: "var(--accent, #0d9488)",
  borderColor: "transparent",
  color: "#fff",
};
const LABEL: React.CSSProperties = {
  fontSize: 11,
  color: "var(--text-muted)",
  display: "block",
  marginBottom: 2,
};
const HINT: React.CSSProperties = {
  fontSize: 10,
  color: "var(--text-muted)",
  marginTop: 2,
  lineHeight: 1.4,
};

export function ModelConfigPage() {
  return (
    <RoleGate require={["sysadmin"]}>
      <ModelConfigEditor />
    </RoleGate>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label style={LABEL}>{label}</label>
      {children}
      {hint ? <div style={HINT}>{hint}</div> : null}
    </div>
  );
}

function ModelConfigEditor() {
  const [data, setData] = useState<ModelListResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<ModelConfig | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setErr(null);
      setData(await modelConfigApi.list());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const editable = data?.editable ?? false;

  async function save() {
    if (!editing) return;
    const errs = validateModel(editing);
    if (errs.length) {
      setErr(errs.join("；"));
      return;
    }
    setBusy(true);
    try {
      setErr(null);
      const r = await modelConfigApi.put(editing.name, editing);
      setNotice(
        `${r.created ? "已新增" : "已保存"} ${editing.name}。` +
          "其它 gateway 进程最多 3 秒后生效。",
      );
      setEditing(null);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(m: ModelConfig) {
    // 输入模型 ID 确认 —— 删掉之后正在用它的员工会话会直接报错, 不是
    // 一个可以手滑的操作。
    const typed = window.prompt(
      `删除模型「${m.display_name}」？\n\n` +
        `正在使用它的员工会话会立刻报错。\n` +
        `确认请输入模型 ID：${m.name}`,
    );
    if (typed !== m.name) return;
    setBusy(true);
    try {
      setErr(null);
      const r = await modelConfigApi.remove(m.name);
      setNotice(
        r.promoted_default
          ? `已删除 ${m.name}。它原本是默认模型，已自动把 ${r.promoted_default} 设为新默认。`
          : `已删除 ${m.name}。`,
      );
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <h3 style={{ margin: 0, fontSize: 15 }}>模型配置</h3>
        <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
          {data ? `${data.models.length} 个模型` : "加载中…"}
          {data?.revision != null ? ` · 版本 ${data.revision}` : ""}
        </span>
        <div style={{ flex: 1 }} />
        <button style={BTN} onClick={() => void load()} disabled={busy}>
          刷新
        </button>
        <button
          style={BTN_PRIMARY}
          disabled={!editable || busy}
          onClick={() => {
            setEditing(emptyModel());
            setIsNew(true);
            setErr(null);
          }}
        >
          + 新增模型
        </button>
      </div>

      {/* 库没启用时说清楚为什么不能改, 而不是让人改完点保存才撞 503 */}
      {data && !editable ? (
        <div
          style={{
            ...BOX,
            fontSize: 12,
            lineHeight: 1.6,
            borderColor: "var(--warn, #d97706)",
          }}
        >
          <b>当前为只读</b>
          <div style={{ marginTop: 4, color: "var(--text-muted)" }}>
            未配置数据库（CATFISH_DB_URL），模型来自 models.yaml。
            这种情况下只能改文件后重启 gateway。下面列出的是当前生效的配置。
          </div>
        </div>
      ) : null}

      {notice ? (
        <div style={{ ...BOX, fontSize: 12, borderColor: "var(--accent, #0d9488)" }}>
          {notice}
          <button
            style={{ ...BTN, marginLeft: 8, padding: "1px 6px" }}
            onClick={() => setNotice(null)}
          >
            知道了
          </button>
        </div>
      ) : null}

      {err ? (
        <div
          style={{
            ...BOX,
            fontSize: 12,
            color: "var(--danger, #dc2626)",
            borderColor: "var(--danger, #dc2626)",
            whiteSpace: "pre-wrap",
          }}
        >
          {err}
        </div>
      ) : null}

      {editing ? (
        <ModelForm
          model={editing}
          isNew={isNew}
          busy={busy}
          onChange={setEditing}
          onCancel={() => {
            setEditing(null);
            setErr(null);
          }}
          onSave={() => void save()}
        />
      ) : null}

      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {(data?.models ?? []).map((m) => (
          <div key={m.name} style={BOX}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <b style={{ fontSize: 13 }}>{m.display_name}</b>
              {m.default ? (
                <span
                  style={{
                    fontSize: 10,
                    padding: "1px 5px",
                    borderRadius: 3,
                    background: "var(--accent, #0d9488)",
                    color: "#fff",
                  }}
                >
                  默认
                </span>
              ) : null}
              <span
                style={{
                  fontSize: 10,
                  padding: "1px 5px",
                  borderRadius: 3,
                  border: "1px solid var(--border)",
                  color: "var(--text-muted)",
                }}
              >
                {m.tier === "public" ? "公网" : "私有"}
              </span>
              <code style={{ fontSize: 11, color: "var(--text-muted)" }}>{m.name}</code>
              <div style={{ flex: 1 }} />
              <button
                style={BTN}
                disabled={!editable || busy}
                onClick={() => {
                  setEditing(JSON.parse(JSON.stringify(m)) as ModelConfig);
                  setIsNew(false);
                  setErr(null);
                }}
              >
                编辑
              </button>
              <button
                style={{ ...BTN, color: "var(--danger, #dc2626)" }}
                disabled={!editable || busy}
                onClick={() => void remove(m)}
              >
                删除
              </button>
            </div>
            <div style={{ ...HINT, marginTop: 4 }}>
              上游 <code>{m.upstream.model}</code>
              {m.upstream.api_base ? ` · ${m.upstream.api_base}` : ""} · key 取自{" "}
              <code>{m.upstream.api_key_env}</code>
              {m.context_window ? ` · 上下文 ${m.context_window.toLocaleString()}` : ""}
              {m.max_output_tokens
                ? ` · 单次输出 ${m.max_output_tokens.toLocaleString()}`
                : ""}
              {m.supports_tool_use ? " · 工具" : ""}
              {m.supports_vision ? " · 视觉" : ""}
              {m.fallback?.chain?.length
                ? ` · 失败切 ${m.fallback.chain.join("→")}`
                : ""}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ModelForm({
  model,
  isNew,
  busy,
  onChange,
  onCancel,
  onSave,
}: {
  model: ModelConfig;
  isNew: boolean;
  busy: boolean;
  onChange: (m: ModelConfig) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  const set = (patch: Partial<ModelConfig>) => onChange({ ...model, ...patch });
  const setUp = (patch: Partial<ModelConfig["upstream"]>) =>
    onChange({ ...model, upstream: { ...model.upstream, ...patch } });
  const num = (v: string) => (v.trim() === "" ? null : Number(v));

  return (
    <div style={{ ...BOX, borderColor: "var(--accent, #0d9488)" }}>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 8 }}>
        <b style={{ fontSize: 13 }}>{isNew ? "新增模型" : `编辑 ${model.name}`}</b>
        <div style={{ flex: 1 }} />
        <button style={BTN} onClick={onCancel} disabled={busy}>
          取消
        </button>
        <button style={{ ...BTN_PRIMARY, marginLeft: 6 }} onClick={onSave} disabled={busy}>
          {busy ? "保存中…" : "保存"}
        </button>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
          gap: 8,
        }}
      >
        <Field
          label="模型 ID"
          hint={
            isNew
              ? "员工看不到，但会出现在 URL 和审计日志里。建好后不要改——改名等于删旧建新。"
              : "已建的模型不能改 ID。要改就新建一个再删旧的。"
          }
        >
          <input
            style={MONO}
            value={model.name}
            disabled={!isNew}
            onChange={(e) => set({ name: e.target.value })}
            placeholder="catfish-public-qwen"
          />
        </Field>

        <Field label="显示名称" hint="员工在聊天页看到的就是这个">
          <input
            style={INPUT}
            value={model.display_name}
            onChange={(e) => set({ display_name: e.target.value })}
            placeholder="通义千问 Plus"
          />
        </Field>

        <Field label="归属" hint="公网模型会走公网出口，注意合规">
          <select
            style={INPUT}
            value={model.tier}
            onChange={(e) => set({ tier: e.target.value as ModelConfig["tier"] })}
          >
            <option value="private">私有（内网）</option>
            <option value="public">公网</option>
          </select>
        </Field>

        <Field label="用途" hint="embedding 不会出现在聊天模型选择里">
          <select
            style={INPUT}
            value={model.mode}
            onChange={(e) => set({ mode: e.target.value as ModelConfig["mode"] })}
          >
            <option value="chat">对话</option>
            <option value="embedding">向量</option>
          </select>
        </Field>
      </div>

      <div
        style={{
          marginTop: 10,
          paddingTop: 8,
          borderTop: "1px solid var(--border)",
        }}
      >
        <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 6 }}>
          上游接入 <span style={{ color: "var(--text-muted)" }}>（部署配置，填错模型直接不可用）</span>
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
            gap: 8,
          }}
        >
          <Field
            label="上游模型"
            hint="必须带 provider 前缀。不带的话 LiteLLM 不知道走哪家。"
          >
            <input
              style={MONO}
              value={model.upstream.model}
              onChange={(e) => setUp({ model: e.target.value })}
              placeholder="openai/qwen-plus"
            />
          </Field>

          <Field label="API 地址" hint="只有 OpenAI 兼容的自建端点才需要填，官方 API 留空">
            <input
              style={MONO}
              value={model.upstream.api_base ?? ""}
              onChange={(e) => setUp({ api_base: e.target.value || null })}
              placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"
            />
          </Field>

          <Field
            label="API Key 环境变量名"
            hint="⚠ 这里填变量名（如 DASHSCOPE_API_KEY），不是 key 本身。key 存在服务器的 .env 里，不进数据库。"
          >
            <input
              style={MONO}
              value={model.upstream.api_key_env}
              onChange={(e) => setUp({ api_key_env: e.target.value })}
              placeholder="DASHSCOPE_API_KEY"
            />
          </Field>

          <Field label="超时（秒）" hint="慢模型（如推理模型）可能要 180 以上">
            <input
              style={INPUT}
              type="number"
              value={model.upstream.timeout ?? 60}
              onChange={(e) => setUp({ timeout: Number(e.target.value) || 60 })}
            />
          </Field>
        </div>
      </div>

      <div
        style={{
          marginTop: 10,
          paddingTop: 8,
          borderTop: "1px solid var(--border)",
        }}
      >
        <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 6 }}>
          能力与限制{" "}
          <span style={{ color: "var(--text-muted)" }}>（照上游官方文档填，填错会在运行时报错）</span>
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
            gap: 8,
          }}
        >
          <Field label="上下文窗口" hint="留空 = 不限制">
            <input
              style={INPUT}
              type="number"
              value={model.context_window ?? ""}
              onChange={(e) => set({ context_window: num(e.target.value) })}
              placeholder="128000"
            />
          </Field>
          <Field
            label="单次输出上限"
            hint="跟上下文窗口是两回事。超过上游上限会返 400，所以要照官方文档填。"
          >
            <input
              style={INPUT}
              type="number"
              value={model.max_output_tokens ?? ""}
              onChange={(e) => set({ max_output_tokens: num(e.target.value) })}
              placeholder="8192"
            />
          </Field>
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 8 }}>
          {(
            [
              ["supports_tool_use", "支持工具调用"],
              ["supports_streaming", "支持流式"],
              ["supports_vision", "支持图片"],
            ] as const
          ).map(([k, label]) => (
            <label key={k} style={{ fontSize: 12, display: "flex", gap: 4 }}>
              <input
                type="checkbox"
                checked={model[k]}
                onChange={(e) => set({ [k]: e.target.checked } as Partial<ModelConfig>)}
              />
              {label}
            </label>
          ))}
          <label style={{ fontSize: 12, display: "flex", gap: 4 }}>
            <input
              type="checkbox"
              checked={model.default}
              onChange={(e) => set({ default: e.target.checked })}
            />
            设为默认模型
          </label>
          <label style={{ fontSize: 12, display: "flex", gap: 4 }}>
            <input
              type="checkbox"
              checked={model.cost_tier === "paid"}
              onChange={(e) => set({ cost_tier: e.target.checked ? "paid" : "free" })}
            />
            计费模型
          </label>
        </div>
        {model.default ? (
          <div style={HINT}>
            保存后会自动取消其它模型的默认标记 —— 默认模型全局只能有一个。
          </div>
        ) : null}
      </div>
    </div>
  );
}
