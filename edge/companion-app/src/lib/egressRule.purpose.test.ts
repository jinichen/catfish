// 10/28 鸿波: 「/api/egress/anchor、长期记忆、长期记忆整理各自的含义; 微信 novac2c.cdn.weixin.qq.com 又是什么」
import { describe, expect, it } from "vitest";

import { displayPurpose, egressPurpose, mergeSummaryRows } from "./egressRule";

describe("用途显示名", () => {
  it("新记录: 报指纹有中文名", () => {
    expect(egressPurpose("http://127.0.0.1:8999/api/egress/anchor")).toBe("报指纹 (防篡改)");
  });

  it("老记录显示时换成现在的叫法", () => {
    expect(displayPurpose("/api/egress/anchor", "127.0.0.1")).toBe("报指纹 (防篡改)");
    expect(displayPurpose("微信", "novac2c.cdn.weixin.qq.com")).toBe("微信 · 发图片 / 文件");
    expect(displayPurpose("微信", "ilinkai.weixin.qq.com")).toBe("微信 · 收发消息");
    expect(displayPurpose("长期记忆", "127.0.0.1", "http://x/v1/chat/completions?catfish_source=plugin:memory-classify")).toBe("记忆归类");
    expect(displayPurpose("长期记忆", "127.0.0.1")).toBe("长期记忆"); // 汇总里没 URL, 分不出来就不猜
    expect(displayPurpose("对话", "127.0.0.1")).toBe("对话");
  });

  it("汇总: 老名字和新名字同去向的并成一行", () => {
    const row = (purpose: string, count: number, lastTs: string) =>
      ({ destKind: "third_party", purpose, destHost: "ilinkai.weixin.qq.com", count, bytesUp: count * 10, bytesDown: 1, lastTs });
    const merged = mergeSummaryRows([row("微信", 248, "2026-10-28T05:35:52Z"), row("微信 · 收发消息", 3, "2026-10-28T08:00:00Z")]);
    expect(merged).toEqual([{ destKind: "third_party", purpose: "微信 · 收发消息", destHost: "ilinkai.weixin.qq.com",
      count: 251, bytesUp: 2510, bytesDown: 2, lastTs: "2026-10-28T08:00:00Z" }]);
  });
});
