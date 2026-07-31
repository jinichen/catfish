/** 模型编辑表单 + 失败切换编辑器.
 *
 * 7/30 按 CLAUDE.md 军规 §1 从 ModelConfigPage.tsx 拆出来 (那个文件到了
 * 1010 行, 红线 800)。见 modelConfigShared.tsx 文件头。
 *
 * 军规 §3: ModelForm / FallbackEditor 一起搬 —— FallbackEditor 只被
 * ModelForm 用, 拆两个文件反而多一层跳转。ModelConfigPage.tsx 顶部
 * re-export 保 import 兼容。
 */

import { useState } from "react";

import { BTN, BTN_PRIMARY } from "../../components/DataTable";
import { MODEL_PALETTE, matchPalette } from "../../lib/modelPalette";
import { describeKey, type Provider } from "../../lib/provider_config";
import {
  chainHopIssue,
  type ModelConfig,
} from "../../lib/model_config";
import {
  BOX,
  Field,
  fmtCompact,
  HINT,
  INPUT,
  isEnvPlaceholder,
  LABEL,
  MONO,
} from "./modelConfigShared";


export function ModelForm({
  model,
  isNew,
  busy,
  allModels,
  autoFallback,
  configError,
  keyState,
  providers,
  masterKeyEnv = "CATFISH_SECRET_KEY",
  onChange,
  onCancel,
  onSave,
}: {
  model: ModelConfig;
  isNew: boolean;
  busy: boolean;
  allModels: ModelConfig[];
  autoFallback: boolean;
  /** 这个模型的 ${VAR} 没解析成功时的说明 (来自 gateway)。 */
  configError?: string;
  /** 它引用的那个 key 变量在**服务器上**设没设。undefined = 新建的模型,
   *  服务端还不知道 (保存后才会有结论)。 */
  keyState?: boolean;
  /** 供应商下拉的选项。null = 还在加载。 */
  providers?: Provider[] | null;
  /** 主密钥的环境变量名, 给 describeKey 用。 */
  masterKeyEnv?: string;
  onChange: (m: ModelConfig) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  const selected = (providers ?? []).find((p) => p.id === model.upstream.provider) ?? null;

  // 当前颜色落在调色板的哪一项。匹配不上 = 客户用的是自己的品牌色,
  // 表单显示「自定义」并把原值原样保留, 不冲掉。
  const matched = matchPalette(model.color);
  const [custom, setCustom] = useState(!!model.color && !matched);
  const set = (patch: Partial<ModelConfig>) => onChange({ ...model, ...patch });
  const setUp = (patch: Partial<ModelConfig["upstream"]>) =>
    onChange({ ...model, upstream: { ...model.upstream, ...patch } });
  const num = (v: string) => (v.trim() === "" ? null : Number(v));

  return (
    <div style={{ ...BOX, borderColor: "var(--accent)" }}>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 8, gap: 8 }}>
        {/* 返回而不只是"取消" —— 现在这是一个整页的详情视图, 不是夹在列表
            上方的一小块, 得有明确的路回去。 */}
        <button style={BTN} onClick={onCancel} disabled={busy}>
          ← 返回列表
        </button>
        <b style={{ fontSize: 13 }}>{isNew ? "新增模型" : model.display_name}</b>
        {!isNew ? (
          <code style={{ fontSize: 11, color: "var(--text-muted)" }}>{model.name}</code>
        ) : null}
        <div style={{ flex: 1 }} />
        <button style={BTN_PRIMARY} onClick={onSave} disabled={busy}>
          {busy ? "保存中…" : "保存"}
        </button>
      </div>

      {configError ? (
        <div
          style={{
            fontSize: 12,
            lineHeight: 1.6,
            border: "1px solid var(--status-err)",
            borderRadius: "var(--radius-md)",
            padding: 8,
            marginBottom: 8,
          }}
        >
          <b style={{ color: "var(--status-err)" }}>这个模型现在是坏的</b>
          <div style={{ marginTop: 2, color: "var(--text-muted)" }}>
            {configError}
            <br />
            员工仍然能在列表里选到它，但每次调用都会失败。要么把下面的环境变量名
            改对，要么让 IT 在服务器的 .env 里补上这个变量。
          </div>
        </div>
      ) : null}

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
            placeholder="catfish-public-你的模型名"
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
          上游接入{" "}
          <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>
            （端点和 API key 归<b>供应商</b>管，在这里只选用哪一家）
          </span>
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
            gap: 8,
          }}
        >
          {/* 8/1: 「API 地址」和「API Key 环境变量名」两格从模型页消失了 ——
              它们现在归供应商管。这也顺带解决了 7/30 那个问题: 内网地址不再
              出现在模型编辑页上被截图带走。 */}
          <Field
            label="供应商"
            hint={
              providers === null
                ? "加载中…"
                : selected
                  ? `端点 ${selected.api_base || "SDK 默认"} · ${describeKey(selected, masterKeyEnv)}`
                  : "选一家。端点和 API key 都跟着它走 —— 换 key 是在「供应商」页改一次，不用逐个模型改。"
            }
          >
            <select
              style={INPUT}
              value={model.upstream.provider ?? ""}
              onChange={(e) => setUp({ provider: e.target.value || null })}
            >
              <option value="">（老形态：端点直接写在这个模型上）</option>
              {(providers ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.display_name}
                  {p.key_ok ? "" : "（key 不可用）"}
                </option>
              ))}
            </select>
          </Field>

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

          {/* 老形态 (还没迁到供应商的模型) 仍要能看到并改这两格 ——
              迁移期两种形态并存, 不给入口的话那些模型改不动。
              选了供应商之后这两格就没意义了 (合并时会被覆盖), 收起来。 */}
          {model.upstream.provider ? null : (
            <>
              <Field
                label="API 地址"
                hint={
                  isEnvPlaceholder(model.upstream.api_base)
                    ? "环境变量占位符：真实地址在服务器的 .env 里。建议改成选一个「供应商」——那样端点和 key 就归一处管了。"
                    : "这个模型还没迁到供应商。选上面的供应商之后这一格就不用填了。"
                }
              >
                <input
                  style={MONO}
                  value={model.upstream.api_base ?? ""}
                  onChange={(e) => setUp({ api_base: e.target.value || null })}
                  placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"
                />
              </Field>

              <Field
                label="API Key 环境变量名"
                hint="填变量名（如 DASHSCOPE_API_KEY），不是 key 本身。"
              >
                {/* ?? "" 是必须的 —— 库里这个键可能压根不存在 (见
                    lib/model_config.ts 里 api_key_env 的注释)。给 undefined
                    会让 React 把它当**非受控**输入框: 打字能打进去, 但
                    state 不更新, 保存的还是老值, 而且只在控制台留一句警告。 */}
                <input
                  style={MONO}
                  value={model.upstream.api_key_env ?? ""}
                  onChange={(e) => setUp({ api_key_env: e.target.value })}
                  placeholder="DASHSCOPE_API_KEY"
                />
                {keyState == null ? null : keyState ? (
                  <div style={{ ...HINT, color: "var(--status-ok)" }}>
                    ✓ 服务器上这个变量已设置。
                  </div>
                ) : (
                  <div style={{ ...HINT, color: "var(--status-err)" }}>
                    ✗ 服务器上<b>没有</b>这个变量 —— 这个模型现在每次调用都会失败。
                  </div>
                )}
              </Field>
            </>
          )}

          <Field
            label="超时（秒）"
            hint="慢模型（如推理模型）可能要 180 以上。留空用供应商的默认值。"
          >
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
          <Field
            label="上下文窗口"
            hint={
              model.context_window
                ? `= ${fmtCompact(model.context_window)} tokens。留空 = 不限制。`
                : "留空 = 不限制"
            }
          >
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

      </div>

      <div
        style={{
          marginTop: 10,
          paddingTop: 8,
          borderTop: "1px solid var(--border)",
        }}
      >
        <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 6 }}>
          展示与计价{" "}
          <span style={{ color: "var(--text-muted)" }}>
            （审计页用。不填单价的话成本按兜底价估算，数字不准）
          </span>
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
            gap: 8,
          }}
        >
          <Field
            label="单价（元 / 1000 token）"
            hint="按厂商价目表填，输入输出取平均。留空则审计页按兜底价 0.001 估算——现有模型真实单价跨度 0.00005 到 0.0218，差 400 倍，估出来的数不能当准。"
          >
            <input
              style={INPUT}
              type="number"
              step="0.00001"
              value={model.price_per_1k_tokens ?? ""}
              onChange={(e) => set({ price_per_1k_tokens: num(e.target.value) })}
              placeholder="0.0015"
            />
          </Field>
          {/* 8/1: 「图表颜色」和「列表圆点」原来是两个自由文本框, 各填各的。
              它们其实是**同一个模型在两个视图里的样子** —— 颜色用在审计页
              图表, 圆点用在模型列表和聊天页选择器。配不一致的话同一个模型
              在图表里是紫的、在列表里是绿的, 而这两处不会同时出现在一屏,
              所以没人会立刻发现。
              合成一个选择: 选一个配色, 两个值一起定。不一致在结构上就不可能。
              见 lib/modelPalette.ts。 */}
          <Field
            label="配色"
            hint={
              custom
                ? "自定义 —— 图表用左边的颜色, 列表用右边的圆点。两者不一致的话, 同一个模型在图表和列表里看起来会是两种颜色。"
                : "审计页图表和模型列表都用它。留空则图表给默认灰、列表给 ⚪。"
            }
          >
            <select
              style={INPUT}
              value={custom ? "__custom__" : (matched?.color ?? "")}
              onChange={(e) => {
                const v = e.target.value;
                if (v === "__custom__") {
                  setCustom(true);
                  return;
                }
                setCustom(false);
                const hit = MODEL_PALETTE.find((x) => x.color === v);
                set({ color: hit?.color ?? null, dot_emoji: hit?.dot ?? null });
              }}
            >
              <option value="">（不指定）</option>
              {MODEL_PALETTE.map((x) => (
                <option key={x.color} value={x.color}>
                  {x.dot} {x.label}
                </option>
              ))}
              <option value="__custom__">自定义…</option>
            </select>

            {custom ? (
              <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
                <input
                  style={{ ...MONO, flex: 1 }}
                  value={model.color ?? ""}
                  onChange={(e) => set({ color: e.target.value || null })}
                  placeholder="#7c3aed"
                />
                <input
                  style={{ ...INPUT, width: 64, textAlign: "center" }}
                  value={model.dot_emoji ?? ""}
                  onChange={(e) => set({ dot_emoji: e.target.value || null })}
                  placeholder="🟣"
                  maxLength={4}
                />
              </div>
            ) : null}
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

      <FallbackEditor
        model={model}
        all={allModels}
        autoFallback={autoFallback}
        onChange={onChange}
      />
    </div>
  );
}

