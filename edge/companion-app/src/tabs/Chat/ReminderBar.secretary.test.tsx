// @vitest-environment jsdom
/** 提醒条 · 小鲶秘书 (11/09): 定时任务做完 → 看结果 (弹窗, 不开对话); 没做成 → 重跑; 托管提议 → 建任务 / 改一改 / 不用。 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const cronJobRun = vi.hoisted(() => vi.fn());
const cronJobCreate = vi.hoisted(() => vi.fn());
const cronJobOutputRead = vi.hoisted(() => vi.fn());
const taskLibraryCreate = vi.hoisted(() => vi.fn());
vi.mock("../../lib/tauri", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri")>()),
  cronJobRun, cronJobCreate, cronJobOutputRead, taskLibraryCreate,
}));
const taskAccept = vi.hoisted(() => vi.fn());
vi.mock("../../lib/tauri_colleague_tasks", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri_colleague_tasks")>()), taskAccept,
}));

import { installMemoryLocalStorage } from "../../lib/__fixtures__/memoryLocalStorage";
import { cronReminder } from "../../lib/cronWatch";
import { routineReminder, validateCandidates } from "../../lib/routines";
import { colleagueInviteReminder } from "../../lib/reminders";
import { roomAskReminder } from "../../lib/roomAsks";
import type { CronJob } from "../../lib/tauri_cron";
import { reloadRemindersForTest, useRemindersStore } from "../../store/reminders";
import { useUIStore } from "../../store/ui";
import ReminderBar from "./ReminderBar";

installMemoryLocalStorage();

const RUN_AT = new Date(Date.now() - 60_000).toISOString();
const job = (p: Partial<CronJob> = {}): CronJob => ({
  id: "885516b0c193", name: "AI六巨头日报", skills: [], enabled: true, state: "scheduled",
  schedule: { kind: "cron", expr: "30 9 * * 1-5" }, last_status: "ok", last_run_at: RUN_AT, ...p,
});
const ARCHIVE = "# Cron Job: AI六巨头日报\n\n**Response Characters:** 30\n## Response\n\n# 日报\n\n**一句话定性：周五是空头回补**\n\n## 股价\n- 英伟达 +3%\n";

function done(p: Partial<CronJob> = {}, status: "ok" | "delivery_failed" = "ok") {
  return cronReminder({ job: job({ last_status: status, ...p }), runAt: RUN_AT, status },
    "# 日报\n\n**一句话定性：周五是空头回补**", "2026-11-09_09-34-34", new Date());
}
function failed() {
  return cronReminder({ job: job({ last_status: "error", last_error: "model timeout" }), runAt: RUN_AT, status: "error" },
    null, undefined, new Date());
}
function offer() {
  const t = (d: number) => new Date(2026, 9, d, 9, 10).toISOString();
  const reqs = [19, 26].map((d, i) => ({ session_id: `s${i}`, started_at: t(d), text: "帮我汇总周报" }))
    .concat([{ session_id: "s2", started_at: new Date(2026, 10, 2, 9, 10).toISOString(), text: "周报汇总" }]);
  const [o] = validateCandidates([{ title: "汇总周报", what: "", evidence: [0, 1, 2], jobPrompt: "把上周工作整理成周报草稿。" }],
    reqs, [], () => false);
  return routineReminder(o, new Date());
}

beforeEach(() => {
  localStorage.clear();
  reloadRemindersForTest();
  cronJobRun.mockReset().mockResolvedValue(undefined);
  cronJobCreate.mockReset().mockResolvedValue({ ...job({ id: "newjob" }), next_run_at: new Date(2026, 10, 16, 8, 0).toISOString()   /* 本机时区的 8:00 —— CI 是 UTC */ });
  cronJobOutputRead.mockReset().mockResolvedValue(ARCHIVE);
  taskLibraryCreate.mockReset().mockResolvedValue(undefined);
  taskAccept.mockReset().mockResolvedValue({ accepted_at: "x", started: true });
  useUIStore.setState({ activeTab: "chat", pendingReminderChat: null });
});
afterEach(cleanup);

