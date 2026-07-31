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
 * · api_key_env 旁边写清楚"这里填变量名, 不是 key 本身", 而且**前后端都拦**
 *   ("sk-…" 这类值不是合法环境变量名) —— 这是最容易填错的一格: 标签里带
 *   "API Key" 三个字, 而旁边「API 地址」那格填的又确实是值本身。粘进真 key
 *   的后果是它明文写进数据库, 也就进 pg_dump 和备份
 * · api_base 和 api_key_env 的行为**不对称**, 界面必须说明: 地址填了就生效,
 *   可以不再依赖 .env; key 永远只能在服务器的 .env 里, 这一格填的只是
 *   "去哪个变量里取它"。旁边直接显示那个变量在服务器上设没设
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
  emptyModel,
  modelConfigApi,
  validateModel,
  type ModelConfig,
  type ModelListResponse,
} from "../../lib/model_config";

// 7/30 按 CLAUDE.md 军规 §1 拆分: 本文件曾到 1010 行 (红线 800)。
// 样式常量和 Field 搬去 modelConfigShared.tsx, 表单搬去 ModelForm.tsx。
import { BOX } from "./modelConfigShared";
import { ModelForm } from "./ModelForm";

// 军规 §3 re-export 协议: 不破老 caller。
export { ModelForm } from "./ModelForm";

export function ModelConfigPage() {
  return (
    <RoleGate require={["sysadmin"]}>
      <ModelConfigEditor />
    </RoleGate>
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
          keyState={data?.api_key_configured?.[editing.name]}
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
