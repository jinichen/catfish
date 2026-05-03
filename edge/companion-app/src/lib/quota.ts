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
import { getOverrideToken } from "./me";

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

let _cachedEnvToken: string | null = null;

async function getToken(): Promise<string> {
  // 切换器优先
  const override = getOverrideToken();
  if (override) return override;
  // .env 兜底
  if (_cachedEnvToken) return _cachedEnvToken;
  try {
    const t = await gatewayGetDevToken();
    _cachedEnvToken = t;
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
