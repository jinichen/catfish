import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DataDiagnosisCard } from "./DataDiagnosisCard";

describe("分析失败的数据诊断", () => {
  it("保留实际数量和来源错误，不把分析失败解释为全空或格式错误", () => {
    const html = renderToStaticMarkup(
      <DataDiagnosisCard
        title="早安分析未完成"
        description="分析未通过当前来源校验"
        statuses={{
          emails: { ok: true, count: 3 },
          events: { ok: true, count: 0 },
          todos: { ok: false, count: 0, reason: "任务读取超时" },
        }}
        onRetry={() => {}}
      />,
    );
    expect(html).toContain("早安分析未完成");
    expect(html).toContain("分析未通过当前来源校验");
    expect(html).toContain("3 条");
    expect(html).toContain("任务读取超时");
    expect(html).not.toContain("都拉到 0 条");
    expect(html).not.toContain("模型返回没成结构");
  });
});
