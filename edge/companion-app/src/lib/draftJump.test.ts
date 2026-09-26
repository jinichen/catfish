import { describe, expect, it, vi } from "vitest";

vi.mock("./tauri", () => ({ emailListFetch: vi.fn() }));
vi.mock("../store/ui", () => {
  const state = { activeTab: "chat", openEmailDraft: vi.fn() };
  return { useUIStore: { getState: () => state } };
});

import { emailListFetch } from "./tauri";
import { useUIStore } from "../store/ui";
import { jumpToFreshDraft, noteToolProgress, pickFreshDraft } from "./draftJump";

const draft = (id: string, date: string) => ({ id, date }) as never;

describe("draftJump", () => {
  it("picks the newest draft saved after the tool finished, ignores old ones", () => {
    const since = Date.parse("2026-09-27T08:00:00Z");
    const list = [draft("old", "2026-09-26T10:00:00Z"), draft("new", "2026-09-27T07:59:30Z")];
    expect(pickFreshDraft(list, since)?.id).toBe("new");
    expect(pickFreshDraft([draft("old", "2026-09-26T10:00:00Z")], since)).toBeNull();
  });

  it("only jumps after a create_draft tool completed and a fresh draft really exists", async () => {
    const open = useUIStore.getState().openEmailDraft as ReturnType<typeof vi.fn>;
    await jumpToFreshDraft();
    expect(emailListFetch).not.toHaveBeenCalled();

    noteToolProgress("mcp__catfish_tools__catfish_email_search", "completed");
    noteToolProgress("mcp__catfish_tools__catfish_email_create_draft", "running");
    await jumpToFreshDraft();
    expect(emailListFetch).not.toHaveBeenCalled();

    noteToolProgress("mcp__catfish_tools__catfish_email_create_draft", "completed");
    (emailListFetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      JSON.stringify([draft("d1", new Date().toISOString())]),
    );
    await jumpToFreshDraft();
    expect(open).toHaveBeenCalledWith("d1");
  });
});
