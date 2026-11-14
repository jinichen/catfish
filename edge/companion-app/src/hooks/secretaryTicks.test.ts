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
  remindersWeekFetch: vi.fn(),
  emailListFetch: vi.fn(),
  emailClassifyNow: vi.fn(),
  sessionsUserRequests: vi.fn(),
  sendNotification: vi.fn(),
}));
vi.mock("../lib/tauri", async (orig) => ({ ...(await orig<typeof import("../lib/tauri")>()), ...t }));
const fetchRoutineCandidates = vi.hoisted(() => vi.fn());
vi.mock("../lib/routines", async (orig) => ({ ...(await orig<typeof import("../lib/routines")>()), fetchRoutineCandidates }));
const serviceLlm = vi.hoisted(() => vi.fn());
vi.mock("../lib/serviceLlm", async (orig) => ({ ...(await orig<typeof import("../lib/serviceLlm")>()), serviceLlm }));
const roomGet = vi.hoisted(() => vi.fn());
vi.mock("../lib/tauri_colleague_rooms", async (orig) => ({ ...(await orig<typeof import("../lib/tauri_colleague_rooms")>()), roomGet }));

import { installMemoryLocalStorage } from "../lib/__fixtures__/memoryLocalStorage";
import { inviteRemindersFor, settledReminder } from "../lib/colleagueTasksStore";
import { processRoomMentions } from "../lib/roomAskFlow";
import { MEETING_PREP_KEY, ROOM_ASK_KEY, ROUTINE_OFFER_KEY } from "../lib/secretaryPrefs";
import type { TaskList } from "../lib/tauri_colleague_tasks";
import type { CronJob } from "../lib/tauri_cron";
import { reloadRemindersForTest, useRemindersStore } from "../store/reminders";
import { CRON_SEEN_STORAGE_KEY, cronWatchTick, outputTsMs } from "./useCronWatcher";
import { advancePrepTick } from "./useAdvancePrep";
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
  serviceLlm.mockReset();
  roomGet.mockReset();
  // 提前准备的四个来源默认: 读到空 (各测试按需覆盖)
  t.calendarUpcomingFetch.mockResolvedValue("[]");
  t.remindersWeekFetch.mockResolvedValue("[]");
  t.emailListFetch.mockResolvedValue("[]");
  t.emailClassifyNow.mockResolvedValue({ urgency: {}, actions: {} });
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

