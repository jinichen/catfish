// @vitest-environment jsdom
/** 11/12 文书风格页: 你指定的范文 + 写法特点 (总结要点了才把范文发给模型; 特点能自己改)。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  STYLE_ACCEPT: ".docx,.pdf,.txt,.md",
  styleState: vi.fn(), styleAdd: vi.fn(), styleRemove: vi.fn(), styleSetTraits: vi.fn(), styleSummarize: vi.fn(),
}));
vi.mock("../../lib/tauri_style_samples", () => api);
vi.mock("../../store/agent", () => ({ useAgentStore: (sel: (s: { name: string }) => unknown) => sel({ name: "小鲶" }) }));
const stage = vi.hoisted(() => ({ stageFiles: vi.fn() }));
vi.mock("../Collab/RoomsDelivery", () => stage);

import StyleFingerprintCard from "./StyleFingerprintCard";

const base = { samples: [], traits: [], traits_at: null, stale: false, max_samples: 5 };
const one = { ...base, samples: [{ id: "ab12cd34ef56", name: "三季度汇报.docx", chars: 2345, added_at: 1794400000 }], stale: true };

afterEach(cleanup);
beforeEach(() => {
  Object.values(api).forEach((f) => typeof f === "function" && (f as ReturnType<typeof vi.fn>).mockReset());
  stage.stageFiles.mockReset().mockResolvedValue([{ name: "周报.docx", stage_id: "s_1" }]);
  api.styleState.mockResolvedValue(base);
});

describe("StyleFingerprintCard", () => {
  it("没有范文: 只提示怎么加, 不出总结按钮", async () => {
    render(<StyleFingerprintCard />);
    await screen.findByText(/还没有范文/);
    expect(screen.queryByText(/总结写法特点/)).toBeNull();
    expect(document.body.textContent).not.toMatch(/高频词|jieba|平均句长/);
  });

  it("选文件 → 暂存 → 加成范文", async () => {
    api.styleAdd.mockResolvedValue(one);
    render(<StyleFingerprintCard />);
    await screen.findByText(/还没有范文/);
    fireEvent.change(screen.getByLabelText("选范文"), { target: { files: [new File(["x"], "周报.docx")] } });
    await waitFor(() => expect(api.styleAdd).toHaveBeenCalledWith("s_1", "周报.docx"));
    await screen.findByText("三季度汇报.docx");
    expect(screen.getByText(/2,345 字/)).toBeTruthy();
  });

  it("总结: 写明范文会发给模型, 点了才调; 出来的特点能改", async () => {
    api.styleState.mockResolvedValue(one);
    api.styleSummarize.mockResolvedValue({ ...one, stale: false, traits: ["开头一句话先说结论", "分点用一、二、三"] });
    api.styleSetTraits.mockResolvedValue({ ...one, stale: false, traits: ["开头先说结论"] });
    render(<StyleFingerprintCard />);
    await screen.findByText(/范文正文会发给模型/);
    expect(api.styleSummarize).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("让小鲶总结写法特点"));
    await screen.findByText("分点用一、二、三");
    fireEvent.click(screen.getByText("我来改"));
    fireEvent.change(screen.getByLabelText("写法特点"), { target: { value: "开头先说结论\n\n" } });
    fireEvent.click(screen.getByText("保存"));
    await waitFor(() => expect(api.styleSetTraits).toHaveBeenCalledWith(["开头先说结论"]));
    await screen.findByText("开头先说结论");
  });

  it("删除范文; 出错把原因显示出来", async () => {
    api.styleState.mockResolvedValue(one);
    api.styleRemove.mockRejectedValue(new Error("范文编号不对"));
    render(<StyleFingerprintCard />);
    fireEvent.click(await screen.findByText("删除"));
    await screen.findByText("范文编号不对");
    expect(api.styleRemove).toHaveBeenCalledWith("ab12cd34ef56");
  });
});
