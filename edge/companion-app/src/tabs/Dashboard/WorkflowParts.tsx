/**
 * 工作流页的零件 (10/19, 鸿波「这种清单对普通办公人员只能靠猜」):
 *   StepChain   步骤画成一条链, 对外的那步带 🔔 (跑到它会停下等你确认)
 *   ParamsLine  参数一行看完 (搜索关键词: 资质、认证… · 回溯天数: 7)
 *   ScheduleDialog / cronText   定时 (每天 / 工作日 / 每周几 / 每月几号 + 几点), 跟 cron 互转
 *   RunLine     运行记录: 中文名 + 怎么跑的 + 失败说人话 (原始报错收起来) + 做成了什么 + 「让小鲶看看」
 */
import { useEffect, useState } from "react";

import type { ChainStep, ParamSpec, RunSummary } from "../../lib/tauri_workflows";
import { Overlay, muted } from "../Collab/formsUi";
import { Btn, ErrorLine, inputStyle, itemStyle } from "../Collab/roomLinkUi";

export function StepChain({ chain }: { chain: ChainStep[] }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 4, fontSize: 12 }}>
      {chain.map((c, i) => (
        <span key={c.id || i} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          {i > 0 && <span style={{ color: "var(--catfish-text-muted)" }}>→</span>}
          <span
            title={c.outward ? "对外: 跑到这一步会停下, 等你在鲶鱼里确认" : undefined}
            style={{
              padding: "1px 8px", borderRadius: 999, border: "1px solid var(--catfish-border)",
              background: c.outward ? "color-mix(in srgb, var(--status-warn, #d97706) 10%, transparent)" : "var(--catfish-bg)",
              color: c.outward ? "var(--status-warn, #d97706)" : "var(--catfish-text)", whiteSpace: "nowrap",
            }}
          >
            {c.outward ? "🔔 " : ""}{c.label}{c.each ? " (逐条)" : ""}
          </span>
        </span>
      ))}
    </div>
  );
}

function showValue(v: unknown): string {
  if (Array.isArray(v)) {
    const s = v.slice(0, 3).map(String).join("、");
    return v.length > 3 ? `${s}…` : s;
  }
  if (v === null || v === undefined || v === "") return "无";
  return String(v);
}

export function ParamsLine({ params }: { params: ParamSpec[] }) {
  if (!params.length) return null;
  return (
    <span style={muted}>
      {params.map((p) => `${p.label}：${showValue(p.default)}`).join(" · ")}
      <span style={{ marginLeft: 6 }}>(点「编辑」改默认值, 或「运行」时临时换)</span>
    </span>
  );
}

// ── 定时 ─────────────────────────────────────────────────────────

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
type Freq = "daily" | "weekdays" | "weekly" | "monthly";

export function toCron(freq: Freq, time: string, weekday: number, monthday: number): string {
  const [h, m] = time.split(":").map((x) => Number.parseInt(x, 10));
  const hh = Number.isFinite(h) ? h : 9;
  const mm = Number.isFinite(m) ? m : 0;
  if (freq === "daily") return `${mm} ${hh} * * *`;
  if (freq === "weekdays") return `${mm} ${hh} * * 1-5`;
  if (freq === "weekly") return `${mm} ${hh} * * ${weekday}`;
  return `${mm} ${hh} ${monthday} * *`;
}

/** 我们自己建的几种写法翻成中文; 认不出的原样显示 */
export function cronText(expr: string | null | undefined): string {
  const p = (expr ?? "").trim().split(/\s+/);
  if (p.length !== 5) return expr ?? "";
  const [mm, hh, dom, mon, dow] = p;
  if (!/^\d+$/.test(mm) || !/^\d+$/.test(hh) || mon !== "*") return expr ?? "";
  const t = `${hh.padStart(2, "0")}:${mm.padStart(2, "0")}`;
  if (dom === "*" && dow === "*") return `每天 ${t}`;
  if (dom === "*" && dow === "1-5") return `工作日 ${t}`;
  if (dom === "*" && /^[0-6]$/.test(dow)) return `每${WEEKDAYS[Number(dow)]} ${t}`;
  if (/^\d+$/.test(dom) && dow === "*") return `每月 ${dom} 号 ${t}`;
  return expr ?? "";
}

