/** 模型配置管理 (7/30) — /api/admin/models/* 的前端调用.
 *
 * 跟已有的两处模型相关接口区分清楚, 三者不是一回事:
 *
 *   /v1/catalog          匿名, 只返展示字段 (名字/能力), 给聊天页选模型用
 *   /api/v1/models       员工可见的模型列表 (经 RBAC 过滤)
 *   /api/admin/models    **本文件**. sysadmin only, 返完整配置含 upstream
 *                        (api_base / api_key_env 这类部署细节)
 *
 * 之所以要单独一套: 前两个都不能暴露 upstream —— 那里面是内网地址和
 * 环境变量名。而管理界面必须能看能改。
 */

import { api } from "./api";

/** 上游 LLM 端点. 对应后端 UpstreamConfig. */
export interface Upstream {
  /** 必须带 provider 前缀, 如 openai/qwen-plus、gemini/gemini-2.5-pro */
  model: string;
  /** 只有 OpenAI 兼容的自建端点才需要填 */
  api_base?: string | null;
  /** 读哪个环境变量拿 key —— **key 本身不进配置**, 只存变量名 */
  api_key_env: string;
  /** 强制覆盖的请求参数, 客户端传什么都会被替换 */
  param_overrides?: Record<string, unknown>;
  timeout?: number;
}

/** fallback 链. 空 chain = 不 fallback. */
export interface Fallback {
  on_errors?: (number | string)[];
  chain?: string[];
  max_hops?: number;
}

/** 上游限速 (preflight 用) */
export interface RateLimits {
  tpm?: number | null;
  rpm?: number | null;
  rpd?: number | null;
  tpd?: number | null;
  tier?: string | null;
}

/** 一个模型的完整配置. 对应后端 ModelConfig. */
export interface ModelConfig {
  name: string;
  tier: "private" | "public";
  display_name: string;
  mode: "chat" | "embedding";
  default: boolean;
  upstream: Upstream;
  context_window?: number | null;
  max_output_tokens?: number | null;
  supports_tool_use: boolean;
  supports_streaming: boolean;
  supports_vision: boolean;
  recommended_for: string[];
  cost_tier: "free" | "paid";
  fallback?: Fallback | null;
  rate_limits?: RateLimits | null;
  // 7/30: 展示与计价. 原本硬编码在 lib/modelDisplay.ts —— 模型能在界面上
  // 增删改之后, 那样会让新加的模型显示"未知模型"、成本按兜底价 0.001 算,
  // 而真实单价跨度 0.00005~0.0218 差 400 倍。
  price_per_1k_tokens?: number | null;
  color?: string | null;
  dot_emoji?: string | null;
}

export interface ModelListResponse {
  ok: boolean;
  /** false = 没配库, 模型来自 models.yaml, 只读 —— 界面要据此禁用编辑 */
  editable: boolean;
  revision: number | null;
  /** 失败切换全局开关 (yaml auto_fallback 或 env CATFISH_AUTO_FALLBACK)。
   *  **默认是关的**。关着的时候界面必须说出来 —— 否则管理员会认真配一条
   *  fallback 链, 而它根本不执行。 */
  auto_fallback: boolean;
  /** 哪些模型的 ${VAR} 没解析成功 (模型名 → 说明).
   *
   * 这类模型仍在列表里、也仍会被员工选到, 但调用时必然失败。不显示的话
   * "这个模型为什么不工作"在界面上没有任何线索, 只有服务器日志里一行。 */
  config_errors?: Record<string, string>;
  /** 每个模型引用的那个 key 环境变量, 在**服务器上**到底设没设.
   *
   * 这是管理员问得最多的那个问题 ——「我把变量名填进去了, 生效了吗」。
   * 答案既不在这份配置里也不在数据库里, 而在服务器的 .env 里。
   * 只有 true/false, 不含 key 的任何内容。 */
  api_key_configured?: Record<string, boolean>;
  models: ModelConfig[];
}

/** 一条 fallback 链里某一跳会不会被运行时跳过.
 *
 * 规则抄自 gateway 的 resolve_chain (fallback.py:239) —— 那里对每种情况都是
 * logger.warning + continue, 也就是**静默少一跳**, 员工侧完全无感。所以要在
 * 配的时候就显示出来, 而不是等出问题去翻日志。
 */
export function chainHopIssue(
  primary: ModelConfig,
  candidateName: string,
  all: ModelConfig[],
): string | null {
  if (candidateName === primary.name) return "指向自己，会被跳过";
  const c = all.find((m) => m.name === candidateName);
  if (!c) return "这个模型不存在，会被跳过";
  if (c.mode !== primary.mode)
    return `用途不同（${primary.mode} vs ${c.mode}），会被跳过`;
  if (primary.tier === "private" && c.tier === "public")
    return "内网切公网：大 prompt 时会被跳过（防内网内容出公司）";
  return null;
}