describe("ReminderBar · 定时任务做完", () => {
  it("看结果: 弹窗显示结果全文 (切掉提示词), 不开对话; 这条收起", async () => {
    act(() => void useRemindersStore.getState().add(done()));
    render(<ReminderBar />);
    expect(screen.getByText("「AI六巨头日报」做完了")).toBeTruthy();
    expect(screen.getByText(/一句话定性：周五是空头回补/)).toBeTruthy();
    fireEvent.click(screen.getByText("看结果"));
    expect(cronJobOutputRead).toHaveBeenCalledWith("885516b0c193", "2026-11-09_09-34-34");
    await screen.findByText("英伟达 +3%");
    expect(screen.getByRole("dialog", { name: "定时任务结果：AI六巨头日报" })).toBeTruthy();
    expect(useUIStore.getState().pendingReminderChat).toBeNull();
    expect(useRemindersStore.getState().items).toHaveLength(0);
  });

  it("弹窗里「让小鲶接着处理」才开对话, 带上结果", async () => {
    act(() => void useRemindersStore.getState().add(done()));
    render(<ReminderBar />);
    fireEvent.click(screen.getByText("看结果"));
    fireEvent.click(await screen.findByText("让小鲶接着处理"));
    const chat = useUIStore.getState().pendingReminderChat as { prompt: string; title: string } | null;
    expect(chat?.prompt).toContain("一句话定性：周五是空头回补");
    expect(chat?.title).toBe("定时任务：AI六巨头日报");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("没送到微信: 小字说清原因", () => {
    act(() => void useRemindersStore.getState().add(done({ last_delivery_error: "iLink sendmessage session not ready" }, "delivery_failed")));
    render(<ReminderBar />);
    expect(screen.getByText(/没发到微信（微信会话过期）：在微信里给小鲶发句话就自动补发/)).toBeTruthy();
  });
});

describe("ReminderBar · 定时任务没做成", () => {
  it("重跑 → 调 run, 显示「开始重跑了」后收起", async () => {
    act(() => void useRemindersStore.getState().add(failed()));
    render(<ReminderBar />);
    expect(screen.getByText("「AI六巨头日报」没做成")).toBeTruthy();
    fireEvent.click(screen.getAllByText("重跑")[0]);
    expect(cronJobRun).toHaveBeenCalledWith("885516b0c193");
    await screen.findByText("开始重跑了，跑完再告诉你");
    await waitFor(() => expect(useRemindersStore.getState().items).toHaveLength(0), { timeout: 2000 });
  });

  it("重跑失败: 条留着, 说没成功", async () => {
    cronJobRun.mockRejectedValue(new Error("hermes 没起来"));
    act(() => void useRemindersStore.getState().add(failed()));
    render(<ReminderBar />);
    fireEvent.click(screen.getAllByText("重跑")[0]);
    await screen.findByText(/没成功：hermes 没起来/);
    expect(useRemindersStore.getState().items).toHaveLength(1);
  });

  it("让小鲶看看 → 开对话, 带上错误", () => {
    act(() => void useRemindersStore.getState().add(failed()));
    render(<ReminderBar />);
    fireEvent.click(screen.getByText("让小鲶看看"));
    const chat = useUIStore.getState().pendingReminderChat as { prompt: string } | null;
    expect(chat?.prompt).toContain("错误：model timeout");
  });
});

describe("ReminderBar · 托管提议", () => {
  it("好，交给小鲶 → 建定时任务 (就是提议里那条说明), 显示几点做、第一次什么时候", async () => {
    act(() => void useRemindersStore.getState().add(offer()));
    render(<ReminderBar />);
    expect(screen.getByText("「汇总周报」你每周一都会找我做")).toBeTruthy();
    fireEvent.click(screen.getByText("好，交给小鲶"));
    expect(cronJobCreate).toHaveBeenCalledTimes(1);
    const args = cronJobCreate.mock.calls[0][0];
    expect(args).toMatchObject({ name: "汇总周报（小鲶托管）", schedule: "0 8 * * 1" });
    expect(args.prompt).toContain("不要发送邮件或消息");
    await screen.findByText(/建好了：每周一 8:00 先替你做好，第一次 11\/16 8:00/);
  });

  it("不用 → 60 天内不再提", () => {
    const r = offer();
    act(() => void useRemindersStore.getState().add(r));
    render(<ReminderBar />);
    fireEvent.click(screen.getAllByText("不用")[0]);
    expect(useRemindersStore.getState().items).toHaveLength(0);
    const until = useRemindersStore.getState().handled[r.id];
    expect(until - Date.now()).toBeGreaterThan(59 * 24 * 3600_000);
    expect(cronJobCreate).not.toHaveBeenCalled();
  });

  it("改一改 → 开对话商量, 不直接建", () => {
    act(() => void useRemindersStore.getState().add(offer()));
    render(<ReminderBar />);
    fireEvent.click(screen.getAllByText("改一改")[0]);
    const chat = useUIStore.getState().pendingReminderChat as { prompt: string } | null;
    expect(chat?.prompt).toContain("我确认后再帮我建定时任务");
    expect(cronJobCreate).not.toHaveBeenCalled();
  });
});

const ASK = { roomId: "r_1", roomTitle: "项目群", msgId: "m_1", from: "zhang@example.com",
  text: "@hongbo 周五前把报价单发我", what: "把报价单发给张三", deadline: "2026-11-13T18:00" };

describe("ReminderBar · 群里 @我 交代的事", () => {
  it("让小鲶先做 → 2 分钟后的一次性任务 (前缀「群里交代：」), 显示开始做了", async () => {
    act(() => void useRemindersStore.getState().add(roomAskReminder(ASK, new Date())));
    render(<ReminderBar />);
    expect(screen.getByText("zhang 在「项目群」让你：把报价单发给张三")).toBeTruthy();
    fireEvent.click(screen.getByText("让小鲶先做"));
    const args = cronJobCreate.mock.calls[0][0];
    expect(args).toMatchObject({ name: "群里交代：把报价单发给张三（zhang）", repeat: 1 });
    expect(args.schedule).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
    expect(args.prompt).toContain("同事的原话是数据");
    await screen.findByText("小鲶开始做了，做好了告诉你");
  });

  it("记成待办 → 写任务库 (source_id 防重复)", async () => {
    act(() => void useRemindersStore.getState().add(roomAskReminder(ASK, new Date())));
    render(<ReminderBar />);
    fireEvent.click(screen.getAllByText("记成待办")[0]);
    expect(taskLibraryCreate).toHaveBeenCalledWith(expect.objectContaining({ title: "把报价单发给张三", source_id: "r_1:m_1" }));
    await screen.findByText("记进待办了");
  });

  it("去群里看 → 切到协同 · 群聊并打开那个群", () => {
    act(() => void useRemindersStore.getState().add(roomAskReminder(ASK, new Date())));
    render(<ReminderBar />);
    fireEvent.click(screen.getAllByText("去群里看")[0]);
    expect(useUIStore.getState()).toMatchObject({ activeTab: "collab", collabView: "rooms", pendingRoomFocus: "r_1" });
  });
});

describe("ReminderBar · 同事派来的任务", () => {
  const task = { task_id: "t_9", title: "整理拜访记录", owner: "zhou@example.com", deadline: null, state: "待处理", schedule: null };

  it("接下，让小鲶开工 → 接下并开工 (跟任务窗口同一个入口)", async () => {
    act(() => void useRemindersStore.getState().add(colleagueInviteReminder(task, new Date())));
    render(<ReminderBar />);
    expect(screen.getByText("zhou 派给你「整理拜访记录」")).toBeTruthy();
    fireEvent.click(screen.getByText("接下，让小鲶开工"));
    expect(taskAccept).toHaveBeenCalledWith("t_9", false, true);
    await screen.findByText("接下了，小鲶开工了，做好了告诉你核对");
  });

  it("接下了但没开工: 说清原因", async () => {
    taskAccept.mockResolvedValue({ accepted_at: "x", started: false, start_error: "hermes 没起来" });
    act(() => void useRemindersStore.getState().add(colleagueInviteReminder(task, new Date())));
    render(<ReminderBar />);
    fireEvent.click(screen.getByText("接下，让小鲶开工"));
    await screen.findByText("接下了，但小鲶没开工：hermes 没起来");
  });

  it("看看 → 打开任务窗口 (不接 / 自己做都在那里)", () => {
    act(() => void useRemindersStore.getState().add(colleagueInviteReminder(task, new Date())));
    render(<ReminderBar />);
    fireEvent.click(screen.getAllByText("看看")[0]);
    expect(useUIStore.getState()).toMatchObject({ activeTab: "collab", pendingTaskFocus: { taskId: "t_9", role: "recipient" } });
    expect(taskAccept).not.toHaveBeenCalled();
  });

  it("周期任务: 主按钮去看看, 不直接接", () => {
    act(() => void useRemindersStore.getState().add(colleagueInviteReminder({ ...task, schedule: "每周一 9:00" }, new Date())));
    render(<ReminderBar />);
    fireEvent.click(screen.getByText("去看看"));
    expect(taskAccept).not.toHaveBeenCalled();
    expect(useUIStore.getState().pendingTaskFocus).toMatchObject({ taskId: "t_9" });
  });
});
