/**
 * 设置 → 技能 →「🧑‍💼 专家」(10/18, docs/EXPERT-PACK-PLAN.md 第一阶段 · 本机专家)。
 *
 * 专家 = 只管某类事的小鲶分身: 自己的人设、只看得到选中的工作流 / 技能和几组能力、知道什么事走哪条路、交付前会核对。
 * 这里搭 / 改 / 删; 「保存并生效」会建 (或更新) 它的 profile 并重启一次小鲶本机服务。搭好了在工作台「找谁」里找它。
 * 第二阶段 (10/18): 导出 / 发布到资源市场 / 从文件或市场安装 / 升级 / 卸载 (零件在 ExpertPackUi.tsx)。
 */
import { useEffect, useState } from "react";

import { refreshExperts, selectExpert } from "../../lib/expertChat";
import {
  expertApply, expertCatalog, expertGet, expertList, expertPackExport, expertPackInspect, expertPackInstall, expertPackPickFile,
  expertRemove, expertSave,
  type ExpertCatalog, type ExpertDef, type ExpertRoute, type ExpertSummary, type HubExpert, type PackInspect,
} from "../../lib/tauri_experts";
import { useUIStore } from "../../store/ui";
import { Overlay, muted } from "../Collab/formsUi";
import { Btn, ErrorLine, cardStyle, inputStyle, itemStyle } from "../Collab/roomLinkUi";
import { InstallDialog, MarketSection, MineSection, PublishDialog } from "./ExpertPackUi";

type InstallSource = { path: string } | { from: "hub"; id: string; version: string };

const blank = (): ExpertDef => ({
  id: "", name: "", description: "", persona: "", quick_prompts: [], workflows: [], skills: [],
  routes: [{ when: "", use: "" }], checks: [], tool_groups: [], allow_web: false, allow_code: false, model: "inherit", enabled: true,
});

const small = { ...inputStyle, padding: "4px 8px", fontSize: 12 };

function routeLabel(use: string): string {
  if (use === "knowledge") return "查知识库回答";
  if (use === "decline") return "不接 (请回小鲶)";
  return use.replace(/^workflow:/, "工作流 · ").replace(/^skill:/, "技能 · ");
}