export interface PutResponse {
  ok: boolean;
  name: string;
  created: boolean;
  revision: number | null;
}

export interface DeleteResponse {
  ok: boolean;
  name: string;
  deleted: boolean;
  /** 删掉的是默认模型时, 后端自动指定的新默认 */
  promoted_default: string | null;
  revision: number | null;
}

export const modelConfigApi = {
  list: () => api.get<ModelListResponse>("/api/admin/models"),

  put: (name: string, cfg: ModelConfig) =>
    api.put<PutResponse>(`/api/admin/models/${encodeURIComponent(name)}`, cfg),

  remove: (name: string) =>
    api.delete<DeleteResponse>(`/api/admin/models/${encodeURIComponent(name)}`),

  /** 重排。路径是 model-order 不是 models/_order —— 后者会被 {name} 抢先匹配 */
  reorder: (names: string[]) =>
    api.put<{ ok: boolean; revision: number | null }>("/api/admin/model-order", {
      names,
    }),
};

/** 新建模型时的初值. 挑的是"填了就能用"的最小集合. */
export function emptyModel(): ModelConfig {
  return {
    name: "",
    tier: "public",
    display_name: "",
    mode: "chat",
    default: false,
    upstream: { model: "", api_base: "", api_key_env: "", timeout: 60 },
    context_window: null,
    max_output_tokens: null,
    supports_tool_use: false,
    supports_streaming: true,
    supports_vision: false,
    recommended_for: [],
    cost_tier: "paid",
    fallback: null,
    rate_limits: null,
    price_per_1k_tokens: null,
    color: null,
    dot_emoji: null,
  };
}

/** 提交前的本地校验.
 *
 * 后端也会校验 (同一个 pydantic 模型), 这里做一遍是为了**在点保存之前**就
 * 把问题说清楚 —— 而不是等一个 400 回来再解析 detail。两边都要有:
 * 只有前端校验会被绕过, 只有后端校验则填错要等一个来回。
 */
export function validateModel(m: ModelConfig): string[] {
  const errs: string[] = [];
  if (!m.name.trim()) errs.push("模型 ID 不能为空");
  else if (!/^[a-zA-Z0-9._-]+$/.test(m.name))
    errs.push("模型 ID 只能用字母、数字、点、下划线、连字符（它会出现在 URL 里）");
  if (!m.display_name.trim()) errs.push("显示名称不能为空（员工在聊天页看到的就是它）");
  if (!m.upstream.model.trim()) errs.push("上游模型不能为空");
  else if (!m.upstream.model.includes("/"))
    errs.push(
      `上游模型要带 provider 前缀，如 openai/${m.upstream.model} —— 不带的话 LiteLLM 不知道走哪家`,
    );
  // ⚠ 这一格填的是**变量名**, 不是 key 本身。
  // 合法环境变量名就是 [A-Za-z_][A-Za-z0-9_]*, 而真 key 基本都含 `-` 或小写
  // (sk-xxx / AIza… / 长 base64), 所以这一条正好挡住"把真 key 粘进来"——
  // 粘进去的后果是它明文写进数据库, 也就进 pg_dump 和备份。
  // 后端 _check_api_key_env 是真正兜底的那道 (前端能被绕过), 这里是即时反馈。
  const keyEnv = m.upstream.api_key_env.trim();
  if (!keyEnv) errs.push("API Key 环境变量名不能为空（这里填变量名，不是 key 本身）");
  else if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(keyEnv) || keyEnv.length > 64)
    errs.push(
      "「API Key 环境变量名」填的是变量名不是 key —— 只能用字母、数字、下划线，" +
        "不能以数字开头，例如 DASHSCOPE_API_KEY。key 本身请让 IT 放到服务器的 .env 里，" +
        "它不进数据库，也就不会出现在备份里。",
    );
  if (m.context_window != null && m.context_window <= 0)
    errs.push("上下文窗口要是正数");
  if (m.max_output_tokens != null && m.max_output_tokens <= 0)
    errs.push("单次输出上限要是正数");
  if (m.price_per_1k_tokens != null && m.price_per_1k_tokens < 0)
    errs.push("单价不能是负数");
  if (
    m.context_window != null &&
    m.max_output_tokens != null &&
    m.max_output_tokens > m.context_window
  )
    errs.push("单次输出上限不该大于上下文窗口");
  return errs;
}
