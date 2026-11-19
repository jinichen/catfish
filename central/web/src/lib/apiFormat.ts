/**
 * 上游「接口格式」(11/19 鸿波「是不是只支持 openai 兼容的 API, 不支持 anthropic 兼容?」)。
 *
 * 网关用 LiteLLM 接上游, 走哪种格式看「上游模型」的前缀:
 *   openai/xxx    → OpenAI 兼容 (/chat/completions)
 *   anthropic/xxx → Anthropic 兼容 (/v1/messages; 地址只填到兼容路径为止, /v1/messages 网关自己补)
 *   其它 (gemini/ dashscope/ …) → 那家的官方 SDK
 * 后台原来只在提示里写「OpenAI 兼容」, 看着像不支持 Anthropic。这里把前缀做成一个选项。
 *
 * 地址归「供应商」管, 而同一家两种格式的地址不一样 (百炼: /compatible-mode/v1 和 /apps/anthropic) ——
 * 格式和地址对不上时请求必挂。formatMismatch 按地址的样子提醒, 不拦保存 (地址写法各家不一, 只是经验判断)。
 */
export type ApiFormat = "openai" | "anthropic" | "other";

export const API_FORMATS: { value: ApiFormat; label: string }[] = [
  { value: "openai", label: "OpenAI 兼容" },
  { value: "anthropic", label: "Anthropic 兼容" },
  { value: "other", label: "其它 (官方 SDK, 如 gemini/)" },
];

export const API_FORMAT_HINT = "上游按哪种格式收请求 (就是「上游模型」的前缀)。Anthropic 兼容的地址只填到兼容路径为止 (如 …/apps/anthropic)。";

export function apiFormatOf(upstreamModel: string): ApiFormat {
  const p = upstreamModel.split("/", 1)[0].toLowerCase();
  if (!upstreamModel.includes("/")) return "openai";
  return p === "openai" ? "openai" : p === "anthropic" ? "anthropic" : "other";
}

/** 换格式 = 换前缀, 模型名本身不动。选「其它」时不猜前缀, 原样留给管理员自己写。 */
export function withApiFormat(upstreamModel: string, fmt: ApiFormat): string {
  const bare = upstreamModel.includes("/") ? upstreamModel.slice(upstreamModel.indexOf("/") + 1) : upstreamModel;
  if (fmt === "other") return upstreamModel;
  return `${fmt}/${bare}`;
}

export function formatMismatch(upstreamModel: string, apiBase: string | null | undefined): string | null {
  const base = (apiBase ?? "").trim().toLowerCase();
  if (!base || base.startsWith("${")) return null; // 没填 / .env 占位符: 看不出来
  const fmt = apiFormatOf(upstreamModel);
  const looksAnthropic = /anthropic/.test(base);
  const looksOpenAI = /compatible-mode|\/openai|\/v1\/?$/.test(base) && !looksAnthropic;
  if (fmt === "anthropic" && looksOpenAI)
    return "选的是 Anthropic 兼容, 但供应商地址看着是 OpenAI 兼容的 —— 请求会发到错的路径。同一家两种格式地址不同 (如百炼 Anthropic 兼容是 …/apps/anthropic), 要另建一个供应商填 Anthropic 地址。";
  if (fmt === "anthropic" && /\/v1\/messages\/?$/.test(base))
    return "地址只填到兼容路径为止, 末尾的 /v1/messages 网关会自己补, 填了会变成 …/v1/messages/v1/messages。";
  if (fmt === "openai" && looksAnthropic)
    return "选的是 OpenAI 兼容, 但供应商地址看着是 Anthropic 兼容的。改成「Anthropic 兼容」, 或换一个 OpenAI 兼容地址的供应商。";
  return null;
}
