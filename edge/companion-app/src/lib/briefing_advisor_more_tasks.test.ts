import { describe, expect, it } from "vitest";

import { MORE_TASKS_MAX, splitByTaskLimit, TIER_TASK_LIMIT } from "./briefing_advisor_common";
import { parseAdvisorResult } from "./briefing_advisor_parse";
import { buildAdvisorTransformRequest } from "./briefing_advisor_request";

const opt = (label: string) => ({ label, tone: "balanced", summary: "建议" });
const task = (n: number, extra: Record<string, unknown> = {}) => ({
  id: 1, taskUid: `uid${n}xx`, title: `事项${n}`, urgency: "medium", reason: "r",
  contextRefs: [], complianceFlags: [], politicalFlags: [], ...extra,
});

describe("超出名额的事 (10/27)", () => {
  it("moreTasks 接在 mainTasks 后面, 不要求 options, id 重排", () => {
    const r = parseAdvisorResult({
      tier: "mid",
      mainTasks: [task(1, { options: [opt("A"), opt("B")] })],
      moreTasks: [task(2, { options: [] }), task(3)],
      handledSilently: [],
    });
    expect(r?.mainTasks.map((t) => t.title)).toEqual(["事项1", "事项2", "事项3"]);
    expect(r?.mainTasks.map((t) => t.id)).toEqual([1, 2, 3]);
  });

  it("moreTasks 跟 mainTasks 重复的跳过, 最多收 MORE_TASKS_MAX 件", () => {
    const more = Array.from({ length: MORE_TASKS_MAX + 5 }, (_, i) => task(i + 10));
    const r = parseAdvisorResult({
      tier: "frontline",
      mainTasks: [task(1, { options: [opt("A"), opt("B")] })],
      more_tasks: [task(1), ...more],
      handledSilently: [],
    });
    expect(r?.mainTasks).toHaveLength(1 + MORE_TASKS_MAX);
    expect(r?.mainTasks.filter((t) => t.title === "事项1")).toHaveLength(1);
  });

  it("mainTasks 的 options 校验不因 moreTasks 放宽", () => {
    expect(parseAdvisorResult({
      tier: "mid",
      mainTasks: [task(1, { options: [opt("A")] })],
      moreTasks: [],
      handledSilently: [],
    })).toBeNull();
  });

  it("先列名额内没办的, 已办的不占名额, 其余折叠", () => {
    const done = new Set([2]);
    const { shown, more } = splitByTaskLimit([1, 2, 3, 4, 5], 2, (n) => !done.has(n));
    expect(shown).toEqual([1, 2, 3]);
    expect(more).toEqual([4, 5]);
    expect(TIER_TASK_LIMIT).toEqual({ frontline: 8, mid: 4, senior: 2 });
  });

  it("Call 2 schema 带 moreTasks 且不要求 options", () => {
    const body = buildAdvisorTransformRequest({ model: "m", rawContent: "x", tier: "mid" });
    const props = body.tools[0].function.parameters.properties;
    expect(props.moreTasks.maxItems).toBe(MORE_TASKS_MAX);
    expect(props.moreTasks.items.properties.options).not.toHaveProperty("minItems");
    expect(props.mainTasks.items.properties.options.minItems).toBe(2);
  });
});
