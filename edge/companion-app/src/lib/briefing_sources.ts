import {
  briefingContextFetch,
  calendarWeekFetch,
  emailDigestFetch,
  remindersWeekFetch,
  type BriefingContext,
} from "./tauri";

/** 早安数据源单独超时，避免一个系统 API 卡住就让页面永久显示“加载中”。 */
export const BRIEFING_SOURCE_TIMEOUT_MS = 15_000;
/** macOS 任务库首次导入 Reminders 可能较慢，略高于 tool-bridge 的 30s 上限。 */
export const BRIEFING_TASKS_TIMEOUT_MS = 35_000;
/**
 * 11/3: 日历要盖住后台最坏情况 —— EventKit 10s 没回就退 AppleScript (20s)，共 30s。
 * 原来按普通来源 15s，升级后 / 刚登录时 EventKit 慢，后台还在退路上，这里先判了超时。
 */
export const BRIEFING_CALENDAR_TIMEOUT_MS = 35_000;

export type BriefingSourceResults = [
  PromiseSettledResult<string>,
  PromiseSettledResult<string>,
  PromiseSettledResult<string>,
  PromiseSettledResult<BriefingContext>,
];

function withSourceTimeout<T>(
  name: string,
  promise: Promise<T>,
  timeoutMs = BRIEFING_SOURCE_TIMEOUT_MS,
): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<T>((_, reject) => {
    timer = setTimeout(() => {
      reject(new Error(`${name} 数据源超过 ${timeoutMs / 1000} 秒未返回`));
    }, timeoutMs);
  });

  return Promise.race([promise, timeout]).finally(() => {
    if (timer !== undefined) clearTimeout(timer);
  });
}

/** 拉取早安四个来源；普通来源 15 秒，日历和任务库单独给更长窗口。 */
export async function fetchBriefingSources(): Promise<BriefingSourceResults> {
  const results = await Promise.allSettled([
    withSourceTimeout("邮件", emailDigestFetch(50)),
    withSourceTimeout("日历", calendarWeekFetch(false), BRIEFING_CALENDAR_TIMEOUT_MS),
    withSourceTimeout("任务库", remindersWeekFetch(), BRIEFING_TASKS_TIMEOUT_MS),
    withSourceTimeout("工作上下文", briefingContextFetch()),
  ]);
  return results as BriefingSourceResults;
}