describe("advancePrepTick", () => {
  const meeting = { calendar: "工作", summary: "ITSS 线上答辩", start: iso(11, 10, 10), end: iso(11, 10, 11), all_day: false };

  it("明天的会: 建一次性准备任务 (今天 16:00, repeat 1); 下一轮不重复建", async () => {
    t.calendarUpcomingFetch.mockResolvedValue(JSON.stringify([meeting]));
    t.cronJobCreate.mockResolvedValue({ id: "prep1" });
    expect(await advancePrepTick(NOW)).toEqual({ created: 1, cancelled: 0, caughtUp: 0 });
    expect(t.cronJobCreate).toHaveBeenCalledWith(expect.objectContaining({
      name: "会前准备：ITSS 线上答辩（11/10 10:00）", schedule: "2026-11-09T16:00", repeat: 1,
    }));
    expect(await advancePrepTick(NOW)).toEqual({ created: 0, cancelled: 0, caughtUp: 0 });
    expect(t.cronJobCreate).toHaveBeenCalledTimes(1);
  });

  it("四个来源一起: 会、出差、待办初稿、邮件回复各建一条", async () => {
    t.calendarUpcomingFetch.mockResolvedValue(JSON.stringify([meeting, { ...meeting, summary: "拜访福州客户", start: iso(11, 11, 9) }]));
    t.remindersWeekFetch.mockResolvedValue(JSON.stringify([{ task_id: "t1", text: "写 Q4 汇报 PPT", due_date_iso: "2026-11-11T18:00:00" }]));
    t.emailListFetch.mockImplementation(async (_u: boolean, _l: number, folder?: string) => folder === "Sent" ? "[]" : JSON.stringify([{ id: "e1", account: "a", folder: "INBOX", subject: "报价确认", sender: "王总",
      date: iso(11, 9, 8), is_read: false, has_attachments: false, body_text: "" }]));
    t.emailClassifyNow.mockResolvedValue({ urgency: {}, actions: { e1: { action: "回" } } });
    t.cronJobCreate.mockImplementation(async (a: { name: string }) => ({ id: a.name }));
    expect((await advancePrepTick(NOW)).created).toBe(4);
    expect(t.emailClassifyNow).toHaveBeenCalledWith([]);   // 只取分诊缓存, 不调模型
    expect(t.cronJobCreate.mock.calls.map((c) => c[0].name.split("：")[0]).sort())
      .toEqual(["会前准备", "出行准备", "待办初稿", "邮件回复"].sort());
  });

  it("会取消了 (日历上没了、还有别的会): 撤掉还没跑的准备任务", async () => {
    t.calendarUpcomingFetch.mockResolvedValue(JSON.stringify([meeting]));
    t.cronJobCreate.mockResolvedValue({ id: "prep1" });
    await advancePrepTick(NOW);
    t.calendarUpcomingFetch.mockResolvedValue(JSON.stringify([{ ...meeting, summary: "别的会", start: iso(11, 11, 10) }]));
    t.cronJobCreate.mockResolvedValue({ id: "prep2" });
    const r = await advancePrepTick(NOW);
    expect(r.cancelled).toBe(1);
    expect(t.cronJobDelete).toHaveBeenCalledWith("prep1");
  });

  it("日历读不到: 会议的准备任务不撤, 别的来源照常", async () => {
    t.calendarUpcomingFetch.mockResolvedValue(JSON.stringify([meeting]));
    t.cronJobCreate.mockResolvedValue({ id: "prep1" });
    await advancePrepTick(NOW);
    t.calendarUpcomingFetch.mockRejectedValue(new Error("没授权"));
    t.remindersWeekFetch.mockResolvedValue(JSON.stringify([{ task_id: "t1", text: "写方案", due_date_iso: "2026-11-11T18:00:00" }]));
    t.cronJobCreate.mockResolvedValue({ id: "todo1" });
    expect(await advancePrepTick(NOW)).toEqual({ created: 1, cancelled: 0, caughtUp: 0 });
    expect(t.cronJobDelete).not.toHaveBeenCalled();
  });

  it("建失败: 不记, 下一轮再试", async () => {
    t.calendarUpcomingFetch.mockResolvedValue(JSON.stringify([meeting]));
    t.cronJobCreate.mockRejectedValueOnce(new Error("hermes 没起来")).mockResolvedValue({ id: "prep1" });
    expect((await advancePrepTick(NOW)).created).toBe(0);
    expect((await advancePrepTick(NOW)).created).toBe(1);
  });

  it("hermes 拒了 (400): 记下不再重试; 连不上 (别的错) 下一轮再试", async () => {
    t.calendarUpcomingFetch.mockResolvedValue(JSON.stringify([meeting]));
    t.cronJobCreate.mockRejectedValue(new Error("HTTP 400 http://127.0.0.1:8642/api/jobs: Blocked: prompt matches threat pattern"));
    await advancePrepTick(NOW);
    await advancePrepTick(NOW);
    expect(t.cronJobCreate).toHaveBeenCalledTimes(1);
  });

  describe("到点时电脑睡着, hermes 把准备任务丢了 (11/14)", () => {
    // 真存档的开头 (~/.hermes/cron/output/538699edc81b/2026-11-09_17-40-49.md, 11/09 那份会前准备就是这么丢的)
    const MISSED = "# Cron job removed before firing (run time outside grace window)\n\n- job id: prep1\n";
    const AT_17 = new Date(2026, 10, 9, 17, 0);   // 16:00 该跑, 17:00 才开盖

    it("看到丢弃存档 → 补建一条 2 分钟后跑; 补过的再丢不再补", async () => {
      t.calendarUpcomingFetch.mockResolvedValue(JSON.stringify([meeting]));
      t.cronJobCreate.mockResolvedValueOnce({ id: "prep1" }).mockResolvedValueOnce({ id: "prep1b" });
      await advancePrepTick(NOW);
      t.cronJobOutputs.mockResolvedValue([{ timestamp: "2026-11-09_17-00-30", size_bytes: 300, snippet: MISSED }]);
      expect(await advancePrepTick(AT_17)).toEqual({ created: 0, cancelled: 0, caughtUp: 1 });
      expect(t.cronJobOutputs).toHaveBeenCalledWith("prep1", 3);
      expect(t.cronJobCreate).toHaveBeenLastCalledWith(expect.objectContaining({
        name: "会前准备：ITSS 线上答辩（11/10 10:00）", schedule: "2026-11-09T17:02", repeat: 1,
      }));
      // 补的这条 (prep1b) 又因为睡着被丢: 不再补
      expect(await advancePrepTick(new Date(2026, 10, 9, 20, 0))).toEqual({ created: 0, cancelled: 0, caughtUp: 0 });
      expect(t.cronJobCreate).toHaveBeenCalledTimes(2);
    });

    it("跑过了 / 员工自己删的 (没有丢弃存档) → 不补", async () => {
      t.calendarUpcomingFetch.mockResolvedValue(JSON.stringify([meeting]));
      t.cronJobCreate.mockResolvedValue({ id: "prep1" });
      await advancePrepTick(NOW);
      t.cronJobOutputs.mockResolvedValue([]);
      expect((await advancePrepTick(AT_17)).caughtUp).toBe(0);
      t.cronJobOutputs.mockResolvedValue([{ timestamp: "2026-11-09_16-03-00", size_bytes: 900, snippet: "# Cron Job: 会前准备：ITSS" }]);
      expect((await advancePrepTick(AT_17)).caughtUp).toBe(0);
      expect(t.cronJobCreate).toHaveBeenCalledTimes(1);
    });

    it("离开会不到 30 分钟才醒 → 来不及, 不补", async () => {
      t.calendarUpcomingFetch.mockResolvedValue(JSON.stringify([meeting]));
      t.cronJobCreate.mockResolvedValue({ id: "prep1" });
      await advancePrepTick(NOW);
      t.cronJobOutputs.mockResolvedValue([{ timestamp: "x", size_bytes: 1, snippet: MISSED }]);
      expect((await advancePrepTick(new Date(2026, 10, 10, 9, 40))).caughtUp).toBe(0);
      expect(t.cronJobCreate).toHaveBeenCalledTimes(1);
    });

    it("这一轮日历读不到 → 先不补 (没有任务说明), 下一轮读到了再补", async () => {
      t.calendarUpcomingFetch.mockResolvedValue(JSON.stringify([meeting]));
      t.cronJobCreate.mockResolvedValueOnce({ id: "prep1" }).mockResolvedValueOnce({ id: "prep1b" });
      await advancePrepTick(NOW);
      t.cronJobOutputs.mockResolvedValue([{ timestamp: "x", size_bytes: 1, snippet: MISSED }]);
      t.calendarUpcomingFetch.mockRejectedValueOnce(new Error("没授权"));
      expect((await advancePrepTick(AT_17)).caughtUp).toBe(0);
      expect((await advancePrepTick(AT_17)).caughtUp).toBe(1);
    });
  });

  it("关了开关: 什么都不读", async () => {
    localStorage.setItem(MEETING_PREP_KEY, "false");
    await advancePrepTick(NOW);
    expect(t.calendarUpcomingFetch).not.toHaveBeenCalled();
    expect(t.emailListFetch).not.toHaveBeenCalled();
  });
});

