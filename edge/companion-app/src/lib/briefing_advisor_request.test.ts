import { describe, expect, it } from "vitest";

import {
  buildAdvisorAgentRequest,
  buildAdvisorTransformRequest,
} from "./briefing_advisor_request";

describe("Advisor 两阶段请求合同", () => {
  it("agent 分析使用场景参数且不强制结构化工具", () => {
    const body = buildAdvisorAgentRequest("model-a", "今日输入");

    expect(body.model).toBe("model-a");
    expect(body.max_tokens).toBe(6000);
    expect(body.temperature).toBe(0.4);
    expect(body.stream).toBe(false);
    expect(body.response_format).toEqual({ type: "json_object" });
    expect(body).not.toHaveProperty("tool_choice");
    expect(body).not.toHaveProperty("tools");
  });

  it("transform 使用低温并只强制 submit_advisor_result", () => {
    const body = buildAdvisorTransformRequest({
      model: "model-a",
      rawContent: "3项新资质采购招投标正在推进",
      tier: "mid",
    });

    expect(body.model).toBe("model-a");
    expect(body.max_tokens).toBe(6000);
    expect(body.temperature).toBe(0.1);
    expect(body.stream).toBe(false);
    expect(body.tools).toHaveLength(1);
    expect(body.tools[0].function.name).toBe("submit_advisor_result");
    expect(body.tool_choice.function.name).toBe("submit_advisor_result");
  });

  it("senior transform 允许零 options，不修改共享 schema", () => {
    const senior = buildAdvisorTransformRequest({
      model: "model-a",
      rawContent: "风险事项",
      tier: "senior",
    });
    const mid = buildAdvisorTransformRequest({
      model: "model-a",
      rawContent: "推进事项",
      tier: "mid",
    });

    const seniorOptions = senior.tools[0].function.parameters.properties.mainTasks
      .items.properties.options;
    const midOptions = mid.tools[0].function.parameters.properties.mainTasks
      .items.properties.options;
    expect(seniorOptions).not.toHaveProperty("minItems");
    expect(midOptions.minItems).toBe(2);
  });
});
