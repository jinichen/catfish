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
