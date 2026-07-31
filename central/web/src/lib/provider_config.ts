/** 供应商配置 API (8/1, DESIGN-PROVIDER-SPLIT §7).
 *
 * ## key 在这一层的约定
 *
 * **服务端永远不返回 key** —— 只有 key_source 和 key_ok。所以前端也没有
 * "当前 key 是什么"这个状态，编辑时那一格永远是空的：
 *
 *   留空 = 不改（编辑显示名不该动 key）
 *   填了 = 覆盖
 *   显式清空 = 传空串，改回走环境变量
 *
 * "不传"和"传空串"必须分得开，所以 `api_key` 的类型是 `string | undefined`
 * 而不是默认空串 —— 默认空串的话每次编辑别的字段都会把 key 清掉，而且
 * 没有任何提示，要等员工调用失败才发现。
 */

import { api } from "./api";

/** key 从哪来。`none` = 这家还没配 key，用它的模型不可用。 */
export type KeySource = "stored" | "env" | "none";

export interface Provider {
  id: string;
  display_name: string;
  api_base: string | null;
  api_key_env: string | null;
  key_source: KeySource;
  /** key 现在**到底能不能用**：存库的能不能解开 / env 那个变量设没设。
   *
   * 这是管理员最想知道的一件事，而它既不在库里也不在配置里 ——
   * 之前只能等员工调用失败才发现。 */
  key_ok: boolean;
  timeout: number;
  /** 哪些模型在用它。删除拦截和"删了会影响谁"都靠它。 */
  models: string[];
}

export interface ProviderListResponse {
  ok: boolean;
  editable: boolean;
  /** 主密钥配了没。没配就不能存 key —— 界面据此在**保存前**说清楚，
   *  而不是让人填完点保存才撞 400。 */
  secret_key_configured: boolean;
  master_key_env: string;
  providers: Provider[];
}

export interface ProviderInput {
  display_name: string;
  api_base: string | null;
  api_key_env: string | null;
  /** undefined = 不改（见文件头）。 */
  api_key?: string;
  timeout: number;
}

export const providerApi = {
  list: () => api.get<ProviderListResponse>("/api/admin/providers"),
  put: (id: string, body: ProviderInput) =>
    api.put<{ ok: boolean; id: string; created: boolean; warning: string | null }>(
      `/api/admin/providers/${encodeURIComponent(id)}`,
      body,
    ),
  remove: (id: string) =>
    api.delete<{ ok: boolean; id: string; deleted: boolean }>(
      `/api/admin/providers/${encodeURIComponent(id)}`,
    ),
};

export function emptyProvider(): ProviderInput {
  return { display_name: "", api_base: null, api_key_env: null, timeout: 60 };
}

/** 供应商标识的规则，跟后端 _ID_RE 一致。
 *
 * 前后端各写一份是重复，但这一格是**新建时才填、填完不能改**的，
 * 让人填完点保存才被拒是很差的体验。后端那道仍是真正兜底的。
 */
export function validateProviderId(id: string): string | null {
  if (!id.trim()) return "标识不能为空";
  if (!/^[a-z0-9][a-z0-9-]{0,62}$/.test(id))
    return "只能用小写字母、数字、连字符，且以字母或数字开头（它会出现在 URL 和模型配置里）";
  return null;
}

/** key 状态的一句话说明。列表和表单都用，免得两处措辞不一样。 */
export function describeKey(p: Provider, masterKeyEnv: string): string {
  if (p.key_source === "stored")
    return p.key_ok
      ? "已加密存库"
      : `存库了但解不开 —— ${masterKeyEnv} 换过但没跑轮换脚本，或者密文被改坏了`;
  if (p.key_source === "env")
    return p.key_ok
      ? `读环境变量 ${p.api_key_env}`
      : `环境变量 ${p.api_key_env} 在服务器上没设 —— 用它的模型现在每次调用都失败`;
  return "还没配 —— 用它的模型现在不可用";
}
