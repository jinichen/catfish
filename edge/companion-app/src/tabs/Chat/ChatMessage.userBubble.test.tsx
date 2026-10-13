// @vitest-environment jsdom
// 10/13 鸿波截图: 切回老会话, 自己那条气泡里是一大段发给模型的附件材料。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
vi.mock("../../store/agent", () => ({ useAgentStore: () => ({}) }));
vi.mock("../../components/TTSButton", () => ({ default: () => null }));
import ChatMessage from "./ChatMessage";
import { formatFileAttachment } from "../../lib/chatWire";
import type { ChatMessage as Msg } from "../../types/chat";

afterEach(() => cleanup());

const NAME = "20260807总经理办公会纪要③2026年第二次企业资质增补预算申请.pdf";
const BLOCK = formatFileAttachment({
  kind: "file", mimeType: "application/pdf", name: NAME, sizeBytes: 1, fileKind: "pdf",
  keptPath: "/Users/chenhongbo/.catfish/uploads/1791879373-" + NAME, previewText: "会议纪要正文第一页……", meta: { pages: 4 },
} as never);

const reloaded: Msg = { id: "db-1", role: "user", content: "就是这个会议纪要" + BLOCK, ts: "2026-10-02T10:00:00Z", status: "done" };

describe("用户气泡只显示员工的话", () => {
  it("老会话读回来的附件材料不出现在气泡里, 附件变成文件名标签", () => {
    const { container } = render(<ChatMessage msg={reloaded} />);
    const text = container.textContent ?? "";
    expect(screen.getByText("就是这个会议纪要")).toBeTruthy();
    expect(screen.getByText(NAME)).toBeTruthy();
    for (const w of ["=== 附件", "完整文件", "preview", "execute_code", "/Users/", "会议纪要正文第一页"]) {
      expect(text).not.toContain(w);
    }
  });

  it("编辑老消息: 编辑框里只有员工的话, 重发时把附件材料接回去 (模型还要看)", () => {
    const onEdit = vi.fn();
    render(<ChatMessage msg={reloaded} onEditAndResend={onEdit} />);
    fireEvent.click(screen.getByTitle(/编辑/));
    const box = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(box.value).toBe("就是这个会议纪要");
    fireEvent.change(box, { target: { value: "就是这个会议纪要, 看第 2 页" } });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onEdit).toHaveBeenCalledWith("db-1", "就是这个会议纪要, 看第 2 页" + BLOCK);
  });

  it("刚发出的消息 (附件在 msg.attachments 里) 也显示文件名标签, 普通消息不受影响", () => {
    const fresh: Msg = {
      id: "u1", role: "user", content: "帮我看看", ts: "x", status: "done",
      attachments: [{ kind: "file", mimeType: "application/pdf", name: "报价单.pdf", sizeBytes: 1 } as never],
    };
    render(<ChatMessage msg={fresh} />);
    expect(screen.getByText("帮我看看")).toBeTruthy();
    expect(screen.getByText("报价单.pdf")).toBeTruthy();
    cleanup();
    render(<ChatMessage msg={{ id: "u2", role: "user", content: "普通一句话", ts: "x", status: "done" }} />);
    expect(screen.getByText("普通一句话")).toBeTruthy();
  });
});