function Editor({ initial, isNew, cat, onClose, onSaved }: {
  initial: ExpertDef; isNew: boolean; cat: ExpertCatalog; onClose: () => void; onSaved: () => void;
}) {
  const [d, setD] = useState<ExpertDef>(initial);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const set = (p: Partial<ExpertDef>) => setD({ ...d, ...p });
  const toggle = (key: "workflows" | "skills" | "tool_groups", v: string) =>
    set({ [key]: d[key].includes(v) ? d[key].filter((x) => x !== v) : [...d[key], v] } as Partial<ExpertDef>);
  const uses = [...d.workflows.map((w) => `workflow:${w}`), ...d.skills.map((s) => `skill:${s}`), "knowledge", "decline"];
  const setRoute = (i: number, p: Partial<ExpertRoute>) => set({ routes: d.routes.map((r, j) => (j === i ? { ...r, ...p } : r)) });

  async function saveApply() {
    setBusy(true);
    setError(null);
    setMsg("保存中…");
    try {
      const { expert } = await expertSave(d);
      setD(expert); // 新建时 id 是保存那一刻生成的; 记回来, 生效失败再点一次不会又建一个
      setMsg("让它生效 (会重启一次小鲶本机服务, 正在进行的对话会中断)…");
      await expertApply(expert.id);
      await refreshExperts();
      setMsg("好了, 在工作台「找谁」里能找到它");
      onSaved();
    } catch (e) {
      setMsg(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Overlay title={isNew ? "新建专家" : `编辑 · ${initial.name}`} onClose={onClose} width={820}>
      {/* 10/18: id 不让员工填 —— 新建时保存那一刻自动生成 (内部目录名), 只填名字 */}
      <input value={d.name} onChange={(e) => set({ name: e.target.value })} placeholder="名字, 例「资质专员」" style={inputStyle} />
      <input value={d.description} onChange={(e) => set({ description: e.target.value })}
             placeholder="一句话: 它管什么 (小鲶靠这句话决定什么时候推荐它)" style={inputStyle} />
      <textarea rows={4} value={d.persona} onChange={(e) => set({ persona: e.target.value })} style={{ ...inputStyle, resize: "vertical" }}
                placeholder="人设 (可不填): 我是谁、说话风格、不碰什么。不填就用名字 + 一句话生成。选路和核对会自动接在后面。" />

      <strong style={{ fontSize: 13 }}>它用哪些工作流 / 技能</strong>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4, maxHeight: 200, overflowY: "auto" }}>
        {cat.workflows.map((w) => (
          <label key={w.skill_path} style={{ fontSize: 12, display: "flex", gap: 6 }} title={w.description}>
            <input type="checkbox" checked={d.workflows.includes(w.skill_path)} onChange={() => toggle("workflows", w.skill_path)} />
            🔗 {w.skill_path}
          </label>
        ))}
        {cat.skills.map((s) => (
          <label key={s.skill_path} style={{ fontSize: 12, display: "flex", gap: 6 }} title={s.description}>
            <input type="checkbox" checked={d.skills.includes(s.skill_path)} onChange={() => toggle("skills", s.skill_path)} />
            {s.runnable ? "⚙️" : "📄"} {s.skill_path}{s.runnable ? "" : " (说明型)"}
          </label>
        ))}
        {cat.workflows.length + cat.skills.length === 0 && <span style={muted}>还没有工作流或技能。先去「工作流」tab 或跟小鲶教一个。</span>}
      </div>

      <strong style={{ fontSize: 13 }}>选路: 什么事走哪条路</strong>
      {d.routes.map((r, i) => (
        <div key={i} style={{ display: "grid", gridTemplateColumns: "2fr 1.5fr 1.5fr auto", gap: 6 }}>
          <input value={r.when} onChange={(e) => setRoute(i, { when: e.target.value })} placeholder="员工要…, 例「审资质待办」" style={small} />
          <select value={r.use} onChange={(e) => setRoute(i, { use: e.target.value })} style={small}>
            <option value="">走哪条路…</option>
            {uses.map((u) => <option key={u} value={u}>{routeLabel(u)}</option>)}
          </select>
          <input value={(r.ask ?? []).join("、")} onChange={(e) => setRoute(i, { ask: e.target.value.split(/[、,，]/).map((x) => x.trim()).filter(Boolean) })}
                 placeholder="缺了先问 (可不填), 顿号隔开" style={small} />
          <Btn kind="ghost" onClick={() => set({ routes: d.routes.filter((_, j) => j !== i) })}>删</Btn>
        </div>
      ))}
      <div><Btn kind="ghost" onClick={() => set({ routes: [...d.routes, { when: "", use: "" }] })}>+ 加一条</Btn></div>

      <strong style={{ fontSize: 13 }}>交付前核对 (可不填)</strong>
      {d.checks.map((c, i) => (
        <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 2fr auto", gap: 6 }}>
          <select value={c.after} onChange={(e) => set({ checks: d.checks.map((x, j) => (j === i ? { ...x, after: e.target.value } : x)) })} style={small}>
            <option value="">每次交付前</option>
            {d.workflows.map((w) => <option key={w} value={w}>跑完 {w} 后</option>)}
          </select>
          <input value={c.verify} onChange={(e) => set({ checks: d.checks.map((x, j) => (j === i ? { ...x, verify: e.target.value } : x)) })}
                 placeholder="要核对什么, 例「每条都写明通过 / 退回和原因」" style={small} />
          <Btn kind="ghost" onClick={() => set({ checks: d.checks.filter((_, j) => j !== i) })}>删</Btn>
        </div>
      ))}
      <div><Btn kind="ghost" onClick={() => set({ checks: [...d.checks, { after: "", verify: "" }] })}>+ 加一条核对</Btn></div>

      <strong style={{ fontSize: 13 }}>还能用哪些能力 (工作流里的步骤不用勾, 会自动带上)</strong>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
        {cat.tool_groups.map((g) => (
          <label key={g.id} style={{ fontSize: 12, display: "flex", gap: 4 }}>
            <input type="checkbox" checked={d.tool_groups.includes(g.id)} onChange={() => toggle("tool_groups", g.id)} />{g.label}
          </label>
        ))}
        <label style={{ fontSize: 12, display: "flex", gap: 4 }}>
          <input type="checkbox" checked={d.allow_web} onChange={(e) => set({ allow_web: e.target.checked })} />上网搜索
        </label>
        <label style={{ fontSize: 12, display: "flex", gap: 4 }} title="默认关: 专家只做选中的事">
          <input type="checkbox" checked={d.allow_code} onChange={(e) => set({ allow_code: e.target.checked })} />终端 / 执行代码 / 读写文件 (一般不需要)
        </label>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 8 }}>
        <input value={d.model === "inherit" ? "" : d.model} onChange={(e) => set({ model: e.target.value.trim() || "inherit" })}
               placeholder="模型: 空着 = 跟你当前选的一样" style={small} />
        <textarea rows={2} value={d.quick_prompts.join("\n")} onChange={(e) => set({ quick_prompts: e.target.value.split("\n").map((x) => x.trim()).filter(Boolean) })}
                  placeholder="快捷问法, 一行一句 (打开它时显示, 点一下就发)" style={{ ...small, resize: "vertical" }} />
      </div>
      <label style={{ fontSize: 12, display: "flex", gap: 6 }}>
        <input type="checkbox" checked={d.enabled} onChange={(e) => set({ enabled: e.target.checked })} />启用 (关掉就不出现在「找谁」里)
      </label>

      {error && <ErrorLine>{error}</ErrorLine>}
      {msg && <span style={muted}>{msg}</span>}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <Btn kind="ghost" disabled={busy} onClick={onClose}>取消</Btn>
        <Btn kind="primary" disabled={busy || !d.name.trim()} onClick={() => void saveApply()}>{busy ? "处理中…" : "保存并生效"}</Btn>
      </div>
    </Overlay>
  );
}

