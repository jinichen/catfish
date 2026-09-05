/** Hermes profile 路由的 Companion 侧薄适配。
 *
 * 功能关闭、未就绪或状态读取失败时，一律回落现有 default profile。普通对话
 * 不调用这里，只有早安工作参谋 Call 1 使用。
 */
import {
  expertBotRoute,
  expertBotsStatus,
  type ExpertBotRoute,
  type ExpertBotsStatus,
} from "./tauri";

export interface ResolvedExpertBotRequest {
  url: string;
  model: string;
  profileId: string | null;
  usingExpertBot: boolean;
  reason: string;
}

export function buildHermesProfileUrl(
  baseUrl: string,
  profile: string,
  endpoint: string,
  query = "",
): string {
  const base = baseUrl.replace(/\/+$/, "");
  const path = endpoint.startsWith("/") ? endpoint : `/${endpoint}`;
  return `${base}/p/${encodeURIComponent(profile)}${path}${query}`;
}

export function advisorRequestUrlFromStatus(args: {
  baseUrl: string;
  useHermes: boolean;
  query: string;
  status: ExpertBotsStatus | null;
}): string {
  const { baseUrl, useHermes, query, status } = args;
  if (useHermes && status?.enabled && status.ready) {
    return buildHermesProfileUrl(
      baseUrl,
      status.advisorProfile,
      "/v1/chat/completions",
      query,
    );
  }
  return `${baseUrl.replace(/\/+$/, "")}/v1/chat/completions${query}`;
}

export async function resolveAdvisorRequestUrl(args: {
  baseUrl: string;
  useHermes: boolean;
  query: string;
}): Promise<string> {
  if (!args.useHermes) {
    return advisorRequestUrlFromStatus({ ...args, status: null });
  }
  try {
    const status = await expertBotsStatus();
    return advisorRequestUrlFromStatus({ ...args, status });
  } catch (error) {
    console.warn("[expert-bots] 状态读取失败，早安回落 default profile:", error);
    return advisorRequestUrlFromStatus({ ...args, status: null });
  }
}

/** 通用场景路由。配置缺失或 Profile 不健康时 fail-open 到 default profile。 */
export async function resolveExpertBotRequest(args: {
  baseUrl: string;
  useHermes: boolean;
  endpoint?: string;
  query?: string;
  scenario: "briefing.advisor" | "email.draft";
  pickerModel: string;
}): Promise<ResolvedExpertBotRequest> {
  const fallback = (reason: string): ResolvedExpertBotRequest => ({
    url: `${args.baseUrl.replace(/\/+$/, "")}${args.endpoint ?? "/v1/chat/completions"}${args.query ?? ""}`,
    model: args.pickerModel,
    profileId: null,
    usingExpertBot: false,
    reason,
  });
  if (!args.useHermes) return fallback("当前请求不经过 Hermes");

  let route: ExpertBotRoute;
  try {
    route = await expertBotRoute(args.scenario, args.pickerModel);
  } catch (error) {
    console.warn(`[expert-bots] ${args.scenario} 路由读取失败，回落 default profile:`, error);
    return fallback("专家 Bot 路由读取失败");
  }
  if (!route.enabled || !route.ready || !route.profileId || !route.model.trim()) {
    return fallback(route.reason);
  }
  return {
    url: buildHermesProfileUrl(
      args.baseUrl,
      route.profileId,
      args.endpoint ?? "/v1/chat/completions",
      args.query ?? "",
    ),
    model: route.model,
    profileId: route.profileId,
    usingExpertBot: true,
    reason: route.reason,
  };
}