export function ScheduleDialog({ title, onClose, onSave }: {
  title: string;
  onClose: () => void;
  onSave: (cron: string) => Promise<void>;
}) {
  const [freq, setFreq] = useState<Freq>("weekly");
  const [time, setTime] = useState("09:00");
  const [weekday, setWeekday] = useState(1);
  const [monthday, setMonthday] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cron = toCron(freq, time, weekday, monthday);
  const sel = { ...inputStyle, width: "auto", padding: "6px 8px" };
  return (
    <Overlay title={`定时 · ${title}`} onClose={onClose} width={520}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 13 }}>
        <select value={freq} onChange={(e) => setFreq(e.target.value as Freq)} style={sel}>
          <option value="daily">每天</option>
          <option value="weekdays">工作日 (周一到周五)</option>
          <option value="weekly">每周</option>
          <option value="monthly">每月</option>
        </select>
        {freq === "weekly" && (
          <select value={weekday} onChange={(e) => setWeekday(Number(e.target.value))} style={sel}>
            {WEEKDAYS.map((w, i) => <option key={w} value={i}>{w}</option>)}
          </select>
        )}
        {freq === "monthly" && (
          <select value={monthday} onChange={(e) => setMonthday(Number(e.target.value))} style={sel}>
            {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => <option key={d} value={d}>{d} 号</option>)}
          </select>
        )}
        <input type="time" value={time} onChange={(e) => setTime(e.target.value)} style={sel} />
        <span style={muted}>= {cronText(cron)}</span>
      </div>
      <span style={muted}>
        到点小鲶会跑一次这个工作流 (要开着电脑、鲶鱼在运行)。对外的步骤照样停下等你在鲶鱼里确认 ——
        除非你在「编辑」里勾了「对外步骤不用每次确认」。
      </span>
      {error && <ErrorLine>{error}</ErrorLine>}
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <Btn kind="ghost" disabled={busy} onClick={onClose}>取消</Btn>
        <Btn kind="primary" disabled={busy} onClick={() => void (async () => {
          setBusy(true);
          setError(null);
          try {
            await onSave(cron);
            onClose();
          } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
          } finally {
            setBusy(false);
          }
        })()}>{busy ? "设置中…" : "设好"}</Btn>
      </div>
    </Overlay>
  );
}

// ── 运行记录 ─────────────────────────────────────────────────────

const STATUS_COLOR: Record<string, string> = {
  done: "var(--status-ok, #16a34a)", failed: "var(--status-err, #D9534F)", invalid: "var(--status-error, #dc2626)",
  waiting_confirm: "var(--status-warn, #d97706)", declined: "var(--catfish-text-muted)", running: "var(--catfish-cyan)",
};
const STATUS_TEXT: Record<string, string> = {
  done: "跑完了", failed: "没做成", waiting_confirm: "等你确认", declined: "没发", running: "进行中", invalid: "没开跑",
};
const TRIGGER_TEXT: Record<string, string> = { trial: "试跑", ui: "手动", chat: "小鲶跑的" };
const FIX_TEXT: Record<string, string> = {
  conditions: "可以放宽条件再跑", workflow: "工作流要改一下", retry: "从这一步重跑试试", login: "先登录再重跑",
};

export function localTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso.slice(0, 16).replace("T", " ") : d.toLocaleString("zh-CN", { hour12: false }).slice(0, -3);
}

