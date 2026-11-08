// @vitest-environment jsdom
/** 小鲶秘书三个轮询的一轮 (11/09): 读任务 / 结果 / 日历 / 对话 → 挂提醒、建 / 撤任务、发通知。hermes 调用全 mock。 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const t = vi.hoisted(() => ({
  cronJobsList: vi.fn(),
  cronJobOutputs: vi.fn(),
  cronJobOutputRead: vi.fn(),
  cronJobCreate: vi.fn(),
  cronJobDelete: vi.fn(),
  calendarUpcomingFetch: vi.fn(),
  sessionsUserRequests: vi.fn(),
  sendNotification: vi.fn(),
}));
vi.mock("../lib/tauri", async (orig) => ({ ...(await orig<typeof import("../lib/tauri")>()), ...t }));
const fetchRoutineCandidates = vi.hoisted(() => vi.fn());
vi.mock("../lib/routines", async (orig) => ({ ...(await orig<typeof import("../lib/routines")>()), fetchRoutineCandidates }));

import { installMemoryLocalStorage } from "../lib/__fixtures__/memoryLocalStorage";
import { MEETING_PREP_KEY, ROUTINE_OFFER_KEY } from "../lib/secretaryPrefs";
import type { CronJob } from "../lib/tauri_cron";
import { reloadRemindersForTest, useRemindersStore } from "../store/reminders";
import { CRON_SEEN_STORAGE_KEY, cronWatchTick, outputTsMs } from "./useCronWatcher";
import { meetingPrepTick } from "./useMeetingPrep";
import { routineDiscoveryTick } from "./useRoutineDiscovery";

installMemoryLocalStorage();

const NOW = new Date(2026, 10, 9, 10, 0); // 11/09 周一 10:00 本地
const iso = (m: number, d: number, h: number, mi = 0, s = 0) => new Date(2026, m - 1, d, h, mi, s).toISOString();
const job = (p: Partial<CronJob>): CronJob => ({
  id: "a", name: "AI六巨头日报", skills: [], enabled: true, state: "scheduled",
  schedule: { kind: "cron", expr: "30 9 * * 1-5" }, last_status: "ok", last_run_at: iso(11, 9, 9, 34, 36), ...p,
});
const ARCHIVE = "# Cron Job: AI六巨头日报\n\n**Response Characters:** 20\n## Response\n\n**一句话定性：回补**\n";

beforeEach(() => {
  localStorage.clear();
  reloadRemindersForTest();
  Object.values(t).forEach((f) => f.mockReset());
  fetchRoutineCandidates.mockReset();
  t.sendNotification.mockResolvedValue(undefined);
  t.cronJobOutputs.mockResolvedValue([{ timestamp: "2026-11-09_09-34-34", size_bytes: 10, snippet: "" }]);
  t.cronJobOutputRead.mockResolvedValue(ARCHIVE);
  vi.spyOn(document, "hasFocus").mockReturnValue(false);
});
afterEach(() => vi.restoreAllMocks());

describe("cronWatchTick", () => {
  it("第一次启动: 只记现状, 不报不通知", async () => {
    t.cronJobsList.mockResolvedValue([job({})]);
    expect(await cronWatchTick(NOW)).toEqual([]);
    expect(JSON.parse(localStorage.getItem(CRON_SEEN_STORAGE_KEY)!)).toEqual({ a: iso(11, 9, 9, 34, 36) });
    expect(t.sendNotification).not.toHaveBeenCalled();
  });

  it("新的一次运行: 挂提醒 (结果第一句) + 窗口在后台时发系统通知; 同一次不重复报", async () => {
    localStorage.setItem(CRON_SEEN_STORAGE_KEY, JSON.stringify({ a: iso(11, 6, 9, 34) }));
    t.cronJobsList.mockResolvedValue([job({})]);
    const added = await cronWatchTick(NOW);
    expect(added.map((r) => r.text)).toEqual(["「AI六巨头日报」做完了"]);
    expect(added[0].meta).toBe("一句话定性：回补");
    expect(added[0].cron?.outputTs).toBe("2026-11-09_09-34-34");
    expect(t.sendNotification).toHaveBeenCalledWith("✅ 定时任务做完了", "「AI六巨头日报」做完了 · 一句话定性：回补");
    expect(useRemindersStore.getState().notifiedAt).toBe(NOW.getTime());
    expect(await cronWatchTick(NOW)).toEqual([]);
  });

  it("窗口在前台 / 夜里: 只挂提醒, 不发通知", async () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    localStorage.setItem(CRON_SEEN_STORAGE_KEY, "{}");
    t.cronJobsList.mockResolvedValue([job({})]);
    await cronWatchTick(NOW);
    expect(t.sendNotification).not.toHaveBeenCalled();

    vi.spyOn(document, "hasFocus").mockReturnValue(false);
    localStorage.setItem(CRON_SEEN_STORAGE_KEY, "{}");
    reloadRemindersForTest();
    t.cronJobsList.mockResolvedValue([job({ id: "b", last_run_at: iso(11, 9, 23, 0) })]);
    t.cronJobOutputs.mockResolvedValue([{ timestamp: "2026-11-09_23-00-00", size_bytes: 1, snippet: "" }]);
    const added = await cronWatchTick(new Date(2026, 10, 9, 23, 5));
    expect(added).toHaveLength(1);
    expect(t.sendNotification).not.toHaveBeenCalled();
  });

  it("结果是 [SILENT]: 不报", async () => {
    localStorage.setItem(CRON_SEEN_STORAGE_KEY, "{}");
    t.cronJobsList.mockResolvedValue([job({})]);
    t.cronJobOutputRead.mockResolvedValue("# Cron Job: x\n\n## Response\n\n[SILENT]");
    expect(await cronWatchTick(NOW)).toEqual([]);
  });

  it("最新存档跟这次运行对不上 (旧的): 照样报, 但不给「看结果」", async () => {
    localStorage.setItem(CRON_SEEN_STORAGE_KEY, "{}");
    t.cronJobsList.mockResolvedValue([job({})]);
    t.cronJobOutputs.mockResolvedValue([{ timestamp: "2026-11-06_09-34-34", size_bytes: 1, snippet: "" }]);
    const [r] = await cronWatchTick(NOW);
    expect(r.cron?.outputTs).toBeUndefined();
    expect(r.actionLabel).toBe("让小鲶看看");
    expect(t.cronJobOutputRead).not.toHaveBeenCalled();
  });

  it("几条一起做完: 合成一条通知", async () => {
    localStorage.setItem(CRON_SEEN_STORAGE_KEY, "{}");
    t.cronJobsList.mockResolvedValue([
      job({}),
      job({ id: "b", name: "资质邮件周扫描", last_status: "error", last_error: "超时" }),
    ]);
    await cronWatchTick(NOW);
    expect(t.sendNotification).toHaveBeenCalledWith("定时任务：1 条做完、1 条没做成", "「AI六巨头日报」做完了\n「资质邮件周扫描」没做成");
  });

  it("存档文件名按本地时间解析", () => {
    expect(outputTsMs("2026-11-09_07-00-34")).toBe(new Date(2026, 10, 9, 7, 0, 34).getTime());
    expect(outputTsMs("bad")).toBeNaN();
  });
});

describe("meetingPrepTick", () => {
  const meeting = { calendar: "工作", summary: "ITSS 线上答辩", start: iso(11, 10, 10), end: iso(11, 10, 11), all_day: false };

  it("明天的会: 建一次性准备任务 (今天 16:00, repeat 1); 下一轮不重复建", async () => {
    t.calendarUpcomingFetch.mockResolvedValue(JSON.stringify([meeting]));
    t.cronJobCreate.mockResolvedValue({ id: "prep1" });
    expect(await meetingPrepTick(NOW)).toEqual({ created: 1, cancelled: 0 });
    expect(t.cronJobCreate).toHaveBeenCalledWith(expect.objectContaining({
      name: "会前准备：ITSS 线上答辩（11/10 10:00）", schedule: "2026-11-09T16:00", repeat: 1,
    }));
    expect(await meetingPrepTick(NOW)).toEqual({ created: 0, cancelled: 0 });
    expect(t.cronJobCreate).toHaveBeenCalledTimes(1);
  });

  it("会取消了 (日历上没了、还有别的会): 撤掉还没跑的准备任务", async () => {
    t.calendarUpcomingFetch.mockResolvedValue(JSON.stringify([meeting]));
    t.cronJobCreate.mockResolvedValue({ id: "prep1" });
    await meetingPrepTick(NOW);
    t.calendarUpcomingFetch.mockResolvedValue(JSON.stringify([{ ...meeting, summary: "别的会", start: iso(11, 11, 10) }]));
    t.cronJobCreate.mockResolvedValue({ id: "prep2" });
    const r = await meetingPrepTick(NOW);
    expect(r.cancelled).toBe(1);
    expect(t.cronJobDelete).toHaveBeenCalledWith("prep1");
  });

  it("日历读不到: 什么都不动", async () => {
    t.calendarUpcomingFetch.mockRejectedValue(new Error("没授权"));
    expect(await meetingPrepTick(NOW)).toEqual({ created: 0, cancelled: 0 });
    expect(t.cronJobCreate).not.toHaveBeenCalled();
  });

  it("建失败: 不记, 下一轮再试", async () => {
    t.calendarUpcomingFetch.mockResolvedValue(JSON.stringify([meeting]));
    t.cronJobCreate.mockRejectedValueOnce(new Error("hermes 没起来")).mockResolvedValue({ id: "prep1" });
    expect((await meetingPrepTick(NOW)).created).toBe(0);
    expect((await meetingPrepTick(NOW)).created).toBe(1);
  });

  it("关了开关: 不看日历", async () => {
    localStorage.setItem(MEETING_PREP_KEY, "false");
    await meetingPrepTick(NOW);
    expect(t.calendarUpcomingFetch).not.toHaveBeenCalled();
  });
});

describe("routineDiscoveryTick", () => {
  const reqs = [
    ...[19, 26].map((d) => ({ session_id: `s${d}`, started_at: iso(10, d, 9, 10), text: "帮我汇总这周周报" })),
    { session_id: "s2", started_at: iso(11, 2, 9, 10), text: "周报汇总" },
    ...[1, 2, 3, 4, 5].map((i) => ({ session_id: `x${i}`, started_at: iso(10, 20 + i, 14), text: `随便问问第${i}件事` })),
  ];

  it("10 点后一天一次: 核过节奏才挂提议; 当天第二轮不再调模型", async () => {
    t.sessionsUserRequests.mockResolvedValue(reqs);
    t.cronJobsList.mockResolvedValue([]);
    fetchRoutineCandidates.mockResolvedValue([
      { title: "汇总周报", what: "", evidence: [0, 1, 2], jobPrompt: "把上周工作整理成周报草稿。" },
      { title: "瞎编的", what: "", evidence: [3, 4, 5], jobPrompt: "这组节奏对不上，不该提。" },
    ]);
    expect(await routineDiscoveryTick(NOW)).toBe(1);
    expect(useRemindersStore.getState().items.map((r) => r.text)).toEqual(["「汇总周报」你每周一都会找我做"]);
    expect(t.sessionsUserRequests).toHaveBeenCalledWith(56);
    expect(await routineDiscoveryTick(NOW)).toBe(0);
    expect(fetchRoutineCandidates).toHaveBeenCalledTimes(1);
  });

  it("10 点前 / 关了开关 / 请求太少: 不调模型", async () => {
    expect(await routineDiscoveryTick(new Date(2026, 10, 9, 9, 0))).toBe(0);
    localStorage.setItem(ROUTINE_OFFER_KEY, "false");
    expect(await routineDiscoveryTick(NOW)).toBe(0);
    localStorage.setItem(ROUTINE_OFFER_KEY, "true");
    t.sessionsUserRequests.mockResolvedValue(reqs.slice(0, 3));
    expect(await routineDiscoveryTick(NOW)).toBe(0);
    expect(fetchRoutineCandidates).not.toHaveBeenCalled();
  });
});
