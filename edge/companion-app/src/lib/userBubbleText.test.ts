import { describe, expect, it } from "vitest";
import { formatFileAttachment, formatManyFiles } from "./chatWire";
import { splitUserBubbleText } from "./userBubbleText";

const pdf = {
  kind: "file" as const,
  mimeType: "application/pdf",
  name: "20260807总经理办公会纪要③2026年第二次企业资质增补预算申请.pdf",
  sizeBytes: 1000,
  fileKind: "pdf",
  keptPath: "/Users/x/.catfish/uploads/1791879373-20260807总经理办公会纪要.pdf",
  previewText: "第一页正文……",
  meta: { pages: 4 },
};

describe("splitUserBubbleText", () => {
  it("10/13 截图那条: 气泡只剩员工的话, 附件变文件名", () => {
    const content = "就是这个会议纪要" + formatFileAttachment(pdf as never);
    const r = splitUserBubbleText(content);
    expect(r.text).toBe("就是这个会议纪要");
    expect(r.fileNames).toEqual([pdf.name]);
    expect(r.hidden).toContain("[完整文件:");
    for (const w of ["=== 附件", "完整文件", "preview", "execute_code", "/Users/"]) {
      expect(r.text).not.toContain(w);
    }
  });

  it("多文件紧凑模式也切得开, 文件名去重", () => {
    const files = Array.from({ length: 7 }, (_, i) => ({ ...pdf, name: `f${i}.pdf` }));
    const r = splitUserBubbleText("帮我核一下" + formatManyFiles(files as never));
    expect(r.text).toBe("帮我核一下");
    expect(r.fileNames).toHaveLength(7);
    expect(r.hidden.startsWith("\n\n[共 7 个文件。")).toBe(true);
  });

  it("微信聊天记录附件", () => {
    const content = "看看这段聊天\n\n=== 微信聊天记录: 达远智能 (120 条 · 10-01 ~ 10-12) ===\n--- 聊天记录 ---\n…";
    const r = splitUserBubbleText(content);
    expect(r.text).toBe("看看这段聊天");
    expect(r.fileNames).toEqual(["达远智能"]);
  });

  it("没有附件 / 只发了附件 / 空内容", () => {
    expect(splitUserBubbleText("普通一句话")).toEqual({ text: "普通一句话", hidden: "", fileNames: [] });
    const onlyFile = splitUserBubbleText(formatFileAttachment(pdf as never));
    expect(onlyFile.text).toBe("");
    expect(onlyFile.fileNames).toEqual([pdf.name]);
    expect(splitUserBubbleText(undefined).text).toBe("");
  });

  it("员工自己正文里提到「附件」不会被误切", () => {
    const s = "附件: 我昨天发的那个报价单, 你再看看 === 附件 这几个字";
    expect(splitUserBubbleText(s).text).toBe(s);
  });
});
