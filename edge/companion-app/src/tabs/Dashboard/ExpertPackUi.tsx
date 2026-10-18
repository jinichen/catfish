/**
 * 专家包的界面零件 (10/18, docs/EXPERT-PACK-PLAN.md 第二 / 三阶段): 权限摘要、安装确认、发布、资源市场、我提交的。
 *
 * 安装前一定先给员工看权限摘要 (从包里实际内容算的), 点「安装」才装; 升级多了权限要重新点头。
 * 发布 = 提交到中央「待审核」, 目标部门的主管 (全公司范围由管理员) 审核通过才上架。
 */
import { useEffect, useState } from "react";

import { refreshExperts } from "../../lib/expertChat";
import {
  expertPackInstall, expertPackMarket, expertPackMine, expertPackPreview, expertPackPublish, expertPackWithdraw,
  type HubExpert, type PackInspect, type PackPermissions, type PackPreview,
} from "../../lib/tauri_experts";
import { Overlay, StateTag, muted } from "../Collab/formsUi";
import { Btn, ErrorLine, inputStyle, itemStyle } from "../Collab/roomLinkUi";

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

const STATUS: Record<HubExpert["status"], string> = { pending: "待审核", approved: "已上架", rejected: "被驳回", withdrawn: "已撤回" };

function Row({ label, items, tone }: { label: string; items: string[]; tone?: string }) {
  if (!items.length) return null;
  return (
    <div style={{ fontSize: 12 }}>
      <span style={{ color: tone ?? "var(--catfish-text-muted)", fontWeight: 600 }}>{label}: </span>
      {items.join("、")}
    </div>
  );
}

/** 会用到什么: 查 / 写本机 / 对外 / 网站 / 终端 / 上网。 */
export function PermissionSummary({ p }: { p: Partial<PackPermissions> }) {
  const empty = !p.read?.length && !p.local?.length && !p.outward?.length && !p.sites?.length && !p.code && !p.web;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <Row label="查" items={p.read ?? []} />
      <Row label="写你电脑上的东西" items={p.local ?? []} />
      <Row label="对外 (跑到时会停下等你确认)" items={p.outward ?? []} tone="var(--status-warn, #d97706)" />
      <Row label="会打开的网站" items={p.sites ?? []} />
      {p.code && <div style={{ fontSize: 12, color: "var(--status-error, #dc2626)" }}>⚠️ 要用终端 / 执行代码 / 读写文件</div>}
      {p.web && <div style={{ fontSize: 12 }}>要上网搜索</div>}
      {empty && <span style={muted}>只跑自带的工作流, 不额外用别的能力。</span>}
    </div>
  );
}