describe("processRoomMentions (群里 @我 交代的事)", () => {
  const mention = { kind: "mention" as const, room_id: "r_1", from: "zhang@example.com", title: "项目群",
    text: "@hongbo 周五前把报价单发我", joined: true, msg_id: "m_1", msg_kind: "text" as const };

  it("规则筛过 + 模型说是 → 挂提醒 (用本机群记录里的全文); 同一条不再问", async () => {
    roomGet.mockResolvedValue({ me: "hongbo@example.com", messages: [{ msg_id: "m_1", kind: "text", at: "", text: "@hongbo 周五前把报价单发我，含税价" }] });
    serviceLlm.mockResolvedValue('{"is_task":true,"what":"把含税报价单发给张三","deadline":"2026-11-13"}');
    expect(await processRoomMentions([mention], NOW)).toBe(1);
    expect(serviceLlm.mock.calls[0][2]).toContain("含税价");
    const [r] = useRemindersStore.getState().items;
    expect(r).toMatchObject({ kind: "ask", text: "zhang 在「项目群」让你：把含税报价单发给张三", meta: "截止 11/13 18:00" });
    expect(await processRoomMentions([mention], NOW)).toBe(0);
    expect(serviceLlm).toHaveBeenCalledTimes(1);
  });

  it("任务卡 (走派任务那条)、普通群消息、寒暄、没加入的群: 不调模型", async () => {
    await processRoomMentions([
      { ...mention, msg_kind: "card" },
      { ...mention, kind: "msg", msg_id: "m_2" },
      { ...mention, msg_id: "m_3", text: "@hongbo 收到谢谢" },
      { ...mention, msg_id: "m_4", joined: false },
    ], NOW);
    expect(serviceLlm).not.toHaveBeenCalled();
  });

  it("模型说不是 / 模型挂了: 不挂", async () => {
    roomGet.mockRejectedValue(new Error("x"));
    serviceLlm.mockResolvedValueOnce('{"is_task":false}').mockResolvedValueOnce(null);
    expect(await processRoomMentions([mention, { ...mention, msg_id: "m_9" }], NOW)).toBe(0);
  });

  it("关了开关: 不看", async () => {
    localStorage.setItem(ROOM_ASK_KEY, "false");
    expect(await processRoomMentions([mention], NOW)).toBe(0);
    expect(roomGet).not.toHaveBeenCalled();
  });
});

