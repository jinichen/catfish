import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import LoadingProgress from "./LoadingProgress";

describe("早安进度", () => {
  it.each(["profile_loading", "data_loading", "llm_running"] as const)("%s 不承诺估计时间", (phase) => {
    const html = renderToStaticMarkup(<LoadingProgress phase={phase} startedAt={Date.now() - 320000} model="private-main" />);
    expect(html).toContain("已等待 5:20");
    expect(html).not.toContain("通常");
    expect(html).not.toContain("正在接收模型输出");
  });
  it("首轮说明只整理任务，保留停止入口", () => {
    const html = renderToStaticMarkup(<LoadingProgress phase="llm_running" startedAt={Date.now() + 1000} model="flash" onCancel={() => {}} />);
    expect(html).toContain("核对来源 · 整理任务");
    expect(html).toContain("已等待 0:00");
    expect(html).toContain("停止本次分析");
  });
});