/** 装 / 升级前的确认框。source = 从文件 (path) 或从市场 (id + version)。 */
export function InstallDialog({ info, source, onClose, onDone }: {
  info: PackInspect;
  source: { path: string } | { from: "hub"; id: string; version: string };
  onClose: () => void;
  onDone: (msg: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const blocked = info.problems.length > 0 || ["same", "older", "conflict"].includes(info.state);
  const upgrade = info.state === "upgrade";
  const fromFile = "path" in source;

  async function go() {
    setBusy(true);
    setError(null);
    try {
      const r = await expertPackInstall({ ...source, confirmed: true });
      if (r.ok) {
        await refreshExperts();
        onDone(`${info.name} ${r.version} ${r.upgraded ? "已升级" : "装好了"}, 在工作台「找谁」里能找到它`);
      } else if ("error" in r) {
        setError(r.error);
      } else {
        setError("还需要确认, 请再点一次");
      }
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Overlay title={`${upgrade ? "升级" : "安装"}专家 · ${info.name} ${info.version}`} onClose={onClose} width={640}>
      {info.description && <span style={{ fontSize: 13 }}>{info.description}</span>}
      <span style={muted}>
        {fromFile ? "⚠️ 从文件装: 没有中央签名, 确认文件来源可靠再装。" : "✅ 从公司资源市场装: 装之前会核对中央签名。"}
        {upgrade && ` 本机现在是 ${info.installed_version}, 升级保留它的会话和记忆。`}
      </span>
      {upgrade && info.expanded.length > 0 && (
        <div style={{ fontSize: 12, color: "var(--status-warn, #d97706)" }}>这一版多了: {info.expanded.join("; ")}</div>
      )}
      <strong style={{ fontSize: 13 }}>它会用到</strong>
      <PermissionSummary p={info.permissions} />
      {info.routes.length > 0 && (
        <>
          <strong style={{ fontSize: 13 }}>它管的事</strong>
          {info.routes.map((r, i) => <span key={i} style={{ fontSize: 12 }}>· {r.when}</span>)}
        </>
      )}
      <span style={muted}>自带 {info.workflows.length} 个工作流、{info.skills.length} 个技能; 不会装进你自己的技能列表。</span>
      {info.problems.length > 0 && <ErrorLine>这个包没过检查: {info.problems.join("; ")}</ErrorLine>}
      {info.state === "same" && <ErrorLine>已经装了这个版本。</ErrorLine>}
      {info.state === "older" && <ErrorLine>本机装的 {info.installed_version} 比这个新。</ErrorLine>}
      {info.state === "conflict" && <ErrorLine>你自己搭的专家里有一个跟它同 id, 装不了。</ErrorLine>}
      {error && <ErrorLine>{error}</ErrorLine>}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <Btn kind="ghost" disabled={busy} onClick={onClose}>取消</Btn>
        <Btn kind="primary" disabled={busy || blocked} onClick={() => void go()}>
          {busy ? "处理中 (会重启一次小鲶本机服务)…" : upgrade ? "升级" : "安装"}
        </Btn>
      </div>
    </Overlay>
  );
}

/** 发布到中央资源市场 (进待审核)。 */
export function PublishDialog({ id, name, onClose, onDone }: {
  id: string; name: string; onClose: () => void; onDone: (msg: string) => void;
}) {
  const [pre, setPre] = useState<PackPreview | null>(null);
  const [scope, setScope] = useState<"department" | "company">("department");
  const [depts, setDepts] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    expertPackPreview(id).then(setPre).catch((e) => setError(errText(e)));
  }, [id]);

  async function go() {
    if (!pre) return;
    setBusy(true);
    setError(null);
    try {
      const departments = depts.split(/[、,，\s]+/).map((x) => x.trim()).filter(Boolean);
      await expertPackPublish({ id, scope, departments, note, version: pre.version });
      onDone(`${name} ${pre.version} 已提交, 等${scope === "company" ? "管理员" : "部门主管"}审核; 结果在「我提交的」里看`);
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Overlay title={`发布到资源市场 · ${name}`} onClose={onClose} width={620}>
      <span style={muted}>
        打包时会把它用到的工作流、技能 (包括工作流里用到的) 一起打进去, 检查有没有密码 / 员工个人数据、脚本有没有调不该调的工具。
        提交后由{scope === "company" ? "管理员" : "目标部门的主管"}审核, 通过才在市场里出现。
      </span>
      {!pre && !error && <span style={muted}>打包检查中…</span>}
      {pre && (
        <>
          <span style={{ fontSize: 13 }}>版本 {pre.version} · {pre.workflows.length} 个工作流 · {pre.skills.length} 个技能 · {Math.ceil(pre.size / 1024)} KB</span>
          <strong style={{ fontSize: 13 }}>别人装它时会看到它用到</strong>
          <PermissionSummary p={pre.permissions} />
        </>
      )}
      <div style={{ display: "flex", gap: 16, fontSize: 12 }}>
        <label style={{ display: "flex", gap: 4 }}>
          <input type="radio" checked={scope === "department"} onChange={() => setScope("department")} />给部门用
        </label>
        <label style={{ display: "flex", gap: 4 }}>
          <input type="radio" checked={scope === "company"} onChange={() => setScope("company")} />全公司 (管理员审核)
        </label>
      </div>
      {scope === "department" && (
        <input value={depts} onChange={(e) => setDepts(e.target.value)} style={inputStyle}
               placeholder="哪些部门能装, 顿号隔开 (空着 = 你所在的部门)" />
      )}
      <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} style={{ ...inputStyle, resize: "vertical" }}
                placeholder="给审核人的话 (可不填): 它解决什么问题、改了什么" />
      {error && <ErrorLine>{error}</ErrorLine>}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <Btn kind="ghost" disabled={busy} onClick={onClose}>取消</Btn>
        <Btn kind="primary" disabled={busy || !pre} onClick={() => void go()}>{busy ? "提交中…" : "提交审核"}</Btn>
      </div>
    </Overlay>
  );
}

/** 资源市场: 本部门 / 全公司可见的已上架专家。 */
export function MarketSection({ onInstall }: { onInstall: (m: HubExpert) => void }) {
  const [items, setItems] = useState<HubExpert[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    expertPackMarket().then((r) => setItems(r.experts)).catch((e) => setError(errText(e)));
  }, []);
  if (error) return <ErrorLine>{error}</ErrorLine>;
  if (!items) return <span style={muted}>读取资源市场…</span>;
  if (!items.length) return <span style={muted}>市场里还没有你能装的专家。</span>;
  return (
    <>
      {items.map((m) => (
        <div key={m.id} style={itemStyle}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>
              {m.name} <span style={muted}>{m.version}</span>
              {m.can_upgrade && <span style={{ fontSize: 11, color: "var(--catfish-cyan)" }}> · 可更新 (本机 {m.installed_version})</span>}
            </span>
            <span style={muted}>{m.scope === "company" ? "全公司" : m.departments.join("、")} · {m.reviewed_by} 审核</span>
          </div>
          <span style={muted}>{m.description}</span>
          {m.summary && <PermissionSummary p={m.summary} />}
          {m.can_upgrade && (m.expanded?.length ?? 0) > 0 && (
            <span style={{ fontSize: 12, color: "var(--status-warn, #d97706)" }}>新版多了: {m.expanded!.join("; ")}</span>
          )}
          <div>
            {m.local_conflict ? <span style={muted}>你自己搭的专家里有同 id 的, 装不了</span>
              : m.installed_version && !m.can_upgrade ? <span style={muted}>已装</span>
              : <Btn kind="primary" onClick={() => onInstall(m)}>{m.can_upgrade ? "升级" : "安装"}</Btn>}
          </div>
        </div>
      ))}
    </>
  );
}

