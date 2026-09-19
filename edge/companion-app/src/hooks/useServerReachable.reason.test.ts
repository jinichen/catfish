/** 连不上服务器时那句提示, 不许说 "undefined"。
 *
 * 9/19 鸿波截图:
 *
 *     认证服务不通 (http://127.0.0.1:8998): undefined
 *
 * 原实现是 `(res.reason as Error).message`。那个 `as` 是个谎 —— 通过 Tauri
 * 代理层 reject 出来的不一定是 Error。不是 Error 时 `.message` 就是
 * undefined, 拼进模板变成字面的 "undefined"。
 *
 * 这比没有提示更糟: 它长得像"我们报了原因", 员工照着这句话什么也查不了,
 * 而真正的原因 (超时?拒绝连接?证书?) 被吞掉了。
 */
import { describe, expect, it } from "vitest";

import { reasonText } from "./useServerReachable";

describe("reasonText", () => {
  it("Error 用它的 message", () => {
    expect(reasonText(new Error("connection refused"))).toBe("connection refused");
  });

  it("字符串 reject 原样带出来 —— 这是 Tauri 层最常见的形态", () => {
    expect(reasonText("Network request failed")).toBe("Network request failed");
  });

  it("带 message 字段的普通对象也认", () => {
    expect(reasonText({ message: "timeout after 3000ms" })).toBe("timeout after 3000ms");
  });

  it("什么都没有时也得说清楚, 绝不吐 undefined", () => {
    for (const v of [undefined, null, {}, 0, false]) {
      const out = reasonText(v);
      expect(out).not.toContain("undefined");
      expect(out.length).toBeGreaterThan(0);
    }
  });

  it("message 是空串 / 空白时不当成有效原因", () => {
    expect(reasonText(new Error(""))).not.toBe("");
    expect(reasonText({ message: "   " })).not.toBe("   ");
  });

  it("message 不是字符串时, 把整个对象如实 JSON 出来", () => {
    // 这条第一版我写反了, 断言的是 `not.toContain("42")` —— 想当然地认为
    // "message 不是字符串就该丢掉"。跑出来红了才想清楚: `{"message":42}`
    // 恰恰是**好输出**, 它把上游真正给的东西原样呈现了。丢掉它换一句
    // "无错误信息", 等于我们自己制造了一次信息损失 —— 正是这个函数要治的病。
    //
    // 真正要守的性质只有两条: 不产出 undefined, 不丢信息。
    expect(reasonText({ message: 42 })).toBe('{"message":42}');
  });

  it("循环引用不炸", () => {
    const a: Record<string, unknown> = {};
    a.self = a;
    expect(() => reasonText(a)).not.toThrow();
  });
});