export default function ExpertsCard() {
  const [list, setList] = useState<ExpertSummary[]>([]);
  const [cat, setCat] = useState<ExpertCatalog | null>(null);
  const [editing, setEditing] = useState<{ def: ExpertDef; isNew: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [installing, setInstalling] = useState<{ info: PackInspect; source: InstallSource } | null>(null);
  const [publishing, setPublishing] = useState<ExpertSummary | null>(null);
  const [panel, setPanel] = useState<"market" | "mine" | null>(null);
  const [marketKey, setMarketKey] = useState(0);
  const setTab = useUIStore((s) => s.setActiveTab);
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));

  async function installFromFile() {
    const path = await expertPackPickFile();
    if (!path) return;
    try {
      setInstalling({ info: await expertPackInspect(path), source: { path } });
    } catch (e) {
      fail(e);
    }
  }

  async function installFromHub(m: HubExpert) {
    setBusy(m.id);
    setError(null);
    try {
      const source = { from: "hub" as const, id: m.id, version: m.version };
      const r = await expertPackInstall(source);
      if (r.ok) {
        await refreshExperts();
        setNotice(`${m.name} ${r.version} 已升级`);
        await load();
        setMarketKey((k) => k + 1);
      } else if ("need_confirm" in r) {
        setInstalling({ info: r.inspect, source });
      } else {
        setError(r.error);
      }
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }

  async function exportPack(e: ExpertSummary) {
    setError(null);
    try {
      const r = await expertPackExport(e.id);
      if (r) {
        setNotice(`已导出 ${r.path} (版本 ${r.version})`);
        await load();
      }
    } catch (err) {
      fail(err);
    }
  }

  const load = async () => {
    try {
      const [l, c] = await Promise.all([expertList(), expertCatalog()]);
      setList(l.experts);
      setCat(c);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  useEffect(() => { void load(); }, []);

  return (
    <div style={{ ...cardStyle, height: "auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
        <div>
          <h3 style={{ margin: 0, fontSize: "var(--text-md)", fontWeight: 600 }}>🧑‍💼 专家</h3>
          <div style={{ ...muted, marginTop: 4 }}>
            把你的几个工作流 / 技能交给一个「只管这类事」的小鲶分身: 它有自己的人设, 只看得到你选的东西, 知道什么事走哪条路。
            搭好了在工作台对话框上方「找谁」里找它; 小鲶遇到归它管的事也会推荐你切过去。
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
          <Btn kind="ghost" onClick={() => void installFromFile()}>从文件安装</Btn>
          <Btn kind="primary" disabled={!cat} onClick={() => setEditing({ def: blank(), isNew: true })}>新建专家</Btn>
        </div>
      </div>
      {error && <ErrorLine>{error}</ErrorLine>}
      {notice && <span style={{ fontSize: 12, color: "var(--status-ok, #16a34a)" }}>{notice}</span>}
      {list.length === 0 && <span style={muted}>还没有专家。</span>}
      {list.map((e) => (
        <div key={e.id} style={itemStyle}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
            <span style={{ fontSize: 13, fontWeight: 600 }}>{e.name}{e.enabled ? "" : " (已停用)"}</span>
            <span style={muted}>{e.workflows} 个工作流 · {e.skills} 个技能 · {e.routes} 条选路</span>
          </div>
          <span style={muted}>{e.description}</span>
          {e.pack && (
            <span style={{ fontSize: 11, color: e.pack.signed ? "var(--status-ok, #16a34a)" : "var(--status-warn, #d97706)" }}>
              📦 专家包 {e.pack.version} · {e.pack.signed ? `✅ 中央已签名${e.pack.reviewed_by ? ` (${e.pack.reviewed_by} 审核)` : ""}` : "⚠️ 未签名 (从文件装的)"}
            </span>
          )}
          {!e.pack && e.packed_version && <span style={muted}>上次打包 {e.packed_version}</span>}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {!e.pack && (
              <Btn kind="ghost" disabled={!!busy} onClick={() => void (async () => {
                try {
                  setEditing({ def: await expertGet(e.id), isNew: false });
                } catch (err) {
                  setError(String(err));
                }
              })()}>编辑</Btn>
            )}
            <Btn kind="ghost" disabled={!!busy || !e.enabled} onClick={() => {
              selectExpert(e.profile_id);
              setTab("chat");
              window.dispatchEvent(new CustomEvent("catfish:new-chat"));
            }}>去工作台找它</Btn>
            {!e.pack && <Btn kind="ghost" disabled={!!busy} onClick={() => void exportPack(e)}>导出</Btn>}
            {!e.pack && <Btn kind="ghost" disabled={!!busy} onClick={() => setPublishing(e)}>发布到资源市场</Btn>}
            <Btn kind="ghost" disabled={!!busy} onClick={() => void (async () => {
              setBusy(e.id);
              try {
                await expertRemove(e.id);
                await refreshExperts();
                await load();
                setMarketKey((k) => k + 1);
              } catch (err) {
                setError(err instanceof Error ? err.message : String(err));
              } finally {
                setBusy(null);
              }
            })()}>{busy === e.id ? "处理中…" : e.pack ? "卸载" : "删除"}</Btn>
          </div>
        </div>
      ))}
      <div style={{ display: "flex", gap: 8 }}>
        <Btn kind={panel === "market" ? "primary" : "ghost"} onClick={() => setPanel(panel === "market" ? null : "market")}>资源市场</Btn>
        <Btn kind={panel === "mine" ? "primary" : "ghost"} onClick={() => setPanel(panel === "mine" ? null : "mine")}>我提交的</Btn>
      </div>
      {panel === "market" && <MarketSection key={marketKey} onInstall={(m) => void installFromHub(m)} />}
      {panel === "mine" && <MineSection />}
      {installing && (
        <InstallDialog info={installing.info} source={installing.source} onClose={() => setInstalling(null)}
                       onDone={(msg) => { setInstalling(null); setNotice(msg); void load(); setMarketKey((k) => k + 1); }} />
      )}
      {publishing && (
        <PublishDialog id={publishing.id} name={publishing.name} onClose={() => setPublishing(null)}
                       onDone={(msg) => { setPublishing(null); setNotice(msg); void load(); }} />
      )}
      {editing && cat && (
        <Editor initial={editing.def} isNew={editing.isNew} cat={cat} onClose={() => setEditing(null)}
                onSaved={() => { void load(); }} />
      )}
    </div>
  );
}
