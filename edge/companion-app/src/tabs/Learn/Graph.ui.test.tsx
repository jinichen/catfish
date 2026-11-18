// @vitest-environment jsdom
/** 学习第 3 步 (11/18): 我学过的要点 —— 按材料分组 / 推理链 / 知识库条目 / 也来自 / 搜 / 导入资料 / 删一份点两下; 学习里「沉淀要点」。 */
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { GraphView } from "../../lib/tauri_learn";

const api = vi.hoisted(() => ({ view: vi.fn(), imp: vi.fn(), del: vi.fn(), stage: vi.fn() }));
vi.mock("../../lib/tauri_learn", async (orig) => ({
  ...(await orig<typeof import("../../lib/tauri_learn")>()),
  learnGraphView: api.view, learnGraphImport: api.imp, learnGraphDelete: api.del,
}));
vi.mock("../Collab/RoomsDelivery", () => ({ stageFiles: api.stage }));

import GraphPanel from "./GraphPanel";

const G: GraphView = {
  points: 3, chains: 1, running: null, last: null,
  sources: [
    { source: "learn:l1", label: "学习: 准备 CCRC 答辩", kind: "learn", points: [
      { id: "pt_1", name: "二级项目数", statement: "二级要近三年 3 个项目。", wiki: "wiki/c.md", wiki_title: "CCRC 应急处理二级",
        also: ["资料: 实施规则.pdf"], chains: [] },
      { id: "pt_3", name: "二级材料齐", statement: "申报要 3 个有过程记录的项目。", wiki: "", wiki_title: "", also: [],
        chains: ["二级项目数 + 项目要过程记录 ⇒ 二级材料齐"] }] },
    { source: "file:abc", label: "资料: 实施规则.pdf", kind: "file", points: [
      { id: "pt_2", name: "项目要过程记录", statement: "每个项目要有过程记录。", wiki: "", wiki_title: "", also: [], chains: [] }] },
  ],
};

beforeEach(() => { Object.values(api).forEach((f) => f.mockReset()); });
afterEach(cleanup);

async function mount() {
  await act(async () => { render(<GraphPanel />); });
}

describe("我学过的要点", () => {
  it("按材料分组, 显示推理、知识库条目、也来自; 能搜", async () => {
    api.view.mockResolvedValue(G);
    await mount();
    expect(screen.getByText(/3 条要点 · 1 条推理/)).toBeTruthy();
    expect(screen.getByText("⤷ 推理: 二级项目数 + 项目要过程记录 ⇒ 二级材料齐")).toBeTruthy();
    expect(screen.getByText(/知识库: CCRC 应急处理二级 · 也来自: 资料: 实施规则.pdf/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("找要点"), { target: { value: "过程记录" } });
    expect(screen.queryByText("二级项目数")).toBeNull();
    expect(screen.getByText("项目要过程记录")).toBeTruthy();
  });

  it("导入资料: 先分片暂存再交给后台; 读的时候显示进度, 读完显示结果", async () => {
    api.view.mockResolvedValue({ ...G, running: null });
    api.stage.mockResolvedValue([{ name: "规则.pdf", stage_id: "st_1" }]);
    api.imp.mockResolvedValue({ ...G, running: "规则.pdf", chars: 9000, parts: 2 });
    await mount();
    const f = new File(["x"], "规则.pdf", { type: "application/pdf" });
    await act(async () => { fireEvent.change(screen.getByLabelText("选资料"), { target: { files: [f] } }); });
    expect(api.imp).toHaveBeenCalledWith("st_1", "规则.pdf");
    expect(screen.getByText(/小鲶在读「规则.pdf」/)).toBeTruthy();
  });

  it("读完的结果 / 出错都告诉员工", async () => {
    api.view.mockResolvedValue({ ...G, last: { name: "规则.pdf", added: 5, linked: 2, chains: 3 } });
    await mount();
    expect(screen.getByText(/「规则.pdf」: 新增 5 条要点, 2 条跟已有的是同一条 \(合并了\), 3 条推理/)).toBeTruthy();
    cleanup();
    api.view.mockResolvedValue({ ...G, last: { name: "扫描件.pdf", error: "读不出文字: 是图片" } });
    await mount();
    expect(screen.getByText(/「扫描件.pdf」没读成: 读不出文字/)).toBeTruthy();
  });

  it("删一份资料点两下; 学习沉淀的没有删除按钮 (跟着那次学习走)", async () => {
    api.view.mockResolvedValue(G);
    api.del.mockResolvedValue({ ...G, sources: [G.sources[0]] });
    await mount();
    expect(screen.getAllByText("删这份")).toHaveLength(1);
    await act(async () => { fireEvent.click(screen.getByText("删这份")); });
    expect(api.del).not.toHaveBeenCalled();
    await act(async () => { fireEvent.click(screen.getByText("再点一次删除")); });
    expect(api.del).toHaveBeenCalledWith("file:abc");
  });

  it("什么都没有时说明从哪来", async () => {
    api.view.mockResolvedValue({ points: 0, chains: 0, running: null, last: null, sources: [] });
    await mount();
    expect(screen.getByText(/学完一次、导入一份资料, 或者在会议纪要上点「记住这几条」/)).toBeTruthy();
  });
});
