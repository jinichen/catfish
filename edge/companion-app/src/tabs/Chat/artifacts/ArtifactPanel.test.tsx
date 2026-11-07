// @vitest-environment jsdom
/** 11/07 对话右侧成果区: 各类文件怎么显示、网页隔离、切换 / 关闭、文件被删的提示 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({
  invoke: (...a: unknown[]) => invoke(...a),
  convertFileSrc: (p: string) => `asset://localhost/${encodeURIComponent(p)}`,
}));

import ArtifactPanel from "./ArtifactPanel";
import { freshArtifact } from "./useArtifactAutoOpen";
import { useArtifactStore } from "../../../store/artifacts";

const b64 = (s: string) => btoa(String.fromCharCode(...new TextEncoder().encode(s)));
const stat = (path: string, kind: string, mtime = 1000) => ({ path, name: path.split("/").pop(), ext: path.split(".").pop(), kind, size: 2048, mtime_ms: mtime });

beforeEach(() => {
  invoke.mockReset();
  useArtifactStore.setState({ path: null, view: null, reloadKey: 0 });
});
afterEach(cleanup);

describe("ArtifactPanel", () => {
  it("网页: iframe 走 asset 地址, 只开 allow-scripts (碰不到 Companion)", async () => {
    invoke.mockImplementation(async (cmd: string, a: { path: string }) => (cmd === "artifact_stat" ? stat(a.path, "html") : null));
    useArtifactStore.setState({ path: "/Users/x/out/报告.html", view: "file" });
    render(<ArtifactPanel paths={["/Users/x/out/报告.html"]} />);
    const frame = await screen.findByTitle("报告.html");
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
    // 保留目录层级 (页面里的相对地址才找得到旁边的图片)
    expect(frame.getAttribute("src")).toBe(`asset://localhost/%2FUsers/x/out/${encodeURIComponent("报告.html")}?v=1000`);
    expect(screen.getByText(/网页 · 2.0 KB · 更新于/)).toBeTruthy();
  });

  it("Markdown 读文本后渲染; 顶上能切到这段对话的其他成果", async () => {
    invoke.mockImplementation(async (cmd: string, a: { path: string }) => {
      if (cmd === "artifact_stat") return stat(a.path, a.path.endsWith(".md") ? "markdown" : "table");
      if (cmd === "artifact_read_text") return { b64: b64(a.path.endsWith(".md") ? "# 周报\n\n正文" : "名称,金额\n甲,1"), truncated: false, size: 10 };
      return null;
    });
    useArtifactStore.setState({ path: "/Users/x/a.md", view: "file" });
    render(<ArtifactPanel paths={["/Users/x/a.md", "/Users/x/b.csv"]} />);
    expect(await screen.findByRole("heading", { name: "周报" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText(/这段对话的成果，共 2 个/), { target: { value: "/Users/x/b.csv" } });
    expect(await screen.findByRole("columnheader", { name: "金额" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: "甲" })).toBeTruthy();
  });

  it("Excel 多张表切换", async () => {
    invoke.mockImplementation(async (cmd: string, a: { path: string }) => {
      if (cmd === "artifact_stat") return stat(a.path, "office");
      if (cmd === "artifact_office_preview")
        return { kind: "sheet", truncated: false, sheets: [
          { name: "1月", rows: [["日期", "金额"], ["1/5", "12"]], total_rows: 2, total_cols: 2 },
          { name: "汇总", rows: [["合计", "99"]], total_rows: 1, total_cols: 2 },
        ] };
      return null;
    });
    useArtifactStore.setState({ path: "/Users/x/t.xlsx", view: "file" });
    render(<ArtifactPanel paths={[]} />);
    expect(await screen.findByRole("cell", { name: "12" })).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "汇总" }));
    expect(screen.getByRole("columnheader", { name: "99" })).toBeTruthy();
  });

  it("文件不在了: 显示原因; 关闭按钮关掉右栏", async () => {
    invoke.mockRejectedValue("文件不存在: /Users/x/gone.pdf");
    useArtifactStore.setState({ path: "/Users/x/gone.pdf", view: "file" });
    render(<ArtifactPanel paths={[]} />);
    expect(await screen.findByText("文件不存在: /Users/x/gone.pdf")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("关闭右侧栏"));
    expect(useArtifactStore.getState().view).toBeNull();
  });

  it("文件被改了 (修改时间变) 自动重读", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let mtime = 1000;
      let body = "第一版";
      invoke.mockImplementation(async (cmd: string, a: { path: string }) => {
        if (cmd === "artifact_stat") return stat(a.path, "text", mtime);
        if (cmd === "artifact_read_text") return { b64: b64(body), truncated: false, size: 9 };
        return null;
      });
      useArtifactStore.setState({ path: "/Users/x/n.txt", view: "file" });
      render(<ArtifactPanel paths={[]} />);
      expect(await screen.findByText("第一版")).toBeTruthy();
      mtime = 2000;
      body = "第二版";
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2100);
      });
      await waitFor(() => expect(screen.getByText("第二版")).toBeTruthy());
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("右侧栏两页", () => {
  it("切到浏览器页: 小鲶的浏览器没开时说清楚, 给「打开」按钮; 切页算用户操作", async () => {
    invoke.mockImplementation(async (cmd: string) => {
      if (cmd === "browser_view_endpoint") throw "小鲶的浏览器还没打开";
      return null;
    });
    useArtifactStore.setState({ view: "file", path: null, userActedAt: 0 });
    render(<ArtifactPanel paths={[]} />);
    expect(screen.getByText(/这段对话还没有生成文件/)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "浏览器" }));
    expect(await screen.findByRole("button", { name: "打开小鲶的浏览器" })).toBeTruthy();
    expect(useArtifactStore.getState().userActedAt).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /我来操作/ })).toHaveProperty("disabled", true);
  });
});

describe("freshArtifact (一轮结束自动打开)", () => {
  it("挑这一轮写过的 (修改时间在这一轮开始之后), 跳过老文件和不在的", async () => {
    invoke.mockImplementation(async (_cmd: string, a: { path: string }) => {
      if (a.path.includes("gone")) throw "文件不存在";
      return stat(a.path, "html", a.path.includes("old") ? 1_000 : 50_000);
    });
    expect(await freshArtifact(["/Users/x/gone.html", "/Users/x/old.html", "/Users/x/new.html"], 40_000)).toBe("/Users/x/new.html");
    expect(await freshArtifact(["/Users/x/old.html"], 40_000)).toBeNull();
  });
});
