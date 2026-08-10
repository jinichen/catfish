/** picker 落盘闸 — 只有员工的选择才准写进 ~/.catfish (8/10).
 *
 * 跑法: npx vitest run src/store/picker_persist.test.ts
 *
 * ── 这个测试挡的是什么 ──────────────────────────────────────────────
 *
 * 8/10 现场: 员工 picker 选了内网 Qwen3-VL, 主聊天确实走内网, 但网关日志里
 * 一大片 `catfish-public-deepseek-flash`。查下来是
 *
 *   roles.yaml `chat_default: catfish-public-deepseek-flash`
 *     → gateway catalog.py role_resolver("chat_default") → catalog.default
 *     → ChatTab effect: setModelInStore(catalog.default, **false**)
 *     → store/chat.ts setModel: invoke("set_picker_model") ← 写盘那句在判断外面
 *     → ~/.catfish/picker_state.json 被写成 deepseek
 *     → hermes 侧 memory_enforce / catfish-memory 读文件 → 全跑公网
 *
 * 那个 `false` 的字面意思就是"这不是员工选的", 代码却照写不误。
 *
 * 为什么必须有测试: 这个 bug **在界面上完全看不出来** —— picker 显示的是员工
 * 选的模型, 主聊天也真的在用它 (model 走请求体), 只有读文件的那几个后台
 * 组件被带偏。不写测试, 下次谁把这句挪出 if 就又静默复发。
 *
 * 两个方向都测 —— 只测一边的话:
 *   漏测 false → 回归到 8/10 的污染
 *   漏测 true  → 把闸焊死, 员工选了也不落盘, 后台永远跟不上 picker (另一种坏)
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

const invokeMock = vi.fn(() => Promise.resolve());
vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...args: unknown[]) => invokeMock(...args),
}));

const _ls = new Map<string, string>();
beforeEach(() => {
  _ls.clear();
  invokeMock.mockReset();
  invokeMock.mockReturnValue(Promise.resolve());
  (globalThis as unknown as { localStorage: Storage }).localStorage = {
    length: 0,
    getItem: (k: string) => _ls.get(k) ?? null,
    setItem: (k: string, v: string) => { _ls.set(k, v); },
    removeItem: (k: string) => { _ls.delete(k); },
    clear: () => _ls.clear(),
    key: (i: number) => Array.from(_ls.keys())[i] ?? null,
  };
});

/** 只挑写 picker 文件那一次 —— setModel 还会 invoke codex_backend_select_model,
 *  不能拿 invokeMock 的总调用次数当判据 (会把两件事混在一起)。 */
function pickerWrites(): string[] {
  return invokeMock.mock.calls
    .filter((c) => c[0] === "set_picker_model")
    .map((c) => (c[1] as { name: string }).name);
}

describe("picker 落盘闸", () => {
  it("员工点 picker (pickedByUser=true) → 落盘", async () => {
    const { useChatStore } = await import("./chat");
    useChatStore.getState().setModel("catfish-private-vision", true);
    expect(pickerWrites()).toEqual(["catfish-private-vision"]);
    expect(useChatStore.getState().modelPickedByUser).toBe(true);
  });

  it("catalog.default 自动 propagate (pickedByUser=false) → **不落盘**", async () => {
    const { useChatStore } = await import("./chat");
    // 这就是 ChatTab.tsx 那个 effect 干的事
    useChatStore.getState().setModel("catfish-public-deepseek-flash", false);

    expect(pickerWrites()).toEqual([]);
    // store 仍然要跟上 —— 新机器还没选过时 UI 得有东西显示, 只是不算"选择"
    expect(useChatStore.getState().model).toBe("catfish-public-deepseek-flash");
    expect(useChatStore.getState().modelPickedByUser).toBe(false);
  });

  it("默认参数就是 true —— 老 caller (picker onChange) 行为不变", async () => {
    const { useChatStore } = await import("./chat");
    useChatStore.getState().setModel("catfish-public-qwen-flash");
    expect(pickerWrites()).toEqual(["catfish-public-qwen-flash"]);
  });

  it("自动 propagate 之后员工再选 → 员工那次写的是员工选的值", async () => {
    const { useChatStore } = await import("./chat");
    // 复现现场顺序: 先被 catalog 灌一次, 再由员工选
    useChatStore.getState().setModel("catfish-public-deepseek-flash", false);
    useChatStore.getState().setModel("catfish-private-vision", true);
    // 落盘的只能有员工那一次, 且不能被 deepseek 污染
    expect(pickerWrites()).toEqual(["catfish-private-vision"]);
  });
});

/** 第二条污染路径的静态断言 —— lib/chat.ts 每次 send 都写盘的那段.
 *
 * 单靠上面的 store 测试挡不住它: streamChat 直接 import picker_state,
 * 绕过 setModel。只堵 store 那条, 下一次 send 就会把 catalog.default 重新
 * 写回去 —— 「修了一条漏一条」。
 *
 * 用读源码的方式钉, 不去跑 streamChat (它要 mock 掉 fetch/SSE/hermes 路由,
 * 成本远大于收益, 而且测不到"有没有这一句"这个点)。
 */
describe("streamChat 不许写 picker 文件", () => {
  it("lib/chat.ts 里没有 savePickerState 调用", async () => {
    const fs = await import("node:fs");
    const url = await import("node:url");
    const path = await import("node:path");
    const here = path.dirname(url.fileURLToPath(import.meta.url));
    const src = fs.readFileSync(path.join(here, "..", "lib", "chat.ts"), "utf8");

    // 注释里提这个名字是允许的 (那段说明为什么删), 只禁真的调用。
    // 去注释再断言 —— 8/9 在 gateway 那边就栽过一次: 断言的字符串
    // 被自己的解释性注释命中, 闸红了但红错地方。
    //
    // ⚠ 剥注释**只剥整行的**, 不剥行尾的 `// ...`。
    // 行尾剥法要写成 `l.replace(/\/\/.*$/, "")`, 而这一刀会砍进字符串字面量 ——
    // `const u = "http://x"; savePickerState(m);` 会被剥成 `const u = "http:`,
    // 真调用消失, 闸变成**恒绿**。宁可反过来错: 行尾注释里写了这个名字就误报,
    // 误报是红的、看得见, 恒绿是看不见的。
    const code = src
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .split("\n")
      .filter((l) => {
        const t = l.trim();
        return !t.startsWith("//") && !t.startsWith("*");
      })
      .join("\n");

    expect(code).not.toMatch(/savePickerState\s*\(/);
    expect(code).not.toMatch(/set_picker_model/);
  });
});
