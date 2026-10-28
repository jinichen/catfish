// @vitest-environment jsdom
// 10/28 鸿波: 「本会话历史附件」超过 12 个时「+N 更多」点了没反应, 后面的文件在界面上打不开。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/tauri", () => ({ openFile: vi.fn(), revealInFinder: vi.fn() }));

import { useChatStore, type SessionAttachment } from "../../store/chat";
import SessionAttachmentsBar from "./SessionAttachmentsBar";

afterEach(() => {
  cleanup();
  useChatStore.setState({ sessionAttachments: [] });
});

const att = (i: number): SessionAttachment => ({
  id: `a${i}`, userId: "u", sessionId: "s", messageId: "m", kind: "file", fileKind: "xlsx",
  name: `人员表-${i}.xlsx`, mimeType: null, sizeBytes: 1024, keptPath: `/tmp/人员表-${i}.xlsx`,
  parsedTextPath: null, meta: null, createdAt: 0,
});

describe("SessionAttachmentsBar", () => {
  it("超过 12 个: 「+N 更多」能点开全部, 再点收起", () => {
    useChatStore.setState({ sessionAttachments: Array.from({ length: 14 }, (_, i) => att(i + 1)) });
    render(<SessionAttachmentsBar />);
    expect(screen.queryByText("人员表-13.xlsx")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "+2 更多" }));
    expect(screen.getByText("人员表-13.xlsx")).toBeTruthy();
    expect(screen.getByText("人员表-14.xlsx")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "收起" }));
    expect(screen.queryByText("人员表-14.xlsx")).toBeNull();
  });

  it("不超过 12 个: 没有「更多」按钮", () => {
    useChatStore.setState({ sessionAttachments: Array.from({ length: 3 }, (_, i) => att(i + 1)) });
    render(<SessionAttachmentsBar />);
    expect(screen.queryByText(/更多/)).toBeNull();
  });
});
