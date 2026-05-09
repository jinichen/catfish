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
import { getToken } from "./me";

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

export async function fetchQuotaMe(): Promise<QuotaMe> {
  const token = await getToken();
  const url = `${config.gatewayUrl}/api/quota/me`;
  const resp = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`);
  }
  return (await resp.json()) as QuotaMe;
}
