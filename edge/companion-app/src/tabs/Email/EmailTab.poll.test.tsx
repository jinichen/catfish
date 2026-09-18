/**
 * @vitest-environment jsdom
 */
/** 邮件页后台轮询: 会转、不叠、断网不清屏。
 *
 * # 为什么这个改动值得单独一个文件
 *
 * 隔壁 EmailTab.selfLoop.test.tsx 记着 8/15 那次: 一个 effect 依赖自己写的
 * state, 83 分钟烧掉 2470 万 token。轮询是往同一个组件里再加一个**自己会重复
 * 发请求的东西**, 出错的形状是一样的 —— 界面上什么都看不出来, 代价在别处。
 *
 * 所以这里钉的不是"能不能刷新出新邮件", 而是三条会把人坑到的性质:
 *
 *   ① 定时器真的会转 (不转就等于没做)
 *   ② 上一轮没回来, 下一轮不许发 —— 否则慢网络上会叠成一串并发 IMAP 登录
 *   ③ 后台那轮失败, 不许把好好的列表换成错误条 —— 断一下网就清屏比不刷新糟
 *
 * ②③ 都是"不会发生的事", 只能靠测试守; 真出了问题, 现场是看不出来的。
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, act, fireEvent } from "@testing-library/react";

let inboxFetches = 0;
let inboxBehavior: () => Promise<string> = async () => "[]";

vi.mock("../../lib/tauri", () => ({
  emailListFetch: vi.fn(async (_unread: boolean, _limit: number, folder?: string) => {
    if (folder === "Sent") return "[]";
    inboxFetches += 1;
    return inboxBehavior();
  }),
  emailAccountsFetch: vi.fn(async () => "[]"),
  emailReadMessage: vi.fn(async () => "{}"),
  emailCheckNew: vi.fn(async () => undefined),
  emailMailDirStatus: vi.fn(async () => "ok"),
  emailPoliticalGet: vi.fn(async () => ({})),
  emailClassifyNow: vi.fn(async () => ({ urgency: {}, actions: {} })),
  emailPhishingScanNow: vi.fn(async () => ({})),
}));

vi.mock("../../store/ui", () => ({
  useUIStore: (sel: (s: unknown) => unknown) =>
    sel({ startEmailChat: vi.fn(), startProactiveChat: vi.fn(), setActiveTab: vi.fn() }),
}));
vi.mock("../../store/agent", () => ({
  useAgentStore: (sel: (s: { name: string; personality: string }) => unknown) =>
    sel({ name: "小鲶", personality: "" }),
}));
vi.mock("../../store/email", () => {
  const snapshot = () => ({
    urgencyMap: {}, actionMap: {}, listCache: null,
    setUrgencyMap: () => undefined,
    reconcileFromRust: async () => ({}),
    markRead: () => undefined,
    setListCache: () => undefined,
    mergeActionMap: () => undefined,
  });
  const hook = (sel: (s: ReturnType<typeof snapshot>) => unknown) => sel(snapshot());
  return { useEmailStore: Object.assign(hook, { getState: snapshot }) };
});

import EmailTab, { EMAIL_POLL_MS } from "./EmailTab";

/** 一封邮件的最小形状 —— 列表渲染要的就这些。 */
const oneMail = JSON.stringify([
  {
    id: "id-0", subject: "在建项目清单", sender: "ff_nic@chinatelecom.cn",
    account: "me@chinatelecom.cn", date: "2026-09-18T13:05:57", is_read: false,
  },
]);

/** 推进假定时器并把 React 的更新冲干净。
 *
 * 少了 act() 的话 state 更新落在 React 的批处理外面, DOM 断言读到的是上一帧,
 * 测试会以一种跟被测代码无关的方式飘。 */
async function tick(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

describe("EmailTab 后台轮询", () => {
  beforeEach(() => {
    inboxFetches = 0;
    inboxBehavior = async () => oneMail;
    vi.useFakeTimers();
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("定时器真的会转 —— 过一个间隔就再拉一次", async () => {
    render(<EmailTab />);
    await tick(0);
    expect(inboxFetches).toBe(1);

    await tick(EMAIL_POLL_MS + 10);
    expect(inboxFetches).toBe(2);
  });

  it("没到点不许自己发 —— 挂载后什么都不做就不该再拉", async () => {
    render(<EmailTab />);
    await tick(0);
    // 远小于一个轮询间隔。会自触发的写法在这里就会露馅 (见 selfLoop 那个文件)。
    await tick(EMAIL_POLL_MS / 2);
    expect(inboxFetches).toBe(1);
  });

  it("上一轮没回来, 后面几轮不叠罗汉", async () => {
    // 永远不 resolve —— 模拟 IMAP 登录卡住 (这在国内网络上不稀奇)
    inboxBehavior = () => new Promise<string>(() => {});
    render(<EmailTab />);
    await tick(0);
    expect(inboxFetches).toBe(1);

    await tick(EMAIL_POLL_MS * 5);
    expect(
      inboxFetches,
      "第一轮还卡着, 后面五个间隔一次都不该发 —— 叠起来就是五个并发 IMAP 登录",
    ).toBe(1);
  });

  it("挡的只是后台那轮 —— 员工自己按的不许吞", async () => {
    render(<EmailTab />);
    await tick(0);
    expect(inboxFetches).toBe(1);

    // 让后台那轮卡住不回来
    inboxBehavior = () => new Promise<string>(() => {});
    await tick(EMAIL_POLL_MS + 10);
    expect(inboxFetches).toBe(2);

    // 此刻有一轮在途。「收信」最后会调 loadList (前台) —— 按了没反应比多发
    // 一次请求糟得多, 所以闸只挡后台。用布尔当闸就会把这次吞掉。
    fireEvent.click(screen.getByText("收信"));
    await tick(0);
    expect(inboxFetches, "员工按下的那次被闸吞了").toBe(3);
  });

  it("后台那轮失败, 列表照旧, 不弹错误条", async () => {
    render(<EmailTab />);
    await tick(0);
    expect(screen.getByText("在建项目清单")).toBeTruthy();

    inboxBehavior = async () => { throw new Error("网络断了"); };
    await tick(EMAIL_POLL_MS + 10);

    expect(screen.getByText("在建项目清单"), "断一次网就清屏, 比不刷新糟得多").toBeTruthy();
    expect(screen.queryByText(/网络断了/)).toBeNull();
  });

  it("但手里本来就是空的, 后台失败必须说话", async () => {
    // 空收件箱 + 一句解释都没有 = Foxmail 那次的排查地狱, 不能再来一遍
    inboxBehavior = async () => "[]";
    render(<EmailTab />);
    await tick(0);

    inboxBehavior = async () => { throw new Error("网络断了"); };
    await tick(EMAIL_POLL_MS + 10);

    expect(screen.queryByText(/网络断了/)).toBeTruthy();
  });
});