/** 我提交的: 状态、驳回原因、撤回。 */
export function MineSection() {
  const [items, setItems] = useState<HubExpert[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => expertPackMine().then((r) => setItems(r.experts)).catch((e) => setError(errText(e)));
  useEffect(() => { void load(); }, []);
  if (error) return <ErrorLine>{error}</ErrorLine>;
  if (!items) return <span style={muted}>读取中…</span>;
  if (!items.length) return <span style={muted}>还没提交过。</span>;
  return (
    <>
      {items.map((m) => (
        <div key={`${m.id}@${m.version}`} style={{ ...itemStyle, gap: 4 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
            <span style={{ fontSize: 13 }}>{m.name} <span style={muted}>{m.version}</span></span>
            <StateTag state={STATUS[m.status]} />
          </div>
          <span style={muted}>
            {m.submitted_at ? new Date(m.submitted_at).toLocaleString("zh-CN", { hour12: false }).slice(0, -3) : ""} 提交 · {m.scope === "company" ? "全公司" : m.departments.join("、")}
            {m.reviewed_by ? ` · ${m.reviewed_by} 审` : ""}
          </span>
          {m.status === "rejected" && m.review_note && <ErrorLine>驳回原因: {m.review_note}</ErrorLine>}
          {m.status === "approved" && m.review_note && <span style={muted}>审核意见: {m.review_note}</span>}
          {(m.status === "pending" || m.status === "approved") && (
            <div>
              <Btn kind="ghost" onClick={() => void expertPackWithdraw(m.id, m.version).then(load).catch((e) => setError(errText(e)))}>
                {m.status === "pending" ? "撤回" : "下架"}
              </Btn>
            </div>
          )}
        </div>
      ))}
    </>
  );
}
