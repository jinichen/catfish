// @vitest-environment jsdom
/** 11/21 误删保护: 开关 / 份数、现在做一份、挑一份只还原勾上的几项 (外发记录不给勾)、要二次确认。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a) }));

import SnapshotsCard from "./SnapshotsCard";

const entry = (name: string, label: string) => ({ root: "catfish", name, class: "required", label, files: 3, bytes: 4096 });
const snap = {
  name: "鲶鱼快照-20261121-093000.catfishsnap",
  createdAt: "2026-11-21T09:30:00+08:00",
  bytes: 9_000_000,
  files: 800,
  companionVersion: "1.2.238",
  entries: [entry("wiki", "个人知识库"), entry("employee_journal.md", "工作日志"), entry("outbound_log.db", "数据外发记录")],
};
const status = (enabled = true, keep = 3) => ({
  settings: { enabled, keep, lastError: null, lastTryAt: null },
  snapshots: [snap],
  dir: "/Users/x/.catfish/snapshots",
  busy: false,
});

beforeEach(() => {
  invoke.mockReset().mockImplementation((cmd: string, a?: { enabled: boolean; keep: number }) => {
    if (cmd === "snapshots_status") return Promise.resolve(status());
    if (cmd === "snapshots_set") return Promise.resolve(status(a?.enabled, a?.keep));
    if (cmd === "snapshots_restore") return Promise.resolve({ files: 3, bytes: 4096, pathsRewritten: 0, keptExisting: 0, backupDir: "/b", redo: [], warnings: [] });
    if (cmd === "snapshots_make_now") return Promise.resolve({ ...snap, createdAt: "2026-11-21T11:00:00+08:00" });
    return Promise.resolve(undefined);
  });
});
afterEach(cleanup);

describe("SnapshotsCard", () => {
  it("挑一份, 只还原勾上的; 外发记录不给勾; 要确认才还原", async () => {
    render(<SnapshotsCard />);
    fireEvent.click(await screen.findByText("从这份还原…"));
    expect(screen.getByText(/个人知识库/)).toBeTruthy();
    expect(screen.queryByText(/数据外发记录/)).toBeNull();
    fireEvent.click(screen.getByText(/个人知识库/));
    fireEvent.click(screen.getByText("还原这 1 项…"));
    expect(invoke).not.toHaveBeenCalledWith("snapshots_restore", expect.anything());
    fireEvent.click(screen.getByText("确定还原"));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("snapshots_restore", { name: snap.name, only: ["catfish/wiki"] }));
    expect(await screen.findByText(/还原好了/)).toBeTruthy();
  });

  it("关自动 / 改份数 / 现在做一份", async () => {
    render(<SnapshotsCard />);
    fireEvent.click(await screen.findByLabelText(/每天自动做一份/));
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("snapshots_set", { enabled: false, keep: 3 }));
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "7" } });
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("snapshots_set", { enabled: false, keep: 7 }));
    fireEvent.click(screen.getByText("现在做一份"));
    expect(await screen.findByText(/做好了: 2026-11-21 11:00/)).toBeTruthy();
  });
});
