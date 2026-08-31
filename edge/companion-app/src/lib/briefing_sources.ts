import {
  briefingContextFetch,
  calendarWeekFetch,
  emailDigestFetch,
  remindersWeekFetch,
  type BriefingContext,
} from "./tauri";

/** 早安数据源单独超时，避免一个系统 API 卡住就让页面永久显示“加载中”。 */
export const BRIEFING_SOURCE_TIMEOUT_MS = 15_000;

export type BriefingSourceResults = [
  PromiseSettledResult<string>,
  PromiseSettledResult<string>,
  PromiseSettledResult<string>,
  PromiseSettledResult<BriefingContext>,
];

function withSourceTimeout<T>(name: string, promise: Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<T>((_, reject) => {
    timer = setTimeout(() => {
      reject(new Error(`${name} 数据源超过 ${BRIEFING_SOURCE_TIMEOUT_MS / 1000} 秒未返回`));
    }, BRIEFING_SOURCE_TIMEOUT_MS);
  });

  return Promise.race([promise, timeout]).finally(() => {
    if (timer !== undefined) clearTimeout(timer);
  });
}

/** 拉取早安四个来源；每个来源最多等待 15 秒，结果仍保留给诊断卡。 */
export async function fetchBriefingSources(): Promise<BriefingSourceResults> {
  const results = await Promise.allSettled([
    withSourceTimeout("邮件", emailDigestFetch(50)),
    withSourceTimeout("日历", calendarWeekFetch(false)),
    withSourceTimeout("Reminders", remindersWeekFetch()),
    withSourceTimeout("工作上下文", briefingContextFetch()),
  ]);
  return results as BriefingSourceResults;
}
