import { describe, expect, it } from "vitest";

import { shrinkTarget } from "../tabs/Chat/components/attachmentHelpers";
import type { ChatMessage } from "../types/chat";
import { COMPACT_FROM, MANY_FILES_PREVIEW_BUDGET, toWire } from "./chatWire";

function invoice(i: number) {
  return {
    kind: "file" as const,
    name: `发票-${i}.pdf`,
    fileKind: "pdf",
    previewText: `发票号码 ${String(i).padStart(8, "0")} 金额 ${i * 100}.00 ` + "明细".repeat(3000),
    meta: { page_count: 1 },
    keptPath: `/Users/x/.catfish/uploads/发票-${i}.pdf`,
  };
}

function userWith(n: number): ChatMessage {
  return {
    role: "user",
    content: "把这些发票汇总成表",
    attachments: Array.from({ length: n }, (_, i) => invoice(i + 1)),
  } as unknown as ChatMessage;
}

describe("toWire 很多文档附件 (10/2 发票场景)", () => {
  it("6 个以内照旧: 每个完整预览 + 代码提示", () => {
    const text = toWire([userWith(COMPACT_FROM)])[0].content as string;
    expect(text.match(/=== 附件:/g)).toHaveLength(COMPACT_FROM);
    expect(text.match(/import pypdfium2/g)).toHaveLength(COMPACT_FROM);
  });

  it("30 张发票: 每张都在, 路径都在, 总长有上限, 提示只说一次", () => {
    const text = toWire([userWith(30)])[0].content as string;
    expect(text.match(/=== 附件:/g)).toHaveLength(30);
    for (let i = 1; i <= 30; i++) {
      expect(text).toContain(`[完整文件: /Users/x/.catfish/uploads/发票-${i}.pdf]`);
      expect(text).toContain(`发票号码 ${String(i).padStart(8, "0")}`); // 预览开头 (号码 / 金额) 留着
    }
    expect(text).not.toContain("import pypdfium2");
    expect(text.match(/共 30 个文件/g)).toHaveLength(1);
    // 原来 30 × (5000+ 字预览 + 代码提示) ≈ 20 万字; 现在压在预算附近
    expect(text.length).toBeLessThan(MANY_FILES_PREVIEW_BUDGET + 30 * 200);
  });
});

describe("shrinkTarget", () => {
  it("长边缩到 2000, 等比", () => {
    expect(shrinkTarget(4032, 3024)).toEqual({ w: 2000, h: 1500 });
    expect(shrinkTarget(3024, 4032)).toEqual({ w: 1500, h: 2000 });
  });
  it("本来就小的不放大", () => {
    expect(shrinkTarget(1200, 800)).toEqual({ w: 1200, h: 800 });
  });
});
