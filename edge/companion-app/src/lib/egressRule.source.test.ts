// 11/19 鸿波「companion 也应该和中央端一样的表述」(截图: 外发记录一列 plugin:toolbridge-learning, 中央显示「学习 · 摸底 / 讲解」)
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { SOURCE_PURPOSE, displayPurpose, egressPurpose, sourceLabel } from "./egressRule";

describe("来源标签 → 中文用途", () => {
  it("早先记下的原始标签, 显示时翻成中文", () => {
    expect(displayPurpose("plugin:toolbridge-learning", "127.0.0.1")).toBe("学习 · 摸底 / 讲解");
    expect(displayPurpose("plugin:toolbridge-learning-cards", "127.0.0.1")).toBe("学习 · 做复习卡");
    expect(displayPurpose("plugin:memory-wiki-ground", "127.0.0.1")).toBe("知识卡核对");
  });

  it("没登记的跟中央一样兜底, 不原样露出", () => {
    expect(sourceLabel("plugin:toolbridge-new-thing")).toBe("小鲶后台 · new-thing");
    expect(sourceLabel("plugin:memory-newkind")).toBe(SOURCE_PURPOSE["plugin:memory"]); // 前缀「plugin:memory」兜住, 跟 egress_log.py 一样
  });

  it("前缀匹配, 长的优先 (跟 egress_log.py 一样)", () => {
    expect(egressPurpose("http://127.0.0.1:8999/v1/chat/completions?catfish_source=plugin:toolbridge-learning-drill")).toBe("学习 · 答辩陪练");
    expect(egressPurpose("http://x/v1?catfish_source=hermes:aux:vision")).toBe(SOURCE_PURPOSE["hermes:aux:vision"]);
  });

  it("跟 tool-bridge egress_log.py 的 _SOURCE_PURPOSE 同一套 (那边有的这边都有, 叫法一样)", () => {
    const py = readFileSync(resolve(__dirname, "../../../tool-bridge/src/catfish_tool_bridge/egress_log.py"), "utf8");
    const block = py.split("_SOURCE_PURPOSE = {")[1].split("\n}")[0];
    const pairs = [...block.matchAll(/"([^"]+)":\s*"([^"]+)"/g)].map((m) => [m[1], m[2]]);
    expect(pairs.length).toBeGreaterThan(50);
    for (const [k, v] of pairs) expect(SOURCE_PURPOSE[k], k).toBe(v);
  });

  it("已经是中文 / 网址路径的照旧", () => {
    expect(displayPurpose("学习 · 练习", "127.0.0.1")).toBe("学习 · 练习");
    expect(displayPurpose("/api/room-link/keys", "127.0.0.1")).toBe("同事协作 · 加密公钥");
  });
});
