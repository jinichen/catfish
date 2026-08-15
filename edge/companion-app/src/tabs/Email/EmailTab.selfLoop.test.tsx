/**
 * @vitest-environment jsdom
 *
 * (只给这个文件开 jsdom。仓库里 225 条测试都跑在默认的 node 环境, 不去动
 *  vite.config.ts 的全局 test.environment —— 那会把所有测试的运行环境换掉,
 *  为了一个文件不值当。)
 */
/** EmailTab 的评级 / 钓鱼扫描 effect 不许自触发。
 *
 * # 复现的是什么
 *
 * 8/15 百炼周配额 07:54 重置、09:17 就烧空了。查 PG gateway_audit:
 * 83 分钟约 3000 万 token, companion-email-scheduler 一家占 2470 万 (83%)。
 * 再查 hermes agent.log: 09:03–09:17 打了 **527 次评级请求, 却只对应 87 种
 * 不同的首封标题**, 最狠一封被重复评了 127 次, 间隔 4–5 秒。
 *
 * 根因是三处咬在一起:
 *
 *   1. 这两个 effect 依赖自己写的 state:
 *          }, [items, urgencyMap])   // 体内又调 setUrgencyMap
 *      (那行上面的注释写的是「依赖 items.length + 第一条 id」—— 代码跟自己的
 *       注释对不上, 说明本意就不是这样)
 *   2. Rust 侧 urgency_cache 上限 200 < 列表上限 500 → 返回的 map 缺条目
 *   3. store 的 setUrgencyMap 是**整份覆盖**不是 merge → 缺的条目把本地更全的
 *      记录抹掉
 *
 * 于是: 评完一批 → 缓存超限丢一半 → 返回的 map 变小 → 覆盖本地 → 前端认为
 * 没评的**更多了** → 再评。永动机。
 *
 * # 这个文件测的是第 1 处
 *
 * 后两处各有自己的守卫 (email_scheduler.rs 的
 * `urgency_cache_上限必须大于列表上限`, 以及 store 里 merge 的注释)。
 * 这里专测「后端返回一个残缺的 map 时, 前端不会因此重评」—— 也就是把第 2、3
 * 处的故障**当成输入喂进来**, 看第 1 处扛不扛得住。三道防线互相独立, 塌一道
 * 不该塌全部。
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, waitFor, cleanup } from "@testing-library/react";

const classifyCalls: string[][] = [];
const phishingCalls: string[][] = [];

vi.mock("../../lib/tauri", () => ({
  // 列表: 固定 5 封
  emailListFetch: vi.fn(async (_unread: boolean, _limit: number, folder?: string) =>
    folder === "Sent"
      ? "[]"
      : JSON.stringify(
          Array.from({ length: 5 }, (_, i) => ({
            id: `id-${i}`,
            subject: `主题 ${i}`,
            sender: `sender${i}@x.com`,
            account: "a@x.com",
            date: "2026-08-15T09:00:00",
            is_read: false,
          })),
        ),
  ),
  emailAccountsFetch: vi.fn(async () => "[]"),
  emailReadMessage: vi.fn(async () => "{}"),
  emailCheckNew: vi.fn(async () => undefined),
  emailMailDirStatus: vi.fn(async () => "ok"),
  emailPoliticalGet: vi.fn(async () => ({})),

  // ★ 关键: 永远返回一个**残缺**的 map (只认 id-0), 模拟 Rust 缓存超限丢条目。
  //   老实现会因为"其余 4 封还没评"而反复重评 —— 永远停不下来。
  emailClassifyNow: vi.fn(async (items: { id: string }[]) => {
    classifyCalls.push(items.map((i) => i.id));
    return { "id-0": "中" };
  }),
  emailPhishingScanNow: vi.fn(async (items: { id: string }[]) => {
    phishingCalls.push(items.map((i) => i.id));
    return {};   // 一条都不认, 更极端
  }),
}));

vi.mock("../../store/ui", () => ({
  useUIStore: (sel: (s: unknown) => unknown) =>
    sel({ startProactiveChat: vi.fn(), setActiveTab: vi.fn() }),
}));
vi.mock("../../store/agent", () => ({
  useAgentStore: (sel: (s: { name: string; personality: string }) => unknown) =>
    sel({ name: "小鲶", personality: "" }),
}));

// store/email: 真实现的 merge 语义在这里不重要 —— 我们要测的是"即使 map
// 一直不增长, effect 也不能重发". 用最朴素的覆盖语义 (也就是修之前那版),
// 让第 1 道防线单独受考验。
let _urgency: Record<string, string> = {};
vi.mock("../../store/email", () => ({
  useEmailStore: (sel: (s: unknown) => unknown) =>
    sel({
      urgencyMap: _urgency,
      setUrgencyMap: (m: Record<string, string>) => {
        _urgency = m;               // 故意整份覆盖
      },
      reconcileFromRust: async () => ({}),
      markRead: vi.fn(),
    }),
}));

import EmailTab from "./EmailTab";

describe("EmailTab 评级/扫描 effect 不自触发", () => {
  beforeEach(() => {
    classifyCalls.length = 0;
    phishingCalls.length = 0;
    _urgency = {};
  });
  afterEach(() => cleanup());

  it("后端返回残缺 map 时, 同一封邮件不会被反复评级", async () => {
    render(<EmailTab />);

    // 等第一批评级发出去
    await waitFor(() => expect(classifyCalls.length).toBeGreaterThan(0));
    // 再给足时间让"如果会自触发"的重跑发生
    await new Promise((r) => setTimeout(r, 300));

    const flat = classifyCalls.flat();
    const dupes = flat.filter((id, i) => flat.indexOf(id) !== i);
    expect(dupes, `这些 id 被重复评级了: ${JSON.stringify(dupes)}`).toEqual([]);

    // 5 封 / 每批 30 → 正好 1 次调用。老实现在这里是停不下来的。
    expect(classifyCalls.length).toBe(1);
  });

  it("钓鱼扫描同理 —— 后端一条都不认也不重扫", async () => {
    render(<EmailTab />);
    await waitFor(() => expect(phishingCalls.length).toBeGreaterThan(0));
    await new Promise((r) => setTimeout(r, 300));

    const flat = phishingCalls.flat();
    const dupes = flat.filter((id, i) => flat.indexOf(id) !== i);
    expect(dupes, `这些 id 被重复扫描了: ${JSON.stringify(dupes)}`).toEqual([]);
    expect(phishingCalls.length).toBe(1);
  });

  it("失败的批次不重试 —— 重试一次的代价是每封约 1 万 token", async () => {
    const { emailClassifyNow } = await import("../../lib/tauri");
    (emailClassifyNow as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("gateway 挂了"),
    );

    render(<EmailTab />);
    await new Promise((r) => setTimeout(r, 300));

    // 只发过一次 (那次失败了), 不会因为 badge 还空着就再来一遍
    expect(classifyCalls.length).toBeLessThanOrEqual(1);
  });
});
