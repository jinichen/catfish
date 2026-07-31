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

import {
  Badge,
  BTN,
  BTN_DANGER,
  BTN_PRIMARY,
  DataTable,
  Section,
  Toolbar,
} from "../../components/DataTable";
import { ConfirmDialog } from "../../components/Dialog";
import { RoleGate } from "../../components/RoleGate";
import { splitDisplayName } from "../../lib/modelDisplay";
import {
  chainHopIssue,
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
// 7/30 二改: 本页原来的 BTN / BTN_PRIMARY 删了, 改用 components/DataTable
// 那一套 —— 全仓 5 份按钮样式各写各的, 尺寸和配色互不相同。
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

/** 这个值是不是个 ${VAR} 占位符.
 *
 * models.yaml 里内网地址是故意写成 ${INTERNAL_LLM_BASE_*} 的 —— 真实地址在
 * .env 里, 不进配置文件、不进 git、不进数据库。7/30 模型入库时一度把它插值
 * 之后才播种, 于是真实地址被烤进了库 (改 .env 从此不生效, 而且不报错)。
 * 已经修好并做了回迁, 但界面上得说清楚: 把占位符改成写死的地址是有代价的。
 */
function isEnvPlaceholder(v: string | null | undefined): boolean {
  return typeof v === "string" && /\$\{[A-Za-z_][A-Za-z0-9_]*(:-[^}]*)?\}/.test(v);
}

/** 128000 → "128K", 1000000 → "1M". 给输入框旁边的换算提示用 ——
 *  填 context_window 时人是按"12 万还是 128 万"想的, 而输入框里是一串 0。 */
