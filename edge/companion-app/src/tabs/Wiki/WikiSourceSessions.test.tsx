// @vitest-environment jsdom
/** 11/02: 知识卡来源 → 能点开的对话; 精确的直接列, 只到日期的标「当天」 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a) }));

import WikiSourceSessions from "./WikiSourceSessions";
import { useUIStore } from "../../store/ui";

afterEach(cleanup);

describe("WikiSourceSessions", () => {
  it("列出来源对话, 点一下打开那次对话", async () => {
    invoke.mockResolvedValue([
      { source: "session:s1", sessionId: "s1", title: "季度预算", startedAt: "2026-11-01T09:10:00+08:00", exact: true },
      { source: "journal:2026-11-01", sessionId: "s2", title: "", startedAt: "2026-11-01T15:20:00+08:00", exact: false },
    ]);
    render(<WikiSourceSessions sources={["journal:2026-11-01", "session:s1", "raw/sources/x"]} />);
    await waitFor(() => expect(screen.getByText("季度预算")).toBeTruthy());
    expect(invoke).toHaveBeenCalledWith("wiki_source_sessions", { sources: ["journal:2026-11-01", "session:s1"] });
    expect(screen.getByText(/当天的对话/)).toBeTruthy();
    fireEvent.click(screen.getByText("季度预算"));
    expect(useUIStore.getState().pendingSessionFocus).toBe("s1");
    expect(useUIStore.getState().activeTab).toBe("chat");
  });

  it("没有对话类来源就不显示", async () => {
    invoke.mockClear();
    const { container } = render(<WikiSourceSessions sources={["raw/sources/x", "manual"]} />);
    await new Promise((r) => setTimeout(r, 0));
    expect(container.innerHTML).toBe("");
    expect(invoke).not.toHaveBeenCalled();
  });
});
