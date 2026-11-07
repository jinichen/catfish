// @vitest-environment jsdom
/** 三个自动提醒出口 (11/07): 待办 / 沉默触发器、早安播报、急邮件 ——
 * 都只挂到提醒条, 不往当前对话里塞消息、不写会话、不切页面。
 *
 * 之前它们都走 ui.startProactiveChat: 当前对话末尾插一条 assistant 消息 + sessionMessageAppend
 * 落库 + activeTab = "chat"。这里把那三件事逐个盯住。 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const tauri = vi.hoisted(() => ({
  remindersWeekFetch: vi.fn(),
  sendNotification: vi.fn(() => Promise.resolve()),
  sessionCreate: vi.fn(() => Promise.resolve({ id: "s-new", startedAt: 0 })),
  sessionMessageAppend: vi.fn(() => Promise.resolve(1)),
  emailDigestFetch: vi.fn(() => Promise.resolve("[]")),
  calendarWeekFetch: vi.fn(() => Promise.resolve("[]")),
}));
vi.mock("../lib/tauri", async (orig) => ({ ...(await orig<typeof import("../lib/tauri")>()), ...tauri }));

const fetchContextualStarter = vi.hoisted(() => vi.fn());
vi.mock("../lib/me", async (orig) => ({ ...(await orig<typeof import("../lib/me")>()), fetchContextualStarter }));

const briefing = vi.hoisted(() => ({
  fetchMergedBriefing: vi.fn(),
  fetchBriefingSuggestion: vi.fn(),
}));
vi.mock("../lib/briefing", async (orig) => ({ ...(await orig<typeof import("../lib/briefing")>()), ...briefing }));

import { installMemoryLocalStorage } from "../lib/__fixtures__/memoryLocalStorage";
import { handleUrgentEmail } from "../lib/urgentEmail";
import { useChatStore } from "../store/chat";
import { useEmailStore } from "../store/email";
import { reloadRemindersForTest, useRemindersStore } from "../store/reminders";
import { useUIStore } from "../store/ui";
import { fireOne } from "./useProactiveScheduler";
import { resetTriggerTasksCacheForTest, runTriggerTick } from "./useProactiveTriggers";

installMemoryLocalStorage();

const EXISTING = [
  { id: "u1", role: "user" as const, content: "帮我改一下这份方案", ts: new Date().toISOString(), status: "done" as const },
  { id: "a1", role: "assistant" as const, content: "好的, 改好了。", ts: new Date().toISOString(), status: "done" as const },
];

function expectConversationUntouched() {
  expect(useChatStore.getState().messages.map((m) => m.id)).toEqual(["u1", "a1"]);
  expect(useChatStore.getState().persistedSessionId).toBe("s-open");
  expect(tauri.sessionCreate).not.toHaveBeenCalled();
  expect(tauri.sessionMessageAppend).not.toHaveBeenCalled();
  expect(useUIStore.getState().activeTab).toBe("email");
}

beforeEach(() => {
  localStorage.clear();
  reloadRemindersForTest();
  resetTriggerTasksCacheForTest();
  vi.clearAllMocks();
  useChatStore.setState({ messages: EXISTING, persistedSessionId: "s-open", model: "m" });
  useUIStore.setState({ activeTab: "email" });
  useEmailStore.setState({ readIds: new Set<string>() });
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
});

describe("待办到期 (useProactiveTriggers)", () => {
  it("挂到提醒条, 字是任务库事实, 不过 LLM; 不碰当前对话、不切页面", async () => {
    const now = new Date(2026, 10, 7, 10, 0);
    tauri.remindersWeekFetch.mockResolvedValue(JSON.stringify([
      { task_id: "reminders:r1", text: "11/9 材料闭环", section: "资质集采二期", due_date_iso: "2026-11-08T18:00:00" },
    ]));
    const t = await runTriggerTick({ now, lastFocusLeftTs: null, lastFocusReturnTs: null });
    expect(t?.kind).toBe("deadline");
    const items = useRemindersStore.getState().items;
    expect(items).toHaveLength(1);
    expect(items[0].text).toBe("待办「11/9 材料闭环」明天 18:00 到期");
    expect(items[0].meta).toBe("来自 资质集采二期");
    expect(items[0].task?.taskId).toBe("reminders:r1");
    expect(fetchContextualStarter).not.toHaveBeenCalled();
    expect(tauri.sendNotification).not.toHaveBeenCalled(); // 人在 Companion 里, 不发通知
    expectConversationUntouched();
  });

  it("窗口不在前台: 发一条系统通知叫人回来; 同一条下一轮不再命中 (不白占每天名额)", async () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(false);
    const now = new Date(2026, 10, 7, 10, 0);
    tauri.remindersWeekFetch.mockResolvedValue(JSON.stringify([
      { task_id: "t1", text: "交标书", due_date_iso: "2026-11-07T17:00:00" },
    ]));
    await runTriggerTick({ now, lastFocusLeftTs: null, lastFocusReturnTs: null });
    expect(tauri.sendNotification).toHaveBeenCalledWith("⏰ 待办快到期", "待办「交标书」今天 17:00 到期");
    expect(useRemindersStore.getState().notifiedAt).toBe(now.getTime());
    const again = await runTriggerTick({ now: new Date(now.getTime() + 40 * 60_000), lastFocusLeftTs: null, lastFocusReturnTs: null });
    expect(again).toBeNull();
    expect(useRemindersStore.getState().items).toHaveLength(1);
    expectConversationUntouched();
  });

  it("总开关关了就什么都不挂", async () => {
    localStorage.setItem("catfish:proactive_enabled", "false");
    tauri.remindersWeekFetch.mockResolvedValue(JSON.stringify([
      { task_id: "t1", text: "交标书", due_date_iso: "2026-11-07T17:00:00" },
    ]));
    expect(await runTriggerTick({ now: new Date(2026, 10, 7, 10, 0), lastFocusLeftTs: null, lastFocusReturnTs: null })).toBeNull();
    expect(useRemindersStore.getState().items).toHaveLength(0);
  });

  it("沉默信号: 挂「聊聊」, 也不往对话里塞", async () => {
    tauri.remindersWeekFetch.mockResolvedValue("[]");
    fetchContextualStarter.mockResolvedValue({ starter: "方案改到哪了? 卡住我帮你看", source: "llm" });
    const now = new Date(2026, 10, 7, 11, 0);
    useChatStore.setState({
      messages: [
        // 沉默信号只看「员工说了一句、之后没动静」: 末尾是员工那句
        { ...EXISTING[1], ts: new Date(now.getTime() - 41 * 60_000).toISOString() },
        { ...EXISTING[0], ts: new Date(now.getTime() - 40 * 60_000).toISOString() },
      ],
    });
    const t = await runTriggerTick({ now, lastFocusLeftTs: null, lastFocusReturnTs: null });
    expect(t?.kind).toBe("silence");
    const [r] = useRemindersStore.getState().items;
    expect(r.actionLabel).toBe("聊聊");
    expect(r.text).toBe("方案改到哪了? 卡住我帮你看");
    expect(r.prompt).toContain("帮我改一下这份方案");
    expect(useChatStore.getState().messages).toHaveLength(2);
    expect(tauri.sessionMessageAppend).not.toHaveBeenCalled();
    expect(useUIStore.getState().activeTab).toBe("email");
  });
});

describe("早安播报 (useProactiveScheduler)", () => {
  it("挂「早安播报」到提醒条 + 系统通知照发; 不碰当前对话、不切页面", async () => {
    tauri.remindersWeekFetch.mockResolvedValue(JSON.stringify([{ text: "交标书" }]));
    briefing.fetchMergedBriefing.mockResolvedValue({ suggestion: "今天先把标书交了, 下午周会", todos: [] });
    await fireOne("09:00", "morning_briefing");
    const [r] = useRemindersStore.getState().items;
    expect(r.kind).toBe("briefing");
    expect(r.text).toBe("早安播报");
    expect(r.meta).toBe("今天先把标书交了, 下午周会");
    expect(r.actionLabel).toBe("展开看看");
    expect(tauri.sendNotification).toHaveBeenCalledWith("☀️ 小鲶的早安播报", "今天先把标书交了, 下午周会");
    expectConversationUntouched();
  });
});

describe("急邮件 (App catfish:email-urgent)", () => {
  it("挂「N 封急邮件」, 读过的不算; 不碰当前对话、不切页面", () => {
    useEmailStore.setState({ readIds: new Set(["m1"]) });
    const ok = handleUrgentEmail({
      count: 2,
      starter: "你有 2 封紧的邮件",
      ids: ["m1", "m2"],
      items: [{ sender: "张三 <z@x.com>", subject: "已读那封" }, { sender: "李四 <l@x.com>", subject: "合同今天签" }],
    }, new Date(2026, 10, 7, 10, 0));
    expect(ok).toBe(true);
    const [r] = useRemindersStore.getState().items;
    expect(r.text).toBe("1 封急邮件");
    expect(r.meta).toBe("李四 — 合同今天签");
    expect(r.prompt).toContain('email_id: "m2"');
    expect(r.prompt).not.toContain("m1");
    expectConversationUntouched();
  });

  it("全都读过就不挂", () => {
    useEmailStore.setState({ readIds: new Set(["m1"]) });
    expect(handleUrgentEmail({ count: 1, starter: "x", ids: ["m1"], items: [{ sender: "a", subject: "b" }] })).toBe(false);
    expect(useRemindersStore.getState().items).toHaveLength(0);
  });
});
