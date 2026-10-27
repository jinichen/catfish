// @vitest-environment jsdom
// 10/27 鸿波截图: 一次拖 30 张发票, 卡片铺满整个对话区。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
vi.mock("../../../store/agent", () => ({ useAgentStore: () => ({}) }));

import AttachmentTray, { COMPACT_AT, kindBreakdown, totalSizeLabel } from "./AttachmentTray";
import ChatMessage from "../ChatMessage";
import type { Attachment, ChatMessage as Msg } from "../../../types/chat";

afterEach(() => cleanup());

const pdf = (i: number): Attachment =>
  ({ kind: "file", mimeType: "application/pdf", name: `dzfp_${i}.pdf`, sizeBytes: 57 * 1024, fileKind: "pdf", meta: { page_count: 1 } }) as never;
const png: Attachment = { kind: "image", mimeType: "image/png", name: "a.png", sizeBytes: 1024, base64: "AA==" } as never;

describe("附件多了收成一行", () => {
  it("汇总: 类型按数量排, 总大小", () => {
    const list = [pdf(1), pdf(2), png];
    expect(kindBreakdown(list)).toBe("2 份PDF · 1 张图片");
    expect(totalSizeLabel([pdf(1), pdf(2)])).toBe("114 KB");
  });

  it(`≤${COMPACT_AT} 个照旧铺开`, () => {
    render(<AttachmentTray attachments={[pdf(1), pdf(2)]} onRemove={() => {}} onClear={() => {}} />);
    expect(screen.getByText("dzfp_1.pdf")).toBeTruthy();
    expect(screen.queryByText(/展开/)).toBeNull();
  });

  it("30 个只显示汇总, 展开才列出, 能全部移除", () => {
    const list = Array.from({ length: 30 }, (_, i) => pdf(i));
    const onClear = vi.fn();
    render(<AttachmentTray attachments={list} onRemove={() => {}} onClear={onClear} />);
    expect(screen.getByText("30 个文件")).toBeTruthy();
    expect(screen.queryByText("dzfp_0.pdf")).toBeNull();
    fireEvent.click(screen.getByText(/展开/));
    expect(screen.getByText("dzfp_29.pdf")).toBeTruthy();
    fireEvent.click(screen.getByText("全部移除"));
    expect(onClear).toHaveBeenCalled();
  });

  it("发出去的气泡: 只列前 5 个文件, 其余「+N 个文件」点开", () => {
    const msg: Msg = { id: "u1", role: "user", content: "核对这些发票", ts: "2026-10-27T10:00:00Z", status: "done",
      attachments: Array.from({ length: 30 }, (_, i) => pdf(i)) };
    render(<ChatMessage msg={msg} />);
    expect(screen.getByText("dzfp_4.pdf")).toBeTruthy();
    expect(screen.queryByText("dzfp_5.pdf")).toBeNull();
    fireEvent.click(screen.getByText("+25 个文件"));
    expect(screen.getByText("dzfp_29.pdf")).toBeTruthy();
  });
});
