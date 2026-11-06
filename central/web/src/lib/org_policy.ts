/** 公司级设置 (11/6, docs/GROUP-CHAT-PLAN.md §4.2) —— 下发给员工 Companion 的那几个数。先只有群聊记录保留天数。 */
import { api } from "./api";

export interface OrgPolicy {
  /** 群聊记录在员工电脑上最多保留几天; null = Companion 用默认 180 天 */
  room_retention_days: number | null;
  updated_at: string | null;
  updated_by: string | null;
}

export const RETENTION_MIN = 7;
export const RETENTION_MAX = 3650;
export const RETENTION_DEFAULT = 180;

export const orgPolicyApi = {
  get: () => api.get<OrgPolicy>("/api/org/policy"),
  setRoomRetention: (days: number | null) => api.put<OrgPolicy>("/api/admin/org/policy", { room_retention_days: days }),
};