describe("同事派来的任务 → 提醒条", () => {
  const list = (state: string, schedule: string | null = null): TaskList => ({
    incoming: [{ task_id: "t_9", title: "整理拜访记录", owner: "zhou@example.com", deadline: "2026-11-13T18:00", state: state as never,
      late: false, received_at: null, reminded_at: null, schedule, to_review: 0 }],
    outgoing: [], pipelines: [],
  });

  it("待处理的一次性任务: 挂「接下，让小鲶开工」; 接下后收掉", () => {
    inviteRemindersFor(list("待处理"), NOW);
    const [r] = useRemindersStore.getState().items;
    expect(r).toMatchObject({ text: "zhou 派给你「整理拜访记录」", actionLabel: "接下，让小鲶开工",
      ctask: { taskId: "t_9", role: "invitee", oneOff: true } });
    expect(r.meta).toMatch(/^截止 /);
    expect(settledReminder(r, list("待处理"))).toBe(false);
    expect(settledReminder(r, list("在做"))).toBe(true);
    expect(settledReminder(r, { incoming: [], outgoing: [], pipelines: [] })).toBe(true);
  });

  it("周期任务 / 改了要重新同意的: 主按钮「去看看」(要看清排期再接)", () => {
    inviteRemindersFor(list("待处理", "每周一 9:00"), NOW);
    expect(useRemindersStore.getState().items[0]).toMatchObject({ actionLabel: "去看看", ctask: { oneOff: false } });
    reloadRemindersForTest();
    localStorage.clear();
    reloadRemindersForTest();
    inviteRemindersFor(list("要重新同意"), NOW);
    expect(useRemindersStore.getState().items[0].text).toBe("zhou 改了「整理拜访记录」，要你重新看一下接不接");
  });

  it("在做 / 已交回的不挂", () => {
    inviteRemindersFor(list("在做"), NOW);
    expect(useRemindersStore.getState().items).toEqual([]);
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

// ── 11/09 能力登记 (lib/secretary.ts): 关了就是真停; 统一入口; 群聊不认识秘书 ──────────────
describe("小鲶秘书能力登记", () => {
  const meeting = { calendar: "工作", summary: "ITSS 线上答辩", start: iso(11, 10, 10), end: iso(11, 10, 11), all_day: false };

  it("关掉「提前准备」: 已经排上、还没到点的准备任务撤掉, 记录清掉; 再开会重新排", async () => {
    const { SECRETARY, setAbilityEnabled } = await import("../lib/secretary");
    const prep = SECRETARY.find((a) => a.id === "advance-prep")!;
    t.calendarUpcomingFetch.mockResolvedValue(JSON.stringify([meeting]));
    t.cronJobCreate.mockResolvedValue({ id: "prep1" });
    expect(await advancePrepTick(NOW)).toEqual({ created: 1, cancelled: 0, caughtUp: 0 });
    vi.spyOn(Date, "now").mockReturnValue(NOW.getTime());
    const note = await setAbilityEnabled(prep, false);
    expect(t.cronJobDelete).toHaveBeenCalledWith("prep1");
    expect(note).toBe("已经排上、还没做的 1 条准备任务撤了");
    expect(localStorage.getItem(MEETING_PREP_KEY)).toBe("false");
    expect(await advancePrepTick(NOW)).toEqual({ created: 0, cancelled: 0, caughtUp: 0 });   // 关着: 不再排
    await setAbilityEnabled(prep, true);
    expect(await advancePrepTick(NOW)).toEqual({ created: 1, cancelled: 0, caughtUp: 0 });   // 再开: 记录清过, 重新排
  });

  it("关掉「群里 @我」: 提醒条上挂着的那类提醒撤掉, 别的提醒不动", async () => {
    const { SECRETARY, setAbilityEnabled } = await import("../lib/secretary");
    const store = useRemindersStore.getState();
    const base = { title: "x", text: "y", createdAt: NOW.getTime(), expiresAt: NOW.getTime() + 86_400_000 };
    store.add({ ...base, id: "ask:1", kind: "ask" } as never, NOW.getTime());
    store.add({ ...base, id: "dl:1", kind: "deadline" } as never, NOW.getTime());
    const note = await setAbilityEnabled(SECRETARY.find((a) => a.id === "room-ask")!, false);
    expect(useRemindersStore.getState().items.map((r) => r.id)).toEqual(["dl:1"]);
    expect(note).toBe("提醒条上 1 条群里交代的事也撤了");
    expect(localStorage.getItem(ROOM_ASK_KEY)).toBe("false");
  });

  it("「做完就报」不设开关; 其余三项都有, 都能撤", async () => {
    const { SECRETARY } = await import("../lib/secretary");
    expect(SECRETARY.filter((a) => a.prefKey === null).map((a) => a.id)).toEqual(["cron-done"]);
    expect(SECRETARY.filter((a) => a.prefKey).every((a) => a.onDisable)).toBe(true);
    expect(SECRETARY.find((a) => a.id === "routine-offer")!.prefKey).toBe(ROUTINE_OFFER_KEY);
  });

  it("统一入口: 按登记起计时器 (不叠轮), 群消息经广播送到订了的能力", async () => {
    vi.useFakeTimers();
    try {
      const { startSecretary } = await import("./useSecretary");
      const { emitRoomEvents } = await import("../lib/roomEventBus");
      let release: () => void = () => {};
      const tick = vi.fn(() => new Promise<void>((r) => { release = r; }));
      const onRoomEvents = vi.fn(async () => 0);
      const stop = startSecretary([
        { id: "a", icon: "", label: "a", prefKey: null, schedule: { tick, everyMs: 1000, firstMs: 0 } },
        { id: "b", icon: "", label: "b", prefKey: null, onRoomEvents },
      ]);
      expect(tick).toHaveBeenCalledTimes(1);
      vi.advanceTimersByTime(3000);
      expect(tick).toHaveBeenCalledTimes(1);   // 上一轮没完, 不叠
      release();
      await Promise.resolve(); await Promise.resolve();
      vi.advanceTimersByTime(1000);
      expect(tick).toHaveBeenCalledTimes(2);
      emitRoomEvents([{ kind: "mention", room_id: "r", from: "x", title: "群" }]);
      expect(onRoomEvents).toHaveBeenCalledTimes(1);
      stop();
      emitRoomEvents([{ kind: "mention", room_id: "r", from: "x", title: "群" }]);
      vi.advanceTimersByTime(5000);
      expect(onRoomEvents).toHaveBeenCalledTimes(1);
      expect(tick).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });
});
