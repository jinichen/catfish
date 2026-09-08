import type { EmailSourceDiscovery } from "./tauri";

export function parseEmailSourceDiscovery(raw: string): EmailSourceDiscovery {
  const value: unknown = JSON.parse(raw);
  if (!value || typeof value !== "object") {
    throw new Error("邮件发现返回格式异常");
  }
  const payload = value as Partial<EmailSourceDiscovery>;
  if (!Array.isArray(payload.sources) || typeof payload.platform !== "string") {
    throw new Error("邮件发现缺少来源信息");
  }
  if (payload.selected_client !== undefined && payload.selected_client !== null &&
      payload.selected_client !== "outlook-win" && payload.selected_client !== "foxmail-win") {
    throw new Error("邮件发现的已选来源无效");
  }
  return payload as EmailSourceDiscovery;
}

export function readyEmailSources(discovery: EmailSourceDiscovery) {
  return discovery.sources.filter((source) => source.status === "ready");
}