/** 失败切换编辑器 (7/30).
 *
 * ## 为什么这一块要特别啰嗦
 *
 * fallback 是**只在上游出错时才走**的路径 —— 配错了平时完全看不出来,
 * 要等真的出故障那天才发现兜底没生效。而 gateway 的 resolve_chain 对每种
 * 配置问题都是 logger.warning + continue, 也就是静默少一跳。
 *
 * 所以这里把运行时会跳过的情况全部提前显示出来, 而不是让人配完就走。
 */
function FallbackEditor({
  model,
  all,
  autoFallback,
  onChange,
}: {
  model: ModelConfig;
  all: ModelConfig[];
  autoFallback: boolean;
  onChange: (m: ModelConfig) => void;
}) {
  const fb = model.fallback ?? { on_errors: [429, 503, 504, "timeout"], chain: [], max_hops: 2 };
  const chain = fb.chain ?? [];
  const onErrors = fb.on_errors ?? [];

  const setFb = (patch: Partial<NonNullable<ModelConfig["fallback"]>>) =>
    onChange({ ...model, fallback: { ...fb, ...patch } });

  const has500 = onErrors.includes(500);
  const isPrivate = model.tier === "private";
  const candidates = all.filter((m) => m.name !== model.name && !chain.includes(m.name));

  return (
    <div
      style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid var(--border)" }}
    >
      <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 6 }}>
        失败切换（fallback）{" "}
        <span style={{ color: "var(--text-muted)" }}>
          （上游出错时自动改用别的模型。只在出错时才走，平时看不出配得对不对）
        </span>
      </div>

      {/* 全局开关状态。关着的时候配了也不执行 —— 这是最要紧的一条提示 */}
      {!autoFallback ? (
        <div
          style={{
            ...BOX,
            padding: 6,
            fontSize: 11,
            lineHeight: 1.6,
            borderColor: "var(--status-warn)",
            marginBottom: 8,
          }}
        >
          <b>失败切换目前全局关闭</b> —— 下面配的链<b>不会执行</b>。
          <div style={{ color: "var(--text-muted)", marginTop: 2 }}>
            上游出错时直接把错误返回给员工，不自动改用别的模型。这是默认行为
            （改用别的模型会让"这次回答来自哪个模型"变得不可预测）。
            要打开：models.yaml 顶层加 <code>auto_fallback: true</code>，
            或给 gateway 设环境变量 <code>CATFISH_AUTO_FALLBACK=1</code>，然后重启。
          </div>
        </div>
      ) : null}

      <label style={LABEL}>切换顺序（从上到下依次尝试）</label>
      {chain.length === 0 ? (
        <div style={{ ...HINT, marginBottom: 4 }}>
          没有配 —— 这个模型出错时直接报错给员工。
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 3, marginBottom: 4 }}>
          {chain.map((n, i) => {
            const issue = chainHopIssue(model, n, all);
            return (
              <div
                key={n}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  fontSize: 12,
                  padding: "2px 0",
                }}
              >
                <span style={{ color: "var(--text-muted)", width: 16 }}>{i + 1}.</span>
                <code>{n}</code>
                {issue ? (
                  <span style={{ fontSize: 10, color: "var(--status-warn)" }}>
                    ⚠ {issue}
                  </span>
                ) : null}
                <div style={{ flex: 1 }} />
                <button
                  style={{ ...BTN, padding: "0 6px" }}
                  disabled={i === 0}
                  onClick={() => {
                    const next = [...chain];
                    [next[i - 1], next[i]] = [next[i], next[i - 1]];
                    setFb({ chain: next });
                  }}
                >
                  ↑
                </button>
                <button
                  style={{ ...BTN, padding: "0 6px" }}
                  disabled={i === chain.length - 1}
                  onClick={() => {
                    const next = [...chain];
                    [next[i], next[i + 1]] = [next[i + 1], next[i]];
                    setFb({ chain: next });
                  }}
                >
                  ↓
                </button>
                <button
                  style={{ ...BTN, padding: "0 6px", color: "var(--status-err)" }}
                  onClick={() => setFb({ chain: chain.filter((x) => x !== n) })}
                >
                  移除
                </button>
              </div>
            );
          })}
        </div>
      )}

      {candidates.length ? (
        <select
          style={{ ...INPUT, maxWidth: 280 }}
          value=""
          onChange={(e) => {
            if (e.target.value) setFb({ chain: [...chain, e.target.value] });
          }}
        >
          <option value="">+ 添加一个备用模型…</option>
          {candidates.map((m) => {
            const issue = chainHopIssue(model, m.name, all);
            return (
              <option key={m.name} value={m.name}>
                {m.display_name}
                {issue ? `（${issue}）` : ""}
              </option>
            );
          })}
        </select>
      ) : null}

      <div style={{ marginTop: 8 }}>
        <label style={LABEL}>触发条件</label>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, fontSize: 12 }}>
          {([429, 500, 502, 503, 504] as const).map((code) => (
            <label key={code} style={{ display: "flex", gap: 3 }}>
              <input
                type="checkbox"
                checked={onErrors.includes(code)}
                // 内网模型的 500 是保密红线, 直接禁掉复选框而不是等保存报错
                disabled={code === 500 && isPrivate}
                onChange={(e) =>
                  setFb({
                    on_errors: e.target.checked
                      ? [...onErrors, code]
                      : onErrors.filter((x) => x !== code),
                  })
                }
              />
              {code}
            </label>
          ))}
          <label style={{ display: "flex", gap: 3 }}>
            <input
              type="checkbox"
              checked={onErrors.includes("timeout")}
              onChange={(e) =>
                setFb({
                  on_errors: e.target.checked
                    ? [...onErrors, "timeout"]
                    : onErrors.filter((x) => x !== "timeout"),
                })
              }
            />
            超时
          </label>
        </div>

        {isPrivate ? (
          <div style={HINT}>
            内网模型的 500 不可勾选 ——
            内网返 500 就切公网，等于内网内容出公司，违反保密要求。
          </div>
        ) : !has500 ? (
          <div style={{ ...HINT, color: "var(--status-warn)" }}>
            公网模型建议勾上 500：公网之间切换不涉及跨边界，勾上员工撞 500
            时能自动切走而不是直接看到报错。
          </div>
        ) : null}
      </div>
    </div>
  );
}

