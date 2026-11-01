// @vitest-environment jsdom
/** 11/02: 检索打分 —— 四种方式的命中率表; 没跑通的写原因; 默认只列有方式没找到的题 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();
vi.mock("@tauri-apps/api/core", () => ({ invoke: (...a: unknown[]) => invoke(...a) }));

import WikiRetrievalEval from "./WikiRetrievalEval";

afterEach(cleanup);

describe("WikiRetrievalEval", () => {
  it("打分后显示各方式命中率, 列出没找到的题", async () => {
    invoke.mockResolvedValue({
      topK: 5,
      engines: [
        { engine: "hybrid", label: "关键词 + 语义 + 图谱 (知识页默认)", available: true, note: null, found: 2, total: 2, recall: 1, mrr: 0.75 },
        { engine: "xiaonian", label: "小鲶聊天时用的", available: true, note: null, found: 1, total: 2, recall: 0.5, mrr: 0.5 },
        { engine: "semantic", label: "只用语义", available: false, note: "语义模型没就绪", found: 0, total: 2, recall: 0, mrr: 0 },
      ],
      queries: [
        { query: "预算谁批", ranks: { hybrid: 1, xiaonian: 1 }, tops: {} },
        { query: "立项会几点", ranks: { hybrid: 2, xiaonian: null }, tops: { xiaonian: ["周报模板"] } },
      ],
    });
    render(<WikiRetrievalEval />);
    fireEvent.change(screen.getByLabelText("评测题目"), { target: { value: '{"query":"a","gold":["b"]}' } });
    fireEvent.click(screen.getByText("开始打分"));
    await waitFor(() => expect(screen.getByText("100%")).toBeTruthy());
    expect(invoke).toHaveBeenCalledWith("wiki_retrieval_eval", { questions: '{"query":"a","gold":["b"]}', topK: 5 });
    expect(screen.getByText("50%")).toBeTruthy();
    expect(screen.getByText(/没跑: 语义模型没就绪/)).toBeTruthy();
    expect(screen.queryByText("预算谁批")).toBeNull(); // 都找到了, 默认不列
    expect(screen.getByText("立项会几点")).toBeTruthy();
    expect(screen.getByText(/排前面的是: 周报模板/)).toBeTruthy();
  });
});
