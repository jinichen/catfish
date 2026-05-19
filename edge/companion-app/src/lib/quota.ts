/** Quota 接入 —— 五一 sprint 5/3 BL-D9 收尾.
 *
 * 调 gateway GET /api/quota/me, 返当前用户三维 quota:
 *   - per-user 1 minute
 *   - per-user 1 day
 *   - per-department 1 day
 *
 * limit=0 表示不限 (内网 LLM + 内部员工常态).
 */

import { config } from "./env";
import { fetchWithAuth } from "./me";

export interface QuotaWindow {
  used: number;
  limit: number; // 0 = 不限
}

export interface QuotaMe {
  user_email: string;
  department: string;
  minute: QuotaWindow;
  day: QuotaWindow;
  department_day: QuotaWindow;
}

// BL-FIX35 (5/10): 删本文件自己的 getToken (第 3 份 copy-paste, 漏 OAuth
// keychain → 配额卡 401). 改用 me.ts 的统一 getToken (OAuth 优先, dev_token
// 兜底). 全 app token 链路从此一处定义.
//
// BL-AUTH-DECOUPLE-A5 Phase 2 (5/19): 改走 fetchWithAuth — hermes 路径 + OAuth
// 路径分支 wrapper 内部处理. URL 也走 backendUrl (hermes proxy 转发 /api/quota/me).
export async function fetchQuotaMe(): Promise<QuotaMe> {
  const url = `${config.backendUrl}/api/quota/me`;
  const resp = await fetchWithAuth(url);
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`);
  }
  return (await resp.json()) as QuotaMe;
}
