/**
 * 与 catfish-gateway /v1/catalog 响应对齐。
 * 字段都是公开信息（display_name / tier / 能力开关 / 状态），不含 api_base / UUID。
 *
 * 状态三态由 (api_key_configured, is_reachable) 组合决定：
 *   绿 ok     api_key_configured && is_reachable === true
 *   黄 warn   api_key_configured && is_reachable !== true
 *   灰 idle   !api_key_configured
 */

export type ModelTier = "private" | "public";

export interface CatalogModel {
  id: string;
  display_name: string;
  tier: ModelTier;
  recommended_for?: string;
  context_window: number;
  cost_tier?: string;
  supports_tool_use: boolean;
  supports_vision: boolean;
  /** API key 环境变量是否配了 */
  api_key_configured: boolean;
  /** 上游 TCP 是否通（启动时探的缓存）；null = gateway 还没探 */
  is_reachable: boolean | null;
  /** 给 UI 显示的人话 */
  status_reason: string;
}

export interface CatalogResponse {
  authenticated: boolean;
  models: CatalogModel[];
  default: string | null;
  your_dept_default: string | null;
}
