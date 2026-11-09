/** 群里发表 (11/09) —— 纯函数: 表卡下面每人的状态, 跟 roomTasks.cardStates 同一个做法。界面在 RoomFormMessage.tsx。 */
import type { RoomFormState, RoomMember, RoomMessage } from "../../lib/tauri_colleague_rooms";

export type FormPersonState = "waiting" | "submitted" | "declined";
export interface FormPerson { email: string; name: string; state: FormPersonState }

const display = (m: RoomMember) => m.name || m.email.split("@")[0];

/** 这张表每个收件人最近报的状态 (没报过 = 还没交); closed = 发起人结束收集了 */
export function formStates(formId: string, recipients: string[], messages: RoomMessage[], members: RoomMember[]): {
  people: FormPerson[]; closed: boolean;
} {
  const last = new Map<string, RoomFormState>();
  let closed = false;
  for (const m of messages) {
    if (m.kind !== "form_status" || m.form_status?.form_id !== formId || !m.from) continue;
    if (m.form_status.state === "closed") closed = true;
    else last.set(m.from, m.form_status.state);
  }
  const people = recipients.map((email) => {
    const mem = members.find((x) => x.email === email);
    return { email, name: mem ? display(mem) : email.split("@")[0], state: (last.get(email) ?? "waiting") as FormPersonState };
  });
  return { people, closed };
}

export const FORM_STATE_WORD: Record<FormPersonState, string> = { waiting: "还没交", submitted: "已交", declined: "不填" };

/** 表卡在眼前时, 它的状态行不再单独显示 (跟任务状态一样) */
export function hiddenFormStatus(messages: RoomMessage[]): Set<string> {
  return new Set(messages.filter((m) => m.kind === "form" && m.form).map((m) => m.form!.form_id));
}
