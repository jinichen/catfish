/** 删一步 / 一个参数, 后面引用它的地方一起清掉 (11/08 删了「生成 Word」那步, 最后一步还引用着它)。 */
import { describe, expect, it } from "vitest";

import type { Workflow } from "./tauri_workflows";
import { removeParam, removeStep } from "./workflowRefs";

const wf = (): Workflow => ({
  kind: "workflow",
  params: { topic: { type: "text", label: "主题" } },
  steps: [
    { id: "brief", xiaonian: "整理 ${params.topic}", returns: { output_format: "text" } },
    { id: "word", xiaonian: "生成", returns: { docx_path: "text" } },
    { id: "md", action: "wiki_create", params: { title: "${params.topic} 分镜", body: "${steps.word.docx_path}" },
      when: { of: "${steps.word.docx_path}", is: "not_empty" } },
    { id: "report", xiaonian: "汇总: ${steps.brief.output_format}",
      input: "主题：${steps.brief.topic}；文件：${steps.word.docx_path}；格式：${steps.brief.output_format}", returns: { summary: "text" } },
  ],
} as Workflow);

describe("removeStep", () => {
  it("后面引用它的: 条件去掉、整个是它的清空、夹在话里的连小标签一起删", () => {
    const r = removeStep(wf(), 1);
    expect(r.wf.steps.map((s) => s.id)).toEqual(["brief", "md", "report"]);
    expect(r.touched).toEqual([2, 3]);
    expect(r.wf.steps[1].when).toBeUndefined();
    expect(r.wf.steps[1].params).toEqual({ title: "${params.topic} 分镜", body: "" });
    expect(r.wf.steps[2].input).toBe("主题：${steps.brief.topic}；格式：${steps.brief.output_format}");
  });
  it("没人引用就只删这一步", () => {
    const r = removeStep(wf(), 3);
    expect(r.touched).toEqual([]);
    expect(r.wf.steps).toHaveLength(3);
  });
  it("不会误伤名字是它前缀的步骤 (word / words)", () => {
    const w = wf();
    w.steps[3].input = "${steps.words.x}";
    expect(removeStep(w, 1).wf.steps[2].input).toBe("${steps.words.x}");
  });
});

describe("removeParam", () => {
  it("参数删掉, 说明里 / 积木参数里用到它的地方清掉", () => {
    const r = removeParam(wf(), "topic");
    expect(r.wf.params).toEqual({});
    expect(r.touched).toEqual([1, 3]);
    expect(r.wf.steps[0].xiaonian).toBe("整理");
    expect(r.wf.steps[2].params?.title).toBe("分镜");
  });
});
