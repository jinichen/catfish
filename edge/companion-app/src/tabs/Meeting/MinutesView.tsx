/**
 * 纪要: 生成 / 展示 / 写回 (10/1)。
 *
 * 写回都要员工点: 勾选的待办 → 任务库 (catfish_create_task, source=meeting, 按
 * 会议+序号去重, 重复点不会出两条); 纪要 → 知识库 (catfish_wiki_ingest, 后台再抽实体)。
 * 两个工具成功的返回形状不一样 (ok:true / type:"success"), 分别判, 不把失败当成功。
 */
import { useEffect, useState } from "react";

import { toolBridgeCallTool } from "../../lib/tauri_services";
import { meetingMinutes, meetingMinutesGenerate, type MeetingMeta, type Minutes } from "../../lib/tauri_meeting";
import { Btn, ErrorLine, itemStyle } from "../Collab/roomLinkUi";
import { actionItemToTask } from "./meetingHelpers";

function toolError(res: { ok: boolean; result: unknown; error: string | null }, success: (r: Record<string, unknown>) => boolean) {
  if (!res.ok) return res.error || "工具调用失败";
  const r = (res.result ?? {}) as Record<string, unknown>;
  return success(r) ? null : String(r.error ?? JSON.stringify(r)).slice(0, 200);
}

export default function MinutesView({ meta }: { meta: MeetingMeta }) {
  const [minutes, setMinutes] = useState<Minutes | null>(null);
  const [generating, setGenerating] = useState(false);
  const [checked, setChecked] = useState<Record<number, boolean>>({});
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setMinutes(null);
    setNote(null);
    meetingMinutes(meta.id).then((m) => {
      setMinutes(m);
      setChecked(Object.fromEntries((m?.json.action_items ?? []).map((_, i) => [i, true])));
    }).catch((e) => setError(String(e)));
  }, [meta.id]);

  async function generate() {
    setGenerating(true);
    setError(null);
    try {
      await meetingMinutesGenerate(meta.id);
      const m = await meetingMinutes(meta.id);
      setMinutes(m);
      setChecked(Object.fromEntries((m?.json.action_items ?? []).map((_, i) => [i, true])));
    } catch (e) {
      setError(String(e));
    } finally {
      setGenerating(false);
    }
  }

  async function addTasks() {
    if (!minutes) return;
    setError(null);
    const items = minutes.json.action_items.map((it, i) => ({ it, i })).filter(({ i }) => checked[i]);
    let ok = 0;
    for (const { it, i } of items) {
      const res = await toolBridgeCallTool("catfish_create_task", actionItemToTask(meta.id, meta.title, i, it));
      const err = toolError(res, (r) => r.ok === true);
      if (err) {
        setError(`「${it.task}」没加进去: ${err}`);
        break;
      }
      ok += 1;
    }
    if (ok) setNote(`已加入任务库 ${ok} 条`);
  }

  async function saveWiki() {
    if (!minutes) return;
    setError(null);
    const res = await toolBridgeCallTool("catfish_wiki_ingest", {
      kept_path: minutes.path,
      filename: `${meta.title}-会议纪要.md`,
      reason: "会议纪要",
    });
    const err = toolError(res, (r) => r.type === "success");
    if (err) setError(`存知识库失败: ${err}`);
    else setNote("已存进知识库 (后台会从里面抽人名 / 项目, 通常当天完成)");
  }

  if (!minutes) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
        <Btn kind="primary" disabled={generating} onClick={() => void generate()}>
          {generating ? "正在生成纪要… (长会议要一两分钟)" : "生成纪要"}
        </Btn>
        <div style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>
          先把说话人改成真名再生成, 纪要里的负责人会更准。只有转写文字会发给大模型, 录音不出本机。
        </div>
        {error && <ErrorLine>{error}</ErrorLine>}
      </div>
    );
  }

  const m = minutes.json;
  const section = (title: string, list: string[]) => (
    <div>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{title}</div>
      {list.length ? list.map((x, i) => <div key={i}>· {x}</div>) : <div style={{ color: "var(--catfish-text-muted)" }}>(无)</div>}
    </div>
  );
  return (
    <div style={{ ...itemStyle, fontSize: 13 }}>
      <div style={{ fontWeight: 600 }}>纪要</div>
      <div>{m.summary}</div>
      {section("决议", m.decisions)}
      <div>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>待办</div>
        {m.action_items.length === 0 && <div style={{ color: "var(--catfish-text-muted)" }}>(无)</div>}
        {m.action_items.map((it, i) => (
          <label key={i} style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
            <input type="checkbox" checked={!!checked[i]} onChange={(e) => setChecked({ ...checked, [i]: e.target.checked })} />
            <span>
              {it.task}
              {(it.owner || it.due) && (
                <span style={{ color: "var(--catfish-text-muted)" }}> ({[it.owner, it.due && `截止 ${it.due}`].filter(Boolean).join(" · ")})</span>
              )}
            </span>
          </label>
        ))}
      </div>
      {section("待定问题", m.open_questions)}
      <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
        <Btn kind="primary" disabled={!Object.values(checked).some(Boolean)} onClick={() => void addTasks()}>勾选的待办加入任务库</Btn>
        <Btn kind="ghost" onClick={() => void saveWiki()}>存进知识库</Btn>
        <Btn kind="ghost" disabled={generating} onClick={() => void generate()}>{generating ? "重新生成中…" : "重新生成"}</Btn>
      </div>
      {note && <div style={{ fontSize: 12, color: "var(--status-ok)" }}>✓ {note}</div>}
      {error && <ErrorLine>{error}</ErrorLine>}
    </div>
  );
}
