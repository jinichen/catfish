// @vitest-environment jsdom
/** 对话里: 「打开总览卡」; 教会的技能接进专家后自动重新生效 (只一次)。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ expertApply: vi.fn() }));
vi.mock("../../lib/tauri_experts", () => api);
vi.mock("../../lib/expertChat", () => ({ refreshExperts: vi.fn().mockResolvedValue(undefined) }));

import { installMemoryLocalStorage } from "../../lib/__fixtures__/memoryLocalStorage";
import { useUIStore } from "../../store/ui";
import { BuildOverviewButton, TaughtReapply } from "./ChatExpertBuild";

installMemoryLocalStorage();
afterEach(cleanup);
beforeEach(() => { localStorage.clear(); api.expertApply.mockReset().mockResolvedValue({ ok: true, profile_id: "p" }); });

describe("ChatExpertBuild", () => {
  it("打开总览卡: 切到工作台专家页", () => {
    render(<BuildOverviewButton resultText={JSON.stringify({ ok: true, build_id: "b12345678" })} />);
    fireEvent.click(screen.getByText("打开总览卡"));
    expect(useUIStore.getState()).toMatchObject({ activeTab: "dashboard", pendingExpertBuild: "b12345678" });
    expect(localStorage.getItem("section_tab_skills")).toBe("experts");
  });

  it("没有 build_id (别的 action) 不显示", () => {
    const { container } = render(<BuildOverviewButton resultText={JSON.stringify({ ok: true, workflows: [] })} />);
    expect(container.innerHTML).toBe("");
  });

  it("教会了: 自动重新生效一次; 再渲染 (翻历史) 不重复", async () => {
    const text = JSON.stringify({ ok: true, reapply_experts: ["e1"], attached: ["资质专员 · 下载证书"] });
    render(<TaughtReapply callId="call_1" resultText={text} />);
    await screen.findByText(/专家已经会了/);
    expect(api.expertApply).toHaveBeenCalledWith("e1");
    cleanup();
    render(<TaughtReapply callId="call_1" resultText={text} />);
    expect(screen.getByText(/专家已经会了/)).toBeTruthy();
    expect(api.expertApply).toHaveBeenCalledTimes(1);
  });

  it("技能存好了但没接进专家: 说出来", () => {
    render(<TaughtReapply callId="call_3" resultText={JSON.stringify({ ok: true, attach_error: "技能存好了, 但没接进专家: 技能 personal/x 不存在" })} />);
    expect(screen.getByText(/没接进专家/)).toBeTruthy();
    expect(api.expertApply).not.toHaveBeenCalled();
  });

  it("重新生效失败: 说清楚, 能重试", async () => {
    api.expertApply.mockRejectedValueOnce(new Error("网关忙"));
    render(<TaughtReapply callId="call_2" resultText={JSON.stringify({ reapply_experts: ["e1"], attached: ["x · y"] })} />);
    await screen.findByText(/没重新生效: 网关忙/);
    fireEvent.click(screen.getByText("重试"));
    await waitFor(() => expect(screen.getByText(/专家已经会了/)).toBeTruthy());
  });
});