/** 「让小鲶看看」发给小鲶的话: 带上第几步、人话、原始报错和 run_id, 让它能用 catfish_workflow 查这次运行 */
export function askXiaonianPrompt(r: RunSummary): string {
  return [
    `工作流「${r.title.replace(/ \(试跑\)$/, "")}」(${r.skill_path}) 这次运行没做成。`,
    r.error_human ? `情况: ${r.error_human}` : "",
    r.error ? `原始报错: ${r.error}` : "",
    `运行记录: ${r.run_id}`,
    "请用 catfish_workflow 看一下这次运行 (action=status), 用大白话告诉我原因和怎么办;",
    "要改工作流的话先把改法讲给我听, 我同意了再改。",
  ].filter(Boolean).join("\n");
}

/** 删除要点两次 (Tauri 里 window.confirm 不弹), 3 秒没点第二下就复原 */
export function useTwoClick(): [boolean, (act: () => void) => void] {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(false), 3000);
    return () => clearTimeout(t);
  }, [armed]);
  return [armed, (act) => { if (armed) { setArmed(false); act(); } else setArmed(true); }];
}

const LIVE = new Set(["running", "waiting_confirm"]);

export function RunLine({ r, onResume, onAsk, onDelete }: {
  r: RunSummary; onResume: () => void; onAsk: () => void; onDelete?: () => void;
}) {
  const [raw, setRaw] = useState(false);
  const [armed, click] = useTwoClick();
  const trig = r.trigger ? TRIGGER_TEXT[r.trigger] : r.title.endsWith("(试跑)") ? "试跑" : null;
  const name = r.title.replace(/ \(试跑\)$/, "");
  return (
    <div style={{ ...itemStyle, gap: 4 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
        <span style={{ fontSize: 13 }}>
          {name}
          {trig && <span style={{ ...muted, marginLeft: 6, padding: "0 6px", border: "1px solid var(--catfish-border)", borderRadius: 999 }}>{trig}</span>}
        </span>
        <span style={{ display: "flex", gap: 10, alignItems: "baseline", whiteSpace: "nowrap" }}>
          <span style={{ fontSize: 11, color: STATUS_COLOR[r.status] ?? "var(--catfish-text-muted)" }}>
            {STATUS_TEXT[r.status] ?? r.status}
          </span>
          {onDelete && !LIVE.has(r.status) && (
            <button type="button" title={armed ? "再点一次删掉这条记录" : "删除这条运行记录"} onClick={() => click(onDelete)}
                    style={{ ...muted, background: "none", border: "none", padding: 0, cursor: "pointer",
                             color: armed ? "var(--status-err, #D9534F)" : undefined }}>
              {armed ? "再点一次删掉" : "删除"}
            </button>
          )}
        </span>
      </div>
      <span style={muted}>{localTime(r.created_at)}</span>
      {r.status === "done" && r.outcome && <span style={{ fontSize: 12 }}>✅ {r.outcome}</span>}
      {r.status === "waiting_confirm" && r.pending && (
        <span style={{ fontSize: 12, color: "var(--status-warn, #d97706)" }}>停在「{r.pending.label}」—— 鲶鱼页面顶上点确认才继续</span>
      )}
      {r.status === "failed" && (
        <>
          <span style={{ fontSize: 12 }}>{r.error_human ?? `停在 ${r.failed_step}: ${r.error ?? ""}`}</span>
          {r.error_fix && <span style={{ ...muted, color: "var(--status-warn, #d97706)" }}>👉 {FIX_TEXT[r.error_fix]}</span>}
          {r.error_human && r.error && (
            <button type="button" onClick={() => setRaw(!raw)}
                    style={{ ...muted, background: "none", border: "none", padding: 0, cursor: "pointer", textAlign: "left" }}>
              {raw ? `▾ 原始报错: ${r.error}` : "▸ 原始报错 (给 IT 看)"}
            </button>
          )}
          <div style={{ display: "flex", gap: 8 }}>
            <Btn kind="ghost" onClick={onResume}>从这一步重跑</Btn>
            <Btn kind="ghost" onClick={onAsk}>🙋 让小鲶看看</Btn>
          </div>
        </>
      )}
    </div>
  );
}
