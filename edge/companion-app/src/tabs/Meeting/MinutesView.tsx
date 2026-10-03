/**
 * 纪要: 生成 / 展示 / 写回 (10/1)。
 *
 * 写回都要员工点: 勾选的待办 → 任务库 (catfish_create_task, source=meeting, 按
 * 会议+序号去重, 重复点不会出两条); 纪要 → 知识库, 存成一条「文档」实体
 * (catfish_wiki_create, entity_type=doc, 标签「会议纪要」), 知识体系里立刻能看到。
 *
 * 10/1 第一版用的是 catfish_wiki_ingest: 那只是把文件放进 wiki/raw/sources/ 这个原料
 * 收件箱, 知识体系页不显示, 后台过很久才从里面抽人名 / 项目, 纪要本身永远不是一条条目 ——
 * 鸿波点完「存进知识库」在知识体系里找不到, 等于没存。
 *
 * 10/3: 可以选纪要模版 (TemplateManager)。缺省 = 原来的固定格式; 选了自定义模版, 这里
 * 显示按模版写的那份 Markdown, 下面照样列待办给「加入任务库」用 —— 存知识库存的也是它。
 * Word / Excel 模版: 顶上给「打开 Word / Excel」(填好的那张表), 下面显示缺省版过目。
 */
import { useEffect, useState } from "react";

