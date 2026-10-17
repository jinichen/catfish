/** 一起填表 (10/17, docs/FORM-COLLECT-PLAN.md) —— Rust commands/forms.rs 的前端封装。规则都在 tool-bridge forms.py。 */
import { invoke } from "@tauri-apps/api/core";

export type FieldType = "text" | "longtext" | "number" | "date" | "choice" | "multichoice";

export interface FormField {
  key: string;
  label: string;
  type: FieldType;
  required?: boolean;
  options?: string[];
  hint?: string;
  col?: number;
}

/** 新建 / 改字段时界面上传的: key 可以没有 (tool-bridge 按 label 生成)。 */
export type FieldDraft = Omit<FormField, "key"> & { key?: string };

export type FieldValues = Record<string, string | number | string[]>;

export interface IncomingSummary {
  form_id: string;
  title: string;
  owner: string;
  deadline: string | null;
  state: "待填" | "草稿" | "已提交" | "不填" | "已结束";
  received_at: string | null;
  reminded_at: string | null;
  closed: boolean;
  fields: number;
}

export interface OutgoingSummary {
  form_id: string;
  title: string;
  status: "draft" | "open" | "closed";
  deadline: string | null;
  created_at: string | null;
  progress: Record<string, number>;
  recipients: number;
}

export interface FormList {
  incoming: IncomingSummary[];
  outgoing: OutgoingSummary[];
}

export interface IncomingRecord {
  form_id: string;
  owner: string;
  status: "open" | "closed";
  received_at: string;
  reminded_at?: string;
  remind_text?: string;
  form: { title: string; note: string; deadline: string | null; fields: FormField[] };
  draft?: { values: FieldValues; sources: Record<string, string>; updated_at: string };
  submitted?: { values: FieldValues; at: string };
  declined?: { reason: string; at: string };
}

export interface ProgressRow {
  email: string;
  state: "未发" | "未提交" | "已提交" | "逾期" | "不填";
  at: string | null;
  reason: string | null;
  invited_at: string | null;
  reminded_at: string | null;
}

export interface OutgoingForm {
  form_id: string;
  title: string;
  owner: string;
  deadline: string | null;
  note: string;
  fields: FormField[];
  status: "draft" | "open" | "closed";
  template: string | null;
  recipients: Record<string, { invited_at: string | null; reminded_at: string | null }>;
}

export type FormDetail =
  | { role: "owner"; form: OutgoingForm; progress: { rows: ProgressRow[]; counts: Record<string, number>; total: number } }
  | { role: "recipient"; record: IncomingRecord; state: IncomingSummary["state"] };

export interface SyncResult {
  invite: number;
  response: number;
  remind: number;
  close: number;
  new_invites: { form_id: string; from: string; title: string }[];
}

export interface Person {
  email: string;
  name: string;
  department: string;
}

type Sent = { ok: boolean; failed: { to: string; error: string }[] };

const call = <T>(action: string, params: Record<string, unknown> = {}) => invoke<T>("form_call", { action, params });

export const formList = () => call<FormList>("list");
export const formGet = (formId: string) => call<FormDetail>("get", { form_id: formId });
export const formSync = () => call<SyncResult>("sync");
export const formFromExcel = (path: string) => call<{ title: string; fields: FormField[] }>("from_excel", { path });
export const formCreate = (p: {
  title: string; fields: FieldDraft[]; deadline?: string | null; note?: string; recipients: string[]; template_path?: string | null;
}) => call<{ form: OutgoingForm }>("create", p);
export const formUpdate = (formId: string, p: Partial<{ title: string; fields: FieldDraft[]; deadline: string | null; note: string; recipients: string[] }>) =>
  call<{ form: OutgoingForm }>("update", { form_id: formId, ...p });
export const formSend = (formId: string) => call<Sent & { sent: string[] }>("send", { form_id: formId });
export const formRemind = (formId: string, text = "") => call<Sent & { reminded: string[] }>("remind", { form_id: formId, text });
export const formClose = (formId: string) => call<Sent>("close", { form_id: formId });
export const formExport = (formId: string) => call<{ path: string; rows: number }>("export", { form_id: formId });
export const formDraft = (formId: string, values: FieldValues) => call<{ filled: number }>("draft", { form_id: formId, values });
export const formSubmit = (formId: string, values: FieldValues) =>
  call<{ ok: boolean; problems?: string[]; submitted_at?: string; late?: boolean }>("submit", { form_id: formId, values });
export const formDecline = (formId: string, reason: string) => call<{ ok: boolean }>("decline", { form_id: formId, reason });
export const formDirectory = (q: string) => call<{ people: Person[]; total: number }>("directory", { q, limit: 30 });
