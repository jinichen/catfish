/** 对话卡片里的鲶鱼自定义组件 (A2UI 自定义目录, 11/15)。
 *
 * 目录定义在 tool-bridge/a2ui_catalog.py (CUSTOM_COMPONENTS), 生成 data/a2ui/catfish_catalog_v1.json;
 * 这里的 zod schema 要跟那边的属性一一对应 (lib/a2uiCard.test.ts 对着 JSON 查)。
 * 模型只给数据 (邮件编号 / 标题 / 时间 / 路径), 长什么样、点了干什么都是这里定的:
 *   · EmailCard  —「在邮件里打开」切到邮件页收件箱选中那封 (store.openEmail)
 *   · EventCard  — 只显示
 *   · TodoItem   — 勾选框双向绑数据模型, 按钮提交时随 action 的 context 一起交回
 *   · FileCard   — 点了用系统默认 app 打开本机文件; 只认本机绝对路径, 不认网址
 */
import type { CSSProperties } from "react";
import { z } from "zod"; // 运行时被 vite.config.ts 的别名指到 zod/v3, 跟 @a2ui/* 同一套
import { createComponentImplementation } from "@a2ui/react";
import { DynamicBooleanSchema, DynamicStringListSchema, DynamicStringSchema } from "@a2ui/web_core/v1_0";

import { CalendarBlank, EnvelopeSimple, MapPin, Users } from "@phosphor-icons/react";
import { FileKindIcon, Ico } from "../../components/icons";
import { openFile } from "../../lib/tauri_app";
import { useUIStore } from "../../store/ui";
import { VegaChart } from "./VegaChart";   // 11/16 数据图表 (上游官方扩展 vegachart)

const weight = z.number().optional();

export const EmailCardApi = {
  name: "EmailCard",
  schema: z.object({
    emailId: z.string(),
    subject: DynamicStringSchema,
    sender: DynamicStringSchema,
    date: DynamicStringSchema.optional(),
    snippet: DynamicStringSchema.optional(),
    weight,
  }).strict(),
};

export const EventCardApi = {
  name: "EventCard",
  schema: z.object({
    title: DynamicStringSchema,
    start: DynamicStringSchema,
    end: DynamicStringSchema.optional(),
    location: DynamicStringSchema.optional(),
    attendees: DynamicStringListSchema.optional(),
    weight,
  }).strict(),
};

export const TodoItemApi = {
  name: "TodoItem",
  schema: z.object({
    title: DynamicStringSchema,
    done: DynamicBooleanSchema,
    due: DynamicStringSchema.optional(),
    weight,
  }).strict(),
};

export const FileCardApi = {
  name: "FileCard",
  schema: z.object({
    path: z.string(),
    name: DynamicStringSchema.optional(),
    weight,
  }).strict(),
};

/** 本机绝对路径 (mac / Linux 以 / 开头, Windows 盘符或 \\server); 带 :// 的一律不认 */
export function isLocalPath(p: string): boolean {
  if (!p || p.includes("://") || p.length > 1024) return false;
  return p.startsWith("/") || /^[A-Za-z]:[\\/]/.test(p) || p.startsWith("\\\\");
}

const box: CSSProperties = {
  border: "1px solid var(--catfish-border)", borderRadius: 8, padding: "8px 12px",
  background: "var(--catfish-bg-elevated)", color: "var(--catfish-text)", fontSize: 13, lineHeight: 1.5,
  display: "flex", flexDirection: "column", gap: 2, minWidth: 0,
};
const muted: CSSProperties = { color: "var(--catfish-text-muted)", fontSize: 12 };
const linkBtn: CSSProperties = {
  alignSelf: "flex-start", marginTop: 4, padding: 0, border: "none", background: "none",
  color: "var(--catfish-cyan)", fontSize: 12, cursor: "pointer",
};
const ellipsis: CSSProperties = { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
const str = (v: unknown): string => (v == null ? "" : String(v));
/** 静态字符串属性 (emailId / path): 上游类型把纯字符串也推成 action (string 满足它那个全可选的 ActionLike), 运行时就是原字符串 */
const staticStr = (v: unknown): string => (typeof v === "string" ? v : "");

const EmailCard = createComponentImplementation(EmailCardApi, ({ props }) => (
  <div style={box} data-a2ui="EmailCard">
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <Ico as={EnvelopeSimple} gap={0} />
      <strong style={{ ...ellipsis, flex: 1 }}>{str(props.subject) || "(无主题)"}</strong>
      {props.date ? <span style={muted}>{str(props.date)}</span> : null}
    </div>
    <div style={{ ...muted, ...ellipsis }}>{str(props.sender)}</div>
    {props.snippet ? <div style={{ ...ellipsis }}>{str(props.snippet)}</div> : null}
    <button type="button" style={linkBtn} onClick={() => { const id = staticStr(props.emailId); if (id) useUIStore.getState().openEmail(id); }}>
      在邮件里打开 →
    </button>
  </div>
));

const EventCard = createComponentImplementation(EventCardApi, ({ props }) => {
  const people = Array.isArray(props.attendees) ? props.attendees.map(str).filter(Boolean) : [];
  return (
    <div style={box} data-a2ui="EventCard">
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <Ico as={CalendarBlank} gap={0} />
        <strong style={{ ...ellipsis, flex: 1 }}>{str(props.title)}</strong>
      </div>
      <div style={muted}>{str(props.start)}{props.end ? ` – ${str(props.end)}` : ""}</div>
      {props.location ? <div style={muted}><Ico as={MapPin} size={14} />{str(props.location)}</div> : null}
      {people.length ? <div style={muted}><Ico as={Users} size={14} />{people.join("、")}</div> : null}
    </div>
  );
});

const TodoItem = createComponentImplementation(TodoItemApi, ({ props }) => {
  const done = props.done === true;
  return (
    <label style={{ ...box, flexDirection: "row", alignItems: "center", gap: 8, cursor: "pointer" }} data-a2ui="TodoItem">
      <input type="checkbox" checked={done} onChange={(e) => props.setDone(e.target.checked)} />
      <span style={{ flex: 1, textDecoration: done ? "line-through" : undefined, opacity: done ? 0.6 : 1 }}>{str(props.title)}</span>
      {props.due ? <span style={muted}>{str(props.due)}</span> : null}
    </label>
  );
});

const FileCard = createComponentImplementation(FileCardApi, ({ props }) => {
  const path = staticStr(props.path);
  const ok = isLocalPath(path);
  const name = str(props.name) || path.split(/[\\/]/).filter(Boolean).pop() || path;
  return (
    <div style={{ ...box, flexDirection: "row", alignItems: "center", gap: 8 }} data-a2ui="FileCard" title={path}>
      <FileKindIcon name={name} gap={0} />
      <span style={{ ...ellipsis, flex: 1 }}>{name}</span>
      {ok ? (
        <button type="button" style={{ ...linkBtn, marginTop: 0 }} onClick={() => { void openFile(path).catch(() => {}); }}>
          打开
        </button>
      ) : <span style={muted}>不是本机文件</span>}
    </div>
  );
});

/** 按名字给 A2uiCard.tsx 拼目录用 */
export const CATFISH_CARD_COMPONENTS = [EmailCard, EventCard, TodoItem, FileCard, VegaChart];