function fmtCompact(n: number): string {
  if (n >= 1_000_000) {
    const v = n / 1_000_000;
    return `${Number.isInteger(v) ? v : v.toFixed(1)}M`;
  }
  if (n >= 1000) {
    const v = n / 1000;
    return `${Number.isInteger(v) ? v : v.toFixed(0)}K`;
  }
  return String(n);
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
  /** 正在等确认删除的那个模型。用应用内对话框而不是 window.prompt ——
   *  见 components/Dialog.tsx 文件头。 */
  const [confirming, setConfirming] = useState<ModelConfig | null>(null);

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
    setConfirming(null);
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
      {confirming ? (
        <ConfirmDialog
          title={`删除模型「${splitDisplayName(confirming.display_name).name}」？`}
          danger
          confirmLabel="删除"
          busy={busy}
          // 要求原样输入模型 ID —— 删掉之后正在用它的员工会话会立刻报错,
          // 不是一个可以手滑的操作。
          requireText={confirming.name}
          onCancel={() => setConfirming(null)}
          onConfirm={() => void remove(confirming)}
        >
          正在使用它的员工会话会<b style={{ color: "var(--status-err)" }}>立刻报错</b>。
          {confirming.default ? (
            <>
              <br />
              它现在是<b>默认模型</b>，删除后会自动把另一个对话模型设为默认。
            </>
          ) : null}
          <br />
          审计里的历史记录不会受影响，但这个模型会从员工的模型列表里消失。
        </ConfirmDialog>
      ) : null}

      <Toolbar title="模型配置">
        {/* 编辑时不显示列表操作 —— 「+ 新增模型」在编辑一半的时候点下去会
            丢掉未保存的改动, 而「刷新」在那个上下文里也没有意义。 */}
        {editing ? null : (
          <>
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              {data ? `${data.models.length} 个模型` : "加载中…"}
              {data?.revision != null ? ` · 版本 ${data.revision}` : ""}
            </span>
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
          </>
        )}
      </Toolbar>

      {/* 库没启用时说清楚为什么不能改, 而不是让人改完点保存才撞 503 */}
      {data && !editable ? (
        <div
          style={{
            ...BOX,
            fontSize: 12,
            lineHeight: 1.6,
            borderColor: "var(--status-warn)",
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
        <div style={{ ...BOX, fontSize: 12, borderColor: "var(--accent)" }}>
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
            color: "var(--status-err)",
            borderColor: "var(--status-err)",
            whiteSpace: "pre-wrap",
          }}
        >
          {err}
        </div>
      ) : null}

      {/* 7/30 四改: 编辑时**整页换成详情**, 不再把表单夹在列表上方。
          原来点列表第 6 行的「编辑」, 表单渲染在列表顶部 —— 出现在视野外,
          看起来像点了没反应; 而表单本身 200 多行字段, 夹在中间还会把列表
          整个推走。
          列表现在只回答"有哪些模型 / 哪个是默认 / 有没有出问题", 别的全在这。 */}
      {editing ? (
        <ModelForm
          model={editing}
          isNew={isNew}
          busy={busy}
          allModels={data?.models ?? []}
          autoFallback={data?.auto_fallback ?? false}
          configError={data?.config_errors?.[editing.name]}
          onChange={setEditing}
          onCancel={() => {
            setEditing(null);
            setErr(null);
          }}
          onSave={() => void save()}
        />
      ) : (
        // 7/30 二改: 每个模型一个大框两行 → 表格一行一个。原来 6 个模型就
        // 占满一屏, 而上游那串细节挤成一句用 · 隔开的长句, 想核对某一项
        // 得在句子里找。
        <Section>
          <DataTable
            rows={data?.models ?? []}
            rowKey={(m) => m.name}
            empty={data ? "还没有任何模型。点右上角「+ 新增模型」。" : "加载中…"}
            columns={[
              // 只回答"这是哪个模型 / 什么类型"。上游、上下文、单价、能力、
              // 失败切换全在编辑页 —— 那些是核对某一个模型时才看的, 摆在
              // 列表上的净效果只是让每一行都变宽、每一列都变窄。
              {
                header: "模型",
                width: "42%",
                truncate: true,
                cell: (m) => {
                  const { name, note } = splitDisplayName(m.display_name);
                  const badErr = data?.config_errors?.[m.name];
                  return (
                    <span
                      style={{ display: "flex", alignItems: "center", gap: 5 }}
                      // ID 不再单独占一列 (7/30 五改: 鸿波"不要 ID")。
                      // 但它仍是唯一标识 —— 显示名可以重复, 而审计日志、员工端
                      // 的模型选择、fallback 链里用的都是它。收进 tooltip,
                      // 要看要改在编辑页 (那里顶部就显示着)。
                      title={`${m.display_name}\n${m.name}`}
                    >
                      <span
                        style={{
                          fontWeight: 500,
                          // flex 子项不写 minWidth:0 是不会缩到内容尺寸以下的,
                          // 省略号根本不出现, 只会把整个单元格撑开
                          minWidth: 0,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                        }}
                      >
                        {name}
                      </span>
                      {note ? (
                        <span
                          style={{
                            color: "var(--text-muted)",
                            minWidth: 0,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                          }}
                        >
                          {note}
                        </span>
                      ) : null}
                      {/* 徽章 flexShrink:0, 名字才是可压缩的那个 ——
                          反过来窄屏下会先把徽章挤没 */}
                      <span style={{ display: "inline-flex", gap: 4, flexShrink: 0 }}>
                        {m.default ? <Badge tone="accent">默认</Badge> : null}
                        {badErr ? (
                          <Badge tone="err" title={badErr}>
                            配置有误
                          </Badge>
                        ) : null}
                        {m.price_per_1k_tokens == null ? (
                          <Badge
                            tone="warn"
                            title="没填单价, 概览和审计页的成本会按兜底价估算, 数字不准"
                          >
                            缺单价
                          </Badge>
                        ) : null}
                      </span>
                    </span>
                  );
                },
              },
              {
                header: "类型",
                width: 90,
                cell: (m) => (m.mode === "embedding" ? "向量" : "对话"),
              },
              {
                header: "归属",
                width: 90,
                // 公网标出来 —— 它走公网出口, 涉及合规, 是这一列里唯一需要
                // 留神的取值。私有是常态, 保持中性。
                cell: (m) =>
                  m.tier === "public" ? (
                    <span style={{ color: "var(--status-warn)" }}>公网</span>
                  ) : (
                    <span style={{ color: "var(--text-muted)" }}>私有</span>
                  ),
              },
              {
                header: "模态",
                width: 90,
                cell: (m) => (m.supports_vision ? "多模态" : "文本"),
              },
              {
                header: "",
                align: "right",
                width: 120,
                cell: (m) => (
                  <div style={{ display: "inline-flex", gap: 4 }}>
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
                      style={BTN_DANGER}
                      disabled={!editable || busy}
                      onClick={() => setConfirming(m)}
                    >
                      删除
                    </button>
                  </div>
                ),
              },
            ]}
          />
        </Section>
      )}
    </div>
  );
}

function ModelForm({
  model,
  isNew,
  busy,
  allModels,
  autoFallback,
  configError,
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
  onChange: (m: ModelConfig) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
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

          <Field
            label="API 地址"
            hint={
              isEnvPlaceholder(model.upstream.api_base)
                ? "⚠ 这里现在是一个环境变量占位符，真实地址在服务器的 .env 里。改成写死的地址之后，IT 再改 .env 就不生效了 —— 除非你确实要为这个模型单独指定，否则别动。"
                : "只有 OpenAI 兼容的自建端点才需要填，官方 API 留空。内网地址建议写成 ${变量名}，真实值放服务器的 .env —— 这样它不会进数据库、不会进备份、也不会出现在截图里。"
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
          <Field label="图表颜色" hint="审计页图表里区分模型用。留空给默认灰。">
            <input
              style={MONO}
              value={model.color ?? ""}
              onChange={(e) => set({ color: e.target.value || null })}
              placeholder="#7c3aed"
            />
          </Field>
          <Field label="列表圆点" hint="一眼分辨来源。留空用 ⚪。">
            <input
              style={INPUT}
              value={model.dot_emoji ?? ""}
              onChange={(e) => set({ dot_emoji: e.target.value || null })}
              placeholder="🟣"
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
