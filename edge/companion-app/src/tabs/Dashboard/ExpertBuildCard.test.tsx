// @vitest-environment jsdom
/** 一句话搭专家的总览卡 (11/08): 起草 → 看 → 勾 → 全部保存并生效 (失败整张撤回) → 定时 / 现在教。 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  expertBuildList: vi.fn(), expertBuildPlan: vi.fn(), expertBuildUpdate: vi.fn(), expertBuildApply: vi.fn(),
  expertBuildRollback: vi.fn(), expertBuildDiscard: vi.fn(), expertBuildTeach: vi.fn(), expertBuildGet: vi.fn(),
  expertBuildRevise: vi.fn(),
  expertApply: vi.fn(), expertRemove: vi.fn(),
}));
const wf = vi.hoisted(() => ({ workflowScheduleSet: vi.fn() }));
vi.mock("../../lib/tauri_experts", () => api);
vi.mock("../../lib/tauri_workflows", () => wf);
vi.mock("../../lib/expertChat", () => ({ refreshExperts: vi.fn().mockResolvedValue(undefined) }));
vi.mock("./WorkflowsCard", () => ({ Detail: () => <div>workflow-detail</div> }));

import type { ExpertBuild } from "../../lib/tauri_experts";
import { useUIStore } from "../../store/ui";
import ExpertBuildCard, { applyBlocker, jobHow } from "./ExpertBuildCard";

afterEach(cleanup);

const build = (over: Partial<ExpertBuild> = {}): ExpertBuild => ({
  build_id: "b12345678", sentence: "帮我搭个资质专员", status: "draft", created_at: "",
  experts: [{
    key: 1, include: true, name: "资质专员", description: "资质邮件和证书", persona: "我是资质专员", quick_prompts: [],
    tool_groups: ["knowledge"], decline: "", jobs: [
      { when: "整理资质邮件", title: "整理资质邮件", kind: "new", include: true, problems: [], draft: "123456-e1-j1",
        schedule: { cron: "0 9 * * 1", text: "每周一 9:00" } },
      { when: "证书快到期了吗", title: "证书到期提醒", kind: "reuse", include: true, problems: [], use: "workflow:personal/cert", use_title: "证书到期提醒" },
      { when: "资质政策", title: "政策问答", kind: "knowledge", include: true, problems: [] },
      { when: "去 EIS 下载证书", title: "下载证书", kind: "teach", include: true, problems: [], what: "在 EIS 里下载证书", teach_name: "t123456-e1-j4" },
    ]}],
  ...over,
});

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  wf.workflowScheduleSet.mockReset().mockResolvedValue(undefined);
  api.expertBuildList.mockResolvedValue({ builds: [build()] });
  api.expertRemove.mockResolvedValue(undefined);
});

describe("ExpertBuildCard", () => {
  it("每件活说清怎么做; 定时、还不会的都标出来", async () => {
    render(<ExpertBuildCard onApplied={vi.fn()} />);
    expect(await screen.findByText("整理资质邮件", { selector: "strong" })).toBeTruthy();
    expect(screen.getAllByText((_t, el) => el?.tagName === "SPAN" && el.textContent === " · 新搭工作流")).toHaveLength(1);
    expect(screen.getByText(/用你已有的「证书到期提醒」/)).toBeTruthy();
    expect(screen.getByText(/⏰ 每周一 9:00/)).toBeTruthy();
    expect(screen.getByText(/还不会: 在 EIS 里下载证书/)).toBeTruthy();
    expect(screen.getByText("现在教")).toBeTruthy();
  });

  it("起草: 一句话交给小鲶, 卡片出来", async () => {
    api.expertBuildList.mockResolvedValue({ builds: [] });
    api.expertBuildPlan.mockResolvedValue({ build: build({ build_id: "b99999999" }) });
    render(<ExpertBuildCard onApplied={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("一句话说要搭什么专家"), { target: { value: "帮我搭个资质专员: 每周一整理资质邮件" } });
    fireEvent.click(screen.getByText("起草"));
    await screen.findByText("全部保存并生效");
    expect(api.expertBuildPlan).toHaveBeenCalledWith("帮我搭个资质专员: 每周一整理资质邮件");
  });

  it("全部保存并生效: 落地 → 建分身 → 挂定时", async () => {
    const onApplied = vi.fn();
    api.expertBuildApply.mockResolvedValue({ experts: [{ id: "e1", name: "资质专员" }],
      schedules: [{ skill_path: "personal/123456-e1-j1", title: "整理资质邮件", cron: "0 9 * * 1" }], build: build({ status: "saved" }) });
    api.expertApply.mockResolvedValue({ ok: true, profile_id: "expert-e1" });
    render(<ExpertBuildCard onApplied={onApplied} />);
    fireEvent.click(await screen.findByText("全部保存并生效"));
    await screen.findByText(/搭好了: 资质专员/);
    expect(api.expertApply).toHaveBeenCalledWith("e1");
    expect(wf.workflowScheduleSet).toHaveBeenCalledWith("personal/123456-e1-j1", "整理资质邮件", "0 9 * * 1");
    expect(onApplied).toHaveBeenCalled();
    expect(screen.getByText("✓ 已生效")).toBeTruthy();
  });

  it("建分身失败: 已建的删掉, 整张撤回", async () => {
    api.expertBuildList.mockResolvedValue({ builds: [build({ experts: [build().experts[0], { ...build().experts[0], key: 2, name: "合同专员" }] })] });
    api.expertBuildApply.mockResolvedValue({ experts: [{ id: "e1", name: "资质专员" }, { id: "e2", name: "合同专员" }], schedules: [], build: build({ status: "saved" }) });
    api.expertApply.mockResolvedValueOnce({ ok: true, profile_id: "expert-e1" }).mockRejectedValueOnce(new Error("hermes 没起来"));
    api.expertBuildRollback.mockResolvedValue({ build: build() });
    render(<ExpertBuildCard onApplied={vi.fn()} />);
    fireEvent.click(await screen.findByText("全部保存并生效"));
    await screen.findByText(/已经全部撤回.*hermes 没起来/);
    expect(api.expertRemove).toHaveBeenCalledWith("e1");
    expect(api.expertBuildRollback).toHaveBeenCalledWith("b12345678");
    expect(wf.workflowScheduleSet).not.toHaveBeenCalled();
  });

  it("定时没挂上: 专家照样好了, 列出来能重试", async () => {
    api.expertBuildApply.mockResolvedValue({ experts: [{ id: "e1", name: "资质专员" }],
      schedules: [{ skill_path: "personal/x", title: "整理资质邮件", cron: "0 9 * * 1" }], build: build({ status: "saved" }) });
    api.expertApply.mockResolvedValue({ ok: true, profile_id: "expert-e1" });
    wf.workflowScheduleSet.mockRejectedValueOnce(new Error("cron 不可用")).mockResolvedValueOnce(undefined);
    render(<ExpertBuildCard onApplied={vi.fn()} />);
    fireEvent.click(await screen.findByText("全部保存并生效"));
    await screen.findByText(/定时没挂上: 整理资质邮件/);
    fireEvent.click(screen.getByText("重试定时"));
    await waitFor(() => expect(screen.queryByText(/定时没挂上/)).toBeNull());
  });

  it("现在教: 开一段教学对话 (技能名事先定好)", async () => {
    api.expertBuildTeach.mockResolvedValue({ prompt: "我要教你… name=t123456-e1-j4", chat_key: "expert-teach:t123456-e1-j4" });
    const start = vi.fn();
    useUIStore.setState({ startEmailChat: start });
    render(<ExpertBuildCard onApplied={vi.fn()} />);
    fireEvent.click(await screen.findByText("现在教"));
    await waitFor(() => expect(start).toHaveBeenCalledWith("expert-teach:t123456-e1-j4", "我要教你… name=t123456-e1-j4"));
    expect(api.expertBuildTeach).toHaveBeenCalledWith("b12345678", 1, 3);
  });

  it("不勾某件 / 不定时: 发给后端改", async () => {
    api.expertBuildUpdate.mockResolvedValue({ build: build() });
    render(<ExpertBuildCard onApplied={vi.fn()} />);
    fireEvent.click(await screen.findByLabelText("要不要「下载证书」"));
    await waitFor(() => expect((screen.getByText("不定时") as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(screen.getByText("不定时"));
    await waitFor(() => expect(api.expertBuildUpdate).toHaveBeenCalledTimes(2));
    expect(api.expertBuildUpdate.mock.calls[0]).toEqual(["b12345678", 1, { jobs: [{ index: 3, include: false }] }]);
    expect(api.expertBuildUpdate.mock.calls[1]).toEqual(["b12345678", 1, { jobs: [{ index: 0, schedule: "" }] }]);
  });
});

describe("不打开编辑器也能改 (11/08 鸿波「一般人很难适应」)", () => {
  const broken = () => build({ experts: [{ ...build().experts[0], jobs: [{
    ...build().experts[0].jobs[0], problems: ["第 2 步 (word) 是小鲶自己想、自己写的一步, 它不能生成文件"],
    chain: [{ id: "script", label: "小鲶：写分镜脚本", block: "xiaonian", outward: false, each: false, cond: null },
            { id: "push", label: "推送到我的微信", block: "action", outward: true, each: false, cond: null }] }] }] });

  it("步骤链直接画在卡上; 问题说人话 (不带步骤 id)", async () => {
    api.expertBuildList.mockResolvedValue({ builds: [broken()] });
    render(<ExpertBuildCard onApplied={vi.fn()} />);
    expect(await screen.findByText("小鲶：写分镜脚本")).toBeTruthy();
    expect(screen.getByText(/🔔 推送到我的微信/)).toBeTruthy();
    expect(screen.getByText(/还没搭好: 第 2 步 是小鲶自己想/)).toBeTruthy();
    expect(screen.queryByText(/\(word\)/)).toBeNull();
  });

  it("让小鲶修好: 不带话发给后端, 卡跟着刷新", async () => {
    api.expertBuildList.mockResolvedValue({ builds: [broken()] });
    api.expertBuildRevise.mockResolvedValue({ build: build() });
    render(<ExpertBuildCard onApplied={vi.fn()} />);
    fireEvent.click(await screen.findByText("让小鲶修好"));
    await waitFor(() => expect(screen.queryByText("让小鲶修好")).toBeNull());
    expect(api.expertBuildRevise).toHaveBeenCalledWith("b12345678", 1, 0, "");
  });

  it("改一改: 一句话说要怎么改", async () => {
    api.expertBuildRevise.mockResolvedValue({ build: build() });
    render(<ExpertBuildCard onApplied={vi.fn()} />);
    fireEvent.click(await screen.findByText("改一改"));
    fireEvent.change(screen.getByLabelText("一句话说要怎么改"), { target: { value: "不要 Word 了" } });
    fireEvent.click(screen.getByText("改"));
    await waitFor(() => expect(api.expertBuildRevise).toHaveBeenCalledWith("b12345678", 1, 0, "不要 Word 了"));
    await waitFor(() => expect(screen.queryByLabelText("一句话说要怎么改")).toBeNull());
  });
});

describe("applyBlocker / jobHow", () => {
  it("有没搭好的工作流不让保存, 说清是哪件; 不勾就能保存", () => {
    const b = build();
    b.experts[0].jobs[0].problems = ["没有「x」这块积木"];
    expect(applyBlocker(b)).toMatch(/「资质专员」的「整理资质邮件」还没搭好/);
    b.experts[0].jobs[0].include = false;
    expect(applyBlocker(b)).toBeNull();
    b.experts[0].include = false;
    expect(applyBlocker(b)).toBe("一个专家都没勾");
  });
  it("教会了的那件", () => {
    expect(jobHow({ ...build().experts[0].jobs[3], attached: "personal/x" })).toBe("已经教会了");
  });
});

describe("plainProblem", () => {
  it("去掉写给小鲶的工具提示", async () => {
    const { plainProblem } = await import("./ExpertBuildCard");
    expect(plainProblem("第 2 步 没有「excel_export」这块积木 (用 catfish_workflow action=catalog 看有哪些)")).toBe("第 2 步 没有「excel_export」这块积木");
    expect(plainProblem("第 4 步 (deliver_docx) skill 写法不对")).toBe("第 4 步 skill 写法不对");
    expect(plainProblem("第 1 步 要有 id (小写字母开头, 例 mails)")).toBe("第 1 步 要有 id (小写字母开头, 例 mails)");
  });
});
