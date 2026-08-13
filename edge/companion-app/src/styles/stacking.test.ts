/** 层叠上下文与浮层的连带关系 —— 8/10.
 *
 * ## 在防什么
 *
 * 8/1 给模型选择器加了两个浮层: `.chat-model-picker__hint`（「这个模型要新开
 * 一个对话才会生效」+ 那个「新建对话」按钮）和 `.chat-model-picker__error`。
 * 它们 `position: absolute` 从 header 往下溢出到消息区, 各自写了 `z-index: 20`。
 * 当时是好的。
 *
 * 8/8 `bd4747b`「同步新版 UI」给 `.chat-workspace__header` 加了
 * `backdrop-filter: blur(14px)`。**backdrop-filter 只要不是 none 就建立层叠
 * 上下文** —— 那个 20 从此只在 header 内部有效, 升不出去; 而 header 自己是
 * `z-index: auto` 的普通流元素, `.chat-workspace__panel` 在 DOM 里排它后面,
 * 于是消息面板整个画在浮层上面。
 *
 * 症状特别难查: 浮层**看得见**（面板那块是透明的, 透过来了）, 但**点不着**
 * —— 点击全被面板接走。员工看到的是"这个按钮点了没反应", 而 React 那边接线
 * 完全正常, 查半天查不出东西。两次改动隔了 7 天、分属 CSS 和 TSX 两个文件,
 * 中间没有任何报错。
 *
 * ## 这里检查什么
 *
 * 凡是"挂着往外溢出的浮层"的容器, 一旦获得任何一个会建立层叠上下文的属性,
 * 就必须同时有 `z-index` 把自己抬上去。少一个都不行。
 *
 * 这个检查读 CSS 源文本, 不需要浏览器 —— jsdom 也做不了布局和绘制, 这条
 * 只能靠源码层面的不变式盯住。
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/** 会建立层叠上下文的属性（取值不是"默认值"时）。
 *  见 https://developer.mozilla.org/docs/Web/CSS/CSS_positioned_layout/Stacking_context */
const STACKING_PROPS: Record<string, (v: string) => boolean> = {
  "backdrop-filter": (v) => v !== "none",
  "-webkit-backdrop-filter": (v) => v !== "none",
  transform: (v) => v !== "none",
  filter: (v) => v !== "none",
  perspective: (v) => v !== "none",
  "mix-blend-mode": (v) => v !== "normal",
  isolation: (v) => v === "isolate",
  contain: (v) => /\b(layout|paint|strict|content)\b/.test(v),
  "will-change": (v) => /\b(transform|opacity|filter|backdrop-filter)\b/.test(v),
  opacity: (v) => parseFloat(v) < 1,
};

/** 挂着溢出浮层的容器。往这里加条目, 而不是放宽检查。 */
const POPOVER_HOSTS = [
  {
    selector: ".chat-workspace__header",
    popovers: [".chat-model-picker__hint", ".chat-model-picker__error"],
    note: "模型选择器的两个浮层从这里往下溢出到消息区",
  },
];

const CSS_FILES = ["globals.css", "refresh.css"];

/** 极简 CSS 规则提取: 返回该选择器在所有文件里的声明合并结果（后加载的覆盖前面）。
 *
 *  **必须先剥注释**。第一版没剥, 于是选择器前面那段中文注释被算进了选择器
 *  文本里, 精确匹配 `=== selector` 永远不成立 —— 整个检查静默退化成"一条规则
 *  都没找到, 所以没有违规", 在 CI 上是一片绿。下面 "CSS 读得到" 那条用例就是
 *  专门盯这个失效方式的。 */
function declarationsFor(selector: string): Record<string, string> {
  const merged: Record<string, string> = {};
  for (const file of CSS_FILES) {
    const css = readFileSync(join(__dirname, file), "utf8").replace(
      /\/\*[\s\S]*?\*\//g,
      "",
    );
    for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const hit = m[1]
        .split(",")
        .map((s) => s.trim())
        .some((s) => s === selector);
      if (!hit) continue;
      for (const decl of m[2].split(";")) {
        const i = decl.indexOf(":");
        if (i < 0) continue;
        const prop = decl.slice(0, i).trim();
        const val = decl.slice(i + 1).trim();
        if (prop) merged[prop] = val;
      }
    }
  }
  return merged;
}

describe("层叠上下文 × 溢出浮层", () => {
  it("CSS 读得到, 解析出来的不是空的", () => {
    // 没有这一条, 下面的断言会在"正则没匹配上"时静默全绿 —— 那是这类
    // 源码检查最常见的失效方式。
    const d = declarationsFor(".chat-workspace__header");
    expect(Object.keys(d).length).toBeGreaterThan(2);
    expect(d["backdrop-filter"]).toBeTruthy();
  });

  it.each(POPOVER_HOSTS)(
    "$selector 建了层叠上下文就必须有 z-index",
    ({ selector, popovers, note }) => {
      const decls = declarationsFor(selector);
      const creators = Object.entries(STACKING_PROPS)
        .filter(([prop, isCreating]) => decls[prop] && isCreating(decls[prop]))
        .map(([prop]) => `${prop}: ${decls[prop]}`);

      if (creators.length === 0) return; // 没建上下文, 不需要 z-index

      expect(
        decls["z-index"],
        `${selector} 有 ${creators.join(" / ")} —— 这会建立层叠上下文, ` +
          `把 ${popovers.join(" / ")} 的 z-index 关在里面升不出去, ` +
          `结果是浮层看得见但点不着 (${note})。` +
          `给 ${selector} 补上 position + z-index。`,
      ).toBeTruthy();
    },
  );

  it("浮层自己的 z-index 还在（被删掉就没有抬升的意义了）", () => {
    for (const { popovers } of POPOVER_HOSTS) {
      for (const p of popovers) {
        const d = declarationsFor(p);
        expect(d["position"], `${p} 应该是 absolute`).toBe("absolute");
        expect(Number(d["z-index"]), `${p} 缺 z-index`).toBeGreaterThan(0);
      }
    }
  });

  it("host 的 z-index 要低于全局遮罩层（不能盖住弹窗和 toast）", () => {
    const header = Number(declarationsFor(".chat-workspace__header")["z-index"]);
    expect(header).toBeGreaterThan(0);
    // .app-rail = 20 / 安装弹窗 = 1000 / undo toast = 2000, 都该在 header 之上
    expect(header).toBeLessThan(20);
  });
});
