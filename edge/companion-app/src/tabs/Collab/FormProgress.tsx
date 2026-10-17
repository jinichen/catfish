/**
 * 发起人看进度 (10/17, docs/FORM-COLLECT-PLAN.md §4)。
 *
 * 每个收件人一行: 未发 / 未提交 / 已提交 / 逾期 / 不填 (原因)。「催一下」只催还没回的;
 * 「结束收集」通知所有人。导出 Excel 落到 ~/.catfish/outputs/<日期>/; 「让小鲶汇总」开工作台对话, 全在本机。
 */
import { useCallback, useEffect, useState } from "react";

import { summaryChatKey, summaryPrompt } from "../../lib/formPrompts";
import { refreshForms } from "../../lib/formsStore";
import { openFile, revealInFinder } from "../../lib/tauri_app";
import {
  formClose, formExport, formGet, formRemind, formSend, formUpdate,
  type FormDetail,
} from "../../lib/tauri_forms";
import { useUIStore } from "../../store/ui";
import { Overlay, StateTag, muted } from "./formsUi";
import { Btn, ErrorLine, inputStyle } from "./roomLinkUi";

type Owner = Extract<FormDetail, { role: "owner" }>;

/** initialError: 刚从「发起填表」过来、发送失败的原因 (那个弹层已经关了)。 */
export default function FormProgress({ formId, onClose, initialError }: {
  formId: string;
  onClose: () => void;
  initialError?: string;
}) {
  const [d, setD] = useState<Owner | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(initialError ?? null);
  const [info, setInfo] = useState<string | null>(null);
  const [exported, setExported] = useState<string | null>(null);
  const [more, setMore] = useState("");
  const [remindText, setRemindText] = useState("");
  const startChat = useUIStore((s) => s.startEmailChat);

  const load = useCallback(async () => {
    await refreshForms();   // 先取一次邮筒, 刚回来的回答也算上
    const r = await formGet(formId);
    if (r.role === "owner") setD(r);
  }, [formId]);

  useEffect(() => {
    load().catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [load]);

  async function run(fn: () => Promise<string | void>) {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const msg = await fn();
      if (msg) setInfo(msg);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!d) {
    return <Overlay title="填表进度" onClose={onClose}>{error ? <ErrorLine>{error}</ErrorLine> : <span style={muted}>读取中…</span>}</Overlay>;
  }
  const { form, progress } = d;
  const unsent = progress.rows.filter((r) => r.state === "未发").length;
  const waiting = progress.rows.filter((r) => r.state === "未提交").length;
  const failedNote = (failed: { to: string }[]) => (failed.length ? `; ${failed.length} 人没发出去: ${failed.map((f) => f.to).join("、")}` : "");

  return (
    <Overlay title={`《${form.title}》的进度`} onClose={onClose} width={720}>
      <span style={muted}>
        {form.status === "closed" ? "已结束收集" : form.status === "draft" ? "还没发出去" : "收集中"}
        {form.deadline ? ` · ${form.deadline} 截止` : ""} · {form.fields.length} 项 ·
        {" "}{Object.entries(progress.counts).map(([k, v]) => `${k} ${v}`).join(" · ") || "还没加人"}
      </span>

      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {progress.rows.map((r) => (
          <div key={r.email} style={{ display: "grid", gridTemplateColumns: "2fr 1fr 2fr", gap: 8, fontSize: 13, alignItems: "baseline" }}>
            <span>{r.email}</span>
            <StateTag state={r.state} />
            <span style={muted}>
              {r.at ? r.at.slice(0, 16).replace("T", " ") : ""}{r.reason ? ` · ${r.reason}` : ""}{r.reminded_at && !r.at ? " · 催过" : ""}
            </span>
          </div>
        ))}
      </div>

      {form.status !== "closed" && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 8 }}>
          <input value={more} onChange={(e) => setMore(e.target.value)} placeholder="再加人: 邮箱, 多个用逗号隔开" style={inputStyle} />
          <Btn kind="ghost" disabled={busy || !more.trim()} onClick={() => run(async () => {
            await formUpdate(formId, { recipients: [...Object.keys(form.recipients), ...more.split(/[\s,;，；]+/).filter(Boolean)] });
            setMore("");
            return "加上了, 点「发给没发过的人」发出去";
          })}>加人</Btn>
        </div>
      )}
      {form.status !== "closed" && unsent > 0 && (
        <div><Btn kind="primary" disabled={busy} onClick={() => run(async () => {
          const r = await formSend(formId);
          return `发出去 ${r.sent.length} 人${failedNote(r.failed)}`;
        })}>发给没发过的人 ({unsent})</Btn></div>
      )}
      {form.status === "open" && waiting > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 8 }}>
          <input value={remindText} onChange={(e) => setRemindText(e.target.value)} placeholder="催填附言 (可不填)" style={inputStyle} />
          <Btn kind="ghost" disabled={busy} onClick={() => run(async () => {
            const r = await formRemind(formId, remindText);
            setRemindText("");
            return `催了 ${r.reminded.length} 人${failedNote(r.failed)}`;
          })}>催一下 ({waiting})</Btn>
        </div>
      )}

      {error && <ErrorLine>{error}</ErrorLine>}
      {info && <div style={{ fontSize: 12, color: "var(--status-ok, #16a34a)" }}>{info}</div>}
      {exported && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12 }}>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{exported}</span>
          <Btn kind="ghost" onClick={() => void openFile(exported)}>打开</Btn>
          <Btn kind="ghost" onClick={() => void revealInFinder(exported)}>在文件夹里显示</Btn>
        </div>
      )}

      <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
        <div style={{ display: "flex", gap: 8 }}>
          <Btn kind="ghost" disabled={busy} onClick={() => run(async () => {
            const r = await formExport(formId);
            setExported(r.path);
            return `导出了 ${r.rows} 份回答`;
          })}>导出 Excel</Btn>
          <Btn kind="ghost" disabled={busy} onClick={() => { startChat(summaryChatKey(formId), summaryPrompt(form)); onClose(); }}>
            🐟 让小鲶汇总
          </Btn>
        </div>
        {form.status === "open" && (
          <Btn kind="ghost" disabled={busy} onClick={() => run(async () => {
            const r = await formClose(formId);
            return `已结束收集${failedNote(r.failed)}`;
          })}>结束收集</Btn>
        )}
      </div>
    </Overlay>
  );
}
