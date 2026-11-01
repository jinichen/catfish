// @vitest-environment jsdom
/** 11/02: 早安「今天要复习的题」—— 有到期才显示, 点开始复习交给小鲶 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a) }));

import PracticeStrip, { PRACTICE_PROMPT } from "./PracticeStrip";
import { useUIStore } from "../../store/ui";

afterEach(cleanup);

describe("PracticeStrip", () => {
  it("有到期的: 显示数量, 点开始复习交给对话", async () => {
    invoke.mockResolvedValue({ total: 20, wrong: 6, dueToday: 4, reviewedToday: 1 });
    render(<PracticeStrip />);
    await waitFor(() => expect(screen.getByRole("status").textContent).toContain("今天有 4 道错题要复习"));
    fireEvent.click(screen.getByText("开始复习"));
    const s = useUIStore.getState();
    expect(s.activeTab).toBe("chat");
    expect(s.pendingEmailChat?.prompt).toBe(PRACTICE_PROMPT);
  });

  it("没有到期的不显示", async () => {
    invoke.mockResolvedValue({ total: 20, wrong: 6, dueToday: 0, reviewedToday: 0 });
    const { container } = render(<PracticeStrip />);
    await new Promise((r) => setTimeout(r, 0));
    expect(container.innerHTML).toBe("");
  });
});
