/** Quota 接入 —— 五一 sprint 5/3 BL-D9 收尾.
 *
 * 调 gateway GET /api/quota/me, 返当前用户三维 quota:
 *   - per-user 1 minute
 *   - per-user 1 day
 *   - per-department 1 day
 *
 * limit=0 表示不限 (内网 LLM + 内部员工常态).
 */

import { gatewayGetDevToken } from "./tauri";
import { config } from "./env";

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

let _cachedToken: string | null = null;

async function getToken(): Promise<string> {
  if (_cachedToken) return _cachedToken;
  try {
    const t = await gatewayGetDevToken();
    _cachedToken = t;
    return t;
  } catch {
    return "dev-token-local"; // 跟 chat.ts 一致, fallback 让 gateway 走匿名/dev 兼容
  }
}

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
