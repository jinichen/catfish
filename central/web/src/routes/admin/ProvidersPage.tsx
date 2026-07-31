/** /admin/providers — 供应商配置 (8/1, DESIGN-PROVIDER-SPLIT §7).
 *
 * ## 这一页解决什么
 *
 * 在这之前，加一家新供应商在客户现场**做不到**：要改 .env、改
 * docker-compose.yml（那里是显式列名转发 env 的，新变量容器根本看不见），
 * 再 `docker compose up -d` 重建容器。改 UI 解决不了那个问题，所以先做了
 * 后端的拆分和 key 加密存库。
 *
 * 现在这条路通了，而且**不需要重启** —— 配置 3 秒内热加载。
 *
 * ## key 那一格的约定
 *
 * 服务端永远不返回 key，所以编辑时那一格永远是空的：留空 = 不改，
 * 填了 = 覆盖。这是密码字段的标准做法 —— 不这么做的话，管理员改一次
 * 显示名就把 key 清掉了，而且没有任何提示。
 *
 * ## 列表上直接显示 key 到底能不能用
 *
 * 「已加密存库」不等于「能用」：主密钥换过没跑轮换脚本、密文被改坏，都会
 * 让它解不开。走环境变量的更常见 —— 变量名填对了但服务器上根本没设。
 * 这两种情况以前都只能等员工调用失败才发现。
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
import {
  describeKey,
  emptyProvider,
  providerApi,
  validateProviderId,
  type Provider,
  type ProviderInput,
  type ProviderListResponse,
} from "../../lib/provider_config";
import { BOX, Field, INPUT, MONO } from "./modelConfigShared";

export function ProvidersPage() {
  return (
    <RoleGate require={["sysadmin"]}>
      <ProvidersEditor />
    </RoleGate>
  );
}

interface Editing {
  id: string;
  isNew: boolean;
  body: ProviderInput;
}

function ProvidersEditor() {
  const [data, setData] = useState<ProviderListResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [confirming, setConfirming] = useState<Provider | null>(null);

  const load = useCallback(async () => {
    try {
      setErr(null);
      setData(await providerApi.list());
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const editable = data?.editable ?? false;
  const masterEnv = data?.master_key_env ?? "CATFISH_SECRET_KEY";

  async function save() {
    if (!editing) return;
    const idErr = editing.isNew ? validateProviderId(editing.id) : null;
    if (idErr) {
      setErr(`供应商标识：${idErr}`);
      return;
    }
    setBusy(true);
    try {
      setErr(null);
      const r = await providerApi.put(editing.id, editing.body);
      setNotice(
        `${r.created ? "已新增" : "已保存"} ${editing.id}。其它 gateway 进程最多 3 秒后生效。` +
          (r.warning ? `\n⚠ ${r.warning}` : ""),
      );
      setEditing(null);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(p: Provider) {
    setConfirming(null);
    setBusy(true);
    try {
      setErr(null);
      await providerApi.remove(p.id);
      setNotice(`已删除供应商 ${p.id}。`);
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
          title={`删除供应商「${confirming.display_name}」？`}
          danger
          confirmLabel="删除"
          busy={busy}
          requireText={confirming.id}
          onCancel={() => setConfirming(null)}
          onConfirm={() => void remove(confirming)}
        >
          它的端点和 API key 会一起删掉。
          <br />
          存库的 key 删了就没了 —— 要恢复只能去供应商后台重新申请。
        </ConfirmDialog>
      ) : null}

      <Toolbar title="供应商">
        {editing ? null : (
          <>
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              {data ? `${data.providers.length} 家` : "加载中…"}
            </span>
            <button style={BTN} onClick={() => void load()} disabled={busy}>
              刷新
            </button>
            <button
              style={BTN_PRIMARY}
              disabled={!editable || busy || !(data?.table_ready ?? false)}
              onClick={() => {
                setEditing({ id: "", isNew: true, body: emptyProvider() });
                setErr(null);
              }}
            >
              + 新增供应商
            </button>
          </>
        )}
      </Toolbar>

      {/* 表还没建 —— 跟"一家都没有"完全不是一回事, 下一步动作也不同。
          不分开的话界面显示"0 家", 管理员会去点「+ 新增供应商」然后保存失败。 */}
      {data && !data.table_ready ? (
        <div style={{ ...BOX, fontSize: 12, lineHeight: 1.7, borderColor: "var(--status-err)" }}>
          <b style={{ color: "var(--status-err)" }}>供应商表还没建</b>
          <div style={{ marginTop: 4, color: "var(--text-muted)" }}>
            数据库迁移还没跑过（这个版本新增了 <code>gateway_providers</code> 表）。
            现在按老形态运行 —— 模型的端点和 key 变量名还写在各自的模型配置里，
            一切正常，只是这一页是空的。
            <br />
            <code style={{ fontSize: 11 }}>
              cd central/llm-gateway &amp;&amp; alembic upgrade head
            </code>
            <br />
            然后<b>重启网关</b> —— 启动时会自动把现有模型拆成供应商（幂等，跑几次都一样）。
            <br />
            <span style={{ fontSize: 11 }}>
              （Docker 部署不会出现这个提示：容器启动命令里已经带了迁移。本机
              直跑网关时才需要自己跑一次。）
            </span>
          </div>
        </div>
      ) : null}

      {/* 主密钥没配时**在保存前**说清楚, 而不是让人填完 key 点保存才撞 400 */}
      {data && !data.secret_key_configured ? (
        <div style={{ ...BOX, fontSize: 12, lineHeight: 1.7, borderColor: "var(--status-warn)" }}>
          <b>还不能把 API key 存进来</b>
          <div style={{ marginTop: 4, color: "var(--text-muted)" }}>
            服务器没有配 <code>{masterEnv}</code>，key 没法加密保存。现在只能用
            「环境变量名」那一格（也就是老办法）。
            <br />
            让 IT 在服务器的 .env 里加一行然后重启网关：
            <br />
            <code style={{ fontSize: 11 }}>
              {masterEnv}=$(python3 -c "from cryptography.fernet import Fernet;
              print(Fernet.generate_key().decode())")
            </code>
            <br />⚠ 这个值丢了的话，所有存库的 API key 都解不开 —— 生成后请立刻存进密码管理器。
          </div>
        </div>
      ) : null}

      {notice ? (
        <div style={{ ...BOX, fontSize: 12, borderColor: "var(--accent)", whiteSpace: "pre-wrap" }}>
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
          {/* 错误框也要能关 —— 上面那个 notice 有关闭按钮而这个没有,
              于是一条已经处理完的错误会一直挂在页面顶部。 */}
          <button
            style={{ ...BTN, marginLeft: 8, padding: "1px 6px" }}
            onClick={() => setErr(null)}
          >
            知道了
          </button>
        </div>
      ) : null}

      {editing ? (
        <ProviderForm
          editing={editing}
          busy={busy}
          canStoreKey={data?.secret_key_configured ?? false}
          masterKeyEnv={masterEnv}
          onChange={setEditing}
          onCancel={() => {
            setEditing(null);
            setErr(null);
          }}
          onSave={() => void save()}
        />
      ) : (
        <Section>
          <DataTable
            rows={data?.providers ?? []}
            rowKey={(p) => p.id}
            empty={
              !data
                ? "加载中…"
                : !data.table_ready
                  ? "表还没建 —— 见上面的说明。"
                  : "还没有供应商。点右上角「+ 新增供应商」。"
            }
            columns={[
              {
                header: "供应商",
                width: "26%",
                truncate: true,
                cell: (p) => (
                  <span
                    style={{ display: "flex", alignItems: "center", gap: 5 }}
                    title={`${p.display_name}\n${p.id}`}
                  >
                    <span
                      style={{
                        fontWeight: 500,
                        minWidth: 0,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {p.display_name}
                    </span>
                    {!p.key_ok ? (
                      <Badge tone="err" title={describeKey(p, masterEnv)}>
                        key 不可用
                      </Badge>
                    ) : null}
                  </span>
                ),
              },
              {
                header: "端点",
                truncate: true,
                width: "30%",
                // 内网地址通常是 ${VAR} 占位符, 显示原样即可 —— 真实地址在
                // .env 里, 不该出现在这里被截图带走 (7/30 那条教训)。
                cell: (p) =>
                  p.api_base ? (
                    <code style={{ fontSize: 11 }} title={p.api_base}>
                      {p.api_base}
                    </code>
                  ) : (
                    <span style={{ color: "var(--text-muted)" }}>SDK 默认端点</span>
                  ),
              },
              {
                header: "API key",
                width: 190,
                truncate: true,
                cell: (p) => (
                  <span
                    style={{ color: p.key_ok ? undefined : "var(--status-err)" }}
                    title={describeKey(p, masterEnv)}
                  >
                    {p.key_source === "stored"
                      ? "已加密存库"
                      : p.key_source === "env"
                        ? p.api_key_env
                        : "未配置"}
                  </span>
                ),
              },
              {
                header: "模型",
                align: "right",
                width: 70,
                cell: (p) => (
                  <span title={p.models.join("\n") || "还没有模型用它"}>{p.models.length}</span>
                ),
              },
              {
                header: "",
                align: "right",
                width: 120,
                cell: (p) => (
                  <div style={{ display: "inline-flex", gap: 4 }}>
                    <button
                      style={BTN}
                      disabled={!editable || busy}
                      onClick={() => {
                        setEditing({
                          id: p.id,
                          isNew: false,
                          // ⚠ 不带 api_key —— 服务端本来也不返回它。
                          // 留空 = 不改 (见文件头)。
                          body: {
                            display_name: p.display_name,
                            api_base: p.api_base,
                            api_key_env: p.api_key_env,
                            timeout: p.timeout,
                          },
                        });
                        setErr(null);
                      }}
                    >
                      编辑
                    </button>
                    {/* 还有模型在用时**在点之前**就禁用, 而不是让人走完确认
                        对话框、输入完整 ID、点了确认才被 400 拒。
                        列表上「模型」那列已经写着数量了 —— 前端早就知道删不掉,
                        没有理由让人白走一遍。
                        (后端那道拦截仍然要有: 另一个标签页刚给它加了模型这种
                         竞态, 只有服务端知道。) */}
                    <span
                      title={
                        p.models.length
                          ? `删不掉 —— 这些模型在用它:\n${p.models
                              .map((m) => `· ${m}`)
                              .join("\n")}\n\n先把它们改到别的供应商上, 或者删掉这些模型。`
                          : undefined
                      }
                    >
                      <button
                        style={{
                          ...BTN_DANGER,
                          ...(p.models.length ? { opacity: 0.4, cursor: "not-allowed" } : null),
                        }}
                        disabled={!editable || busy || p.models.length > 0}
                        onClick={() => setConfirming(p)}
                      >
                        删除
                      </button>
                    </span>
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

function ProviderForm({
  editing,
  busy,
  canStoreKey,
  masterKeyEnv,
  onChange,
  onCancel,
  onSave,
}: {
  editing: Editing;
  busy: boolean;
  canStoreKey: boolean;
  masterKeyEnv: string;
  onChange: (e: Editing) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  const { id, isNew, body } = editing;
  const set = (patch: Partial<ProviderInput>) =>
    onChange({ ...editing, body: { ...body, ...patch } });

  return (
    <Section style={{ borderColor: "var(--accent)" }}>
      <div style={{ display: "flex", alignItems: "center", marginBottom: 8, gap: 8 }}>
        <button style={BTN} onClick={onCancel} disabled={busy}>
          ← 返回列表
        </button>
        <b style={{ fontSize: 13 }}>{isNew ? "新增供应商" : body.display_name || id}</b>
        {!isNew ? <code style={{ fontSize: 11, color: "var(--text-muted)" }}>{id}</code> : null}
        <div style={{ flex: 1 }} />
        <button style={BTN_PRIMARY} onClick={onSave} disabled={busy}>
          {busy ? "保存中…" : "保存"}
        </button>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
          gap: 8,
        }}
      >
        <Field
          label="标识"
          hint={
            isNew
              ? "小写字母、数字、连字符。建好后不能改 —— 它会出现在每个模型的配置里。例如 dashscope / internal-vllm-vision"
              : "已建的供应商不能改标识。要改就新建一个、把模型指过去、再删旧的。"
          }
        >
          <input
            style={MONO}
            value={id}
            disabled={!isNew}
            onChange={(e) => onChange({ ...editing, id: e.target.value })}
            placeholder="dashscope"
          />
        </Field>

        <Field label="显示名称" hint="给人看的。例如「阿里云百炼」「内网 vLLM · 视觉」">
          <input
            style={INPUT}
            value={body.display_name}
            onChange={(e) => set({ display_name: e.target.value })}
            placeholder="阿里云百炼"
          />
        </Field>

        <Field
          label="API 地址"
          hint="OpenAI 兼容的自建端点才需要填；用官方 SDK 的（如 Gemini）留空。内网地址可以写成 ${变量名} 交给 .env 管 —— 那样它不进数据库、不进备份、也不会出现在截图里。"
        >
          <input
            style={MONO}
            value={body.api_base ?? ""}
            onChange={(e) => set({ api_base: e.target.value || null })}
            placeholder="https://dashscope.aliyuncs.com/compatible-mode/v1"
          />
        </Field>

        <Field label="超时（秒）" hint="这家的默认值。模型可以单独覆盖（慢模型如推理模型要 180 以上）。">
          <input
            style={INPUT}
            type="number"
            value={body.timeout}
            onChange={(e) => set({ timeout: Number(e.target.value) || 60 })}
          />
        </Field>
      </div>

      <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid var(--border)" }}>
        <div style={{ fontSize: 11, fontWeight: 600, marginBottom: 6 }}>
          API key{" "}
          <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>
            （两种方式二选一。填了上面那格就用存库的，否则读环境变量）
          </span>
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
            gap: 8,
          }}
        >
          <Field
            label={canStoreKey ? "API key（存库，加密）" : "API key（当前不可用）"}
            hint={
              canStoreKey
                ? "留空 = 不改现有的。填了就覆盖。保存后立即生效，不用重启。这里存的是加密后的密文，明文不会再回传到界面。"
                : `服务器没有配 ${masterKeyEnv}，还不能把 key 存进来 —— 请先用右边的「环境变量名」。`
            }
          >
            <input
              style={MONO}
              type="password"
              autoComplete="new-password"
              disabled={!canStoreKey}
              value={body.api_key ?? ""}
              onChange={(e) => set({ api_key: e.target.value })}
              placeholder={canStoreKey ? "留空 = 不改" : "先配主密钥"}
            />
          </Field>

          <Field
            label="环境变量名（老办法）"
            hint="填变量名不是 key 本身，例如 DASHSCOPE_API_KEY。key 放在服务器的 .env 里。⚠ 用这条的话，以后换 key 要改 .env 并重建容器。"
          >
            <input
              style={MONO}
              value={body.api_key_env ?? ""}
              onChange={(e) => set({ api_key_env: e.target.value || null })}
              placeholder="DASHSCOPE_API_KEY"
            />
          </Field>
        </div>
      </div>
    </Section>
  );
}