import { Markdown } from "../../lib/markdown";
import { toolBridgeCallTool } from "../../lib/tauri_services";
import {
  DEFAULT_TEMPLATE_ID, meetingMinutes, meetingMinutesGenerate, meetingMinutesOpenFile, meetingTemplatesList,
  type MeetingMeta, type Minutes, type MinutesTemplate,
} from "../../lib/tauri_meeting";
import { Btn, ErrorLine, inputStyle, itemStyle } from "../Collab/roomLinkUi";
import { actionItemToTask, minutesWikiBody, minutesWikiTitle } from "./meetingHelpers";
import { lastTemplateId, rememberTemplateId } from "./minutesTemplates";
import TemplateManager from "./TemplateManager";

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
  const [templates, setTemplates] = useState<MinutesTemplate[]>([]);
  const [templateId, setTemplateId] = useState(DEFAULT_TEMPLATE_ID);
  const [managing, setManaging] = useState(false);

  async function loadTemplates(selectId?: string) {
    try {
      const ts = await meetingTemplatesList();
      setTemplates(ts);
      setTemplateId(selectId && ts.some((t) => t.id === selectId) ? selectId : lastTemplateId(ts));
    } catch (e) {
      setError(String(e));
    }
  }

  useEffect(() => {
    void loadTemplates();
  }, []);

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
      await meetingMinutesGenerate(meta.id, templateId);
      rememberTemplateId(templateId);
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
    const title = minutesWikiTitle(meta.title, meta.created_at.slice(0, 10));
    const res = await toolBridgeCallTool("catfish_wiki_create", {
      kind: "entity",
      subtype: "doc",
      title,
      body: minutesWikiBody(minutes.markdown),
      tags: ["会议纪要"],
    });
    const r = (res.result ?? {}) as { ok?: boolean; error?: string; rel_path?: string };
    if (res.ok && r.ok) setNote(`已存进知识库: 知识体系里搜「${title}」`);
    else if (res.ok && r.error?.includes("已存在")) setNote(`已经存过了: 知识体系里搜「${title}」`);
    else setError(`存知识库失败: ${res.error || r.error || "未知错误"}`);
  }

  const templateName = templates.find((t) => t.id === templateId)?.name;
  const generatingLabel = templateName ? `正在按「${templateName}」生成…` : "正在生成…";

  // 10/3 UI 调整: 模版选择 / 生成 放卡片头上 (生成前的设置), 写回按钮放卡片底下 (生成后的动作);
  // 模版管理是弹窗, 不再把卡片撑开把按钮和转写挤下去。
  const header = (
    <div style={{ display: "flex", alignItems: "center", gap: "var(--space-2)", flexWrap: "wrap" }}>
      <div style={{ fontWeight: 600, fontSize: 14, flex: 1, minWidth: 120 }}>
        纪要
        {minutes?.json.template && (
          <span style={{ marginLeft: 8, fontSize: 11, fontWeight: 400, color: "var(--catfish-text-muted)" }}>
            按「{minutes.json.template.name}」
          </span>
        )}
      </div>
      <select
        aria-label="纪要模版"
        title="纪要模版"
        style={{ ...inputStyle, width: "auto", maxWidth: 200, padding: "4px 8px", fontSize: 12 }}
        value={templateId}
        disabled={generating}
        onChange={(e) => setTemplateId(e.target.value)}
      >
        <option value={DEFAULT_TEMPLATE_ID}>标准纪要</option>
        {templates.map((t) => (
          <option key={t.id} value={t.id}>{t.kind === "docx" ? "Word · " : t.kind === "xlsx" ? "Excel · " : ""}{t.name}</option>
        ))}
      </select>
      <Btn kind={minutes ? "ghost" : "primary"} disabled={generating} onClick={() => void generate()}>
        {generating ? generatingLabel : minutes ? "重新生成" : "生成纪要"}
      </Btn>
      <Btn kind="ghost" onClick={() => setManaging(true)}>管理模版</Btn>
    </div>
  );
  const manager = managing && (
    <TemplateManager templates={templates} onChanged={(id) => void loadTemplates(id)} onClose={() => setManaging(false)} />
  );
  const muted = { color: "var(--catfish-text-muted)" } as const;

  if (!minutes) {
    return (
      <div style={{ ...itemStyle, fontSize: 13, gap: "var(--space-2)" }}>
        {header}
        <div style={{ fontSize: 12, ...muted }}>
          {generating
            ? "长会议要一两分钟, 可以先去干别的。"
            : "先把上面的说话人改成真名再生成, 纪要里的负责人会更准。只有转写文字会发给大模型, 录音不出本机。"}
        </div>
        {manager}
        {error && <ErrorLine>{error}</ErrorLine>}
      </div>
    );
  }

  const m = minutes.json;
  const markdownView = !!m.template && !m.output_file; // 文字模版: 显示按模版写的那份
  const list = (title: string, items: string[]) => (
    <div>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{title}</div>
      {items.map((x, i) => <div key={i} style={{ lineHeight: 1.7 }}>· {x}</div>)}
    </div>
  );
  // 空的几节并成一行灰字, 不再每节占两行写「(无)」
  const empty = [
    !markdownView && m.decisions.length === 0 && "无决议",
    m.action_items.length === 0 && "无待办",
    !markdownView && m.open_questions.length === 0 && "无待定问题",
  ].filter(Boolean);
  const checkedCount = m.action_items.filter((_, i) => checked[i]).length;

  return (
    <div style={{ ...itemStyle, fontSize: 13, gap: "var(--space-3)" }}>
      {header}
      {m.output_file && (
        // Word / Excel 模版: 填好的那张表就是交出去的纪要; 下面照常显示缺省版给员工过目
        <div style={{ display: "flex", gap: "var(--space-2)", alignItems: "center", flexWrap: "wrap", fontSize: 12,
          padding: "8px 10px", borderRadius: "var(--radius-sm)", background: "var(--catfish-bg-cream)" }}>
          <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            📄 {m.output_file.split(/[\\/]/).pop()}
          </span>
          <Btn kind="primary" onClick={() => void meetingMinutesOpenFile(meta.id, false).catch((e) => setError(String(e)))}>
            打开 {m.template?.kind === "xlsx" ? "Excel" : "Word"}
          </Btn>
          <Btn kind="ghost" onClick={() => void meetingMinutesOpenFile(meta.id, true).catch((e) => setError(String(e)))}>
            在文件夹中显示
          </Btn>
        </div>
      )}
      {markdownView ? (
        <Markdown text={minutes.markdown} />
      ) : (
        <>
          <div style={{ lineHeight: 1.75 }}>{m.summary}</div>
          {m.decisions.length > 0 && list("决议", m.decisions)}
        </>
      )}
      {m.action_items.length > 0 && (
        <div>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>待办</div>
          {m.action_items.map((it, i) => (
            <label key={i} style={{ display: "flex", gap: 6, alignItems: "baseline", lineHeight: 1.7 }}>
              <input type="checkbox" checked={!!checked[i]} onChange={(e) => setChecked({ ...checked, [i]: e.target.checked })} />
              <span>
                {it.task}
                {(it.owner || it.due) && (
                  <span style={muted}> ({[it.owner, it.due && `截止 ${it.due}`].filter(Boolean).join(" · ")})</span>
                )}
              </span>
            </label>
          ))}
        </div>
      )}
      {!markdownView && m.open_questions.length > 0 && list("待定问题", m.open_questions)}
      {empty.length > 0 && <div style={{ fontSize: 12, ...muted }}>{empty.join(" · ")}</div>}
      <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap", alignItems: "center",
        borderTop: "1px solid var(--catfish-border)", paddingTop: "var(--space-3)" }}>
        {m.action_items.length > 0 && (
          <Btn kind="primary" disabled={checkedCount === 0} onClick={() => void addTasks()}>
            {checkedCount ? `勾选的 ${checkedCount} 项待办加入任务库` : "勾选待办加入任务库"}
          </Btn>
        )}
        <Btn kind="ghost" onClick={() => void saveWiki()}>存进知识库</Btn>
        {note && <span style={{ fontSize: 12, color: "var(--status-ok)" }}>✓ {note}</span>}
      </div>
      {manager}
      {error && <ErrorLine>{error}</ErrorLine>}
    </div>
  );
}
