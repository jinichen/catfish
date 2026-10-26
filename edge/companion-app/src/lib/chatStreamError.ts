/** Hermes may put failure detail in its extension rather than OpenAI.error. */
export function chatStreamError(chunk: unknown): string | undefined {
  if (!chunk || typeof chunk !== "object") return undefined;
  const data = chunk as Record<string, unknown>;
  const hermes = data.hermes && typeof data.hermes === "object"
    ? data.hermes as Record<string, unknown> : undefined;
  for (const value of [data.error, hermes?.error]) {
    if (typeof value === "string" && value.trim()) return value;
    if (value && typeof value === "object" && "message" in value &&
        typeof value.message === "string" && value.message.trim()) {
      return value.message;
    }
  }
  const failed = Array.isArray(data.choices) && data.choices.some((choice) =>
    choice && typeof choice === "object" && choice.finish_reason === "error");
  if (failed || hermes?.failed === true) {
    return "Hermes 已报告对话失败，但未提供错误详情；请检查本次请求对应的 agent.log。";
  }
  return undefined;
}

/** 输出被截断 (finish_reason "length") —— 明确的「半途停下」信号, 自动接力据此续 (10/26)。
 *  hermes 截断时这一块同时带 error (会被 chatStreamError 报成错误), 所以要在那之前单独认出来。 */
export function isTruncatedChunk(chunk: unknown): boolean {
  if (!chunk || typeof chunk !== "object") return false;
  const data = chunk as Record<string, unknown>;
  const hermes = data.hermes && typeof data.hermes === "object"
    ? data.hermes as Record<string, unknown> : undefined;
  if (hermes?.error_code === "output_truncated") return true;
  return Array.isArray(data.choices) && data.choices.some((choice) =>
    choice && typeof choice === "object" && choice.finish_reason === "length");
}
