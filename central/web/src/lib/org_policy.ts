/** 公司级设置 (11/6, docs/GROUP-CHAT-PLAN.md §4.2) —— 下发给员工 Companion 的那几样: 群聊记录保留天数、本单位邮箱域名 (11/07)。 */
import { api } from "./api";

export interface OrgPolicy {
  /** 群聊记录在员工电脑上最多保留几天; null = Companion 用默认 180 天 */
  room_retention_days: number | null;
  /** 本单位邮箱域名 (11/07): 员工电脑上钓鱼识别判「本单位发来的」以它为准; [] = 各自按员工自己的工作邮箱算 */
  org_mail_domains: string[];
  updated_at: string | null;
  updated_by: string | null;
}

export const RETENTION_MIN = 7;
export const RETENTION_MAX = 3650;
export const RETENTION_DEFAULT = 180;

export const orgPolicyApi = {
  get: () => api.get<OrgPolicy>("/api/org/policy"),
  setRoomRetention: (days: number | null) => api.put<OrgPolicy>("/api/admin/org/policy", { room_retention_days: days }),
  setMailDomains: (domains: string[]) => api.put<OrgPolicy>("/api/admin/org/policy", { org_mail_domains: domains }),
};

/** 输入框里的一段字 → 域名列表: 逗号 / 顿号 / 空格 / 换行都算分隔, 去掉 @ 和大小写, 去重 */
export function splitDomains(text: string): string[] {
  const out: string[] = [];
  for (const part of text.split(/[\s,，、;；]+/)) {
    const d = part.trim().toLowerCase().replace(/^@+/, "").replace(/\.+$/, "");
    if (d && !out.includes(d)) out.push(d);
  }
  return out;
}
