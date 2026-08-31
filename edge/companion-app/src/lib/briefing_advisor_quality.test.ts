import { describe, expect, it } from "vitest";

import type { AdvisorInput, AdvisorResult } from "./briefing_advisor_common";
import {
  filterAdvisorResultByEvidence,
  isAdvisorTaskBackedByCurrentInput,
  isAdvisorResultCacheSafe,
  isAdvisorResultGrounded,
  isAdvisorTransformSourceUsable,
} from "./briefing_advisor_quality";


function input(): AdvisorInput {
  return {
    profile: {
      tier: "mid",
      centralState: "strong",
      style: "direct",
      confidence: 0.9,
      keyPeople: [],
      keyProjects: [],
      evidence: [],
      updatedAt: "2026-08-24T08:00:00+08:00",
      nextRecomputeAt: "2026-08-31T08:00:00+08:00",
    },
    emails: [],
    events: [],
    todos: [
      {
        text: "ISO系列资质采购招投标，等待采购公司审核反馈",
        line: 1,
        source: "checkbox",
        section: "本周",
      },
    ],
    ctx: {
      distilledFacts: "",
      recentSessionBriefs: [],
      workplan: "9月前锁定招投标关键节点",
      projects: "",
      weeklyReports: [],
      hermesMemoryRecent: "",
    },
    urgencyMap: {},
    model: "catfish-public-qwen-flash",
  };
}

function result(title: string): AdvisorResult {
  return {
    tier: "mid",
    mainTasks: [
      {
        id: 1,
        taskUid: "iso001",
        title,
        urgency: "high",
        reason: "采购审核反馈是当前关键节点",
        options: [
          { label: "催办", tone: "urgent", summary: "向采购公司确认审核进度" },
          { label: "稳妥", tone: "balanced", summary: "先锁定9月节点再跟进" },
        ],
        complianceFlags: [],
        politicalFlags: [],
        contextRefs: ["ISO系列资质采购招投标"],
      },
    ],
    handledSilently: [],
  };
}

describe("advisor Qwen 空转质量门", () => {
  it("拒绝线上 Qwen 的系统占位回复进入结构化 Call 2", () => {
    expect(
      isAdvisorTransformSourceUsable(
        "系统提示已接收。你说吧，需要我做什么？",
        input(),
      ),
    ).toBe(false);
  });

  it("允许包含真实事项名称的简短业务结论", () => {
    expect(
      isAdvisorTransformSourceUsable(
        "ISO系列资质采购招投标需跟进采购审核反馈，并锁定9月节点。",
        input(),
      ),
    ).toBe(true);
  });

  it("拒绝 Call 2 编造的等待用户输入任务", () => {
    expect(isAdvisorResultGrounded(result("等待用户输入具体任务"), input())).toBe(false);
    expect(isAdvisorResultCacheSafe(result("等待用户输入具体任务"))).toBe(false);
  });

  it("接受能在本轮 TODO 中找到依据的结构化任务", () => {
    expect(isAdvisorResultGrounded(result("3项新资质采购招投标（ISO系列）"), input())).toBe(true);
    expect(isAdvisorResultCacheSafe(result("3项新资质采购招投标（ISO系列）"))).toBe(true);
  });

  it("有明确 TODO 时拒绝空主菜结果", () => {
    const empty: AdvisorResult = {
      tier: "mid",
      mainTasks: [],
      handledSilently: [],
    };
    expect(isAdvisorResultGrounded(empty, input())).toBe(false);
  });

  it("保留真实 ISO 任务并删除仅靠同领域通用词搭边的 CMMI 任务", () => {
    const mixed = result("3项新资质采购招投标（ISO系列）");
    mixed.mainTasks.push({
      ...mixed.mainTasks[0],
      id: 2,
      taskUid: "cmmi01",
      title: "CMMI-5 / CSMM-4 取证后的资质盘点复盘",
      reason: "检查招投标评分系统",
      contextRefs: ["CMMI-5 有效期"],
    });

    const filtered = filterAdvisorResultByEvidence(mixed, input());
    expect(filtered?.mainTasks.map((task) => task.taskUid)).toEqual(["iso001"]);
  });

  it("历史 distilled facts 不能让已结束任务重新获得当前依据", () => {
    const historicalOnly = {
      ...result("秦树鹏一级建造师补位"),
      mainTasks: [
        {
          ...result("秦树鹏一级建造师补位").mainTasks[0],
          taskUid: "qinjnr",
          contextRefs: ["distilled_facts.md: 已 resolved"],
        },
      ],
    };
    const historicalInput = {
      ...input(),
      profile: {
        ...input().profile,
        keyPeople: [{ name: "秦树鹏", relation: "下属" }],
      },
      todos: [],
      ctx: {
        ...input().ctx,
        distilledFacts: "一级建造师补位 | resolved（秦树鹏入职，已归档）",
        workplan: "",
      },
    };
    expect(isAdvisorTaskBackedByCurrentInput(historicalOnly.mainTasks[0], historicalInput)).toBe(false);
    expect(isAdvisorResultGrounded(historicalOnly, historicalInput)).toBe(false);
    expect(filterAdvisorResultByEvidence(historicalOnly, historicalInput)).toBeNull();
  });

  it("当前任务的选项不能夹带当前来源未证明的历史联系人", () => {
    const contaminated = result("3项新资质采购招投标（ISO系列）");
    contaminated.mainTasks[0].options[1].summary = "申请备用方案并同步给秦树鹏跟进";
    const currentInput = {
      ...input(),
      profile: {
        ...input().profile,
        keyPeople: [{ name: "秦树鹏", relation: "下属" }],
      },
    };

    expect(isAdvisorResultGrounded(contaminated, currentInput)).toBe(false);
    expect(filterAdvisorResultByEvidence(contaminated, currentInput)?.mainTasks[0].options[1].summary)
      .toBe("申请备用方案并同步给相关负责人跟进");
  });

  it("拒绝并清空依赖历史资料生成的洞察字段", () => {
    const historical = result("3项新资质采购招投标（ISO系列）");
    historical.graveyard = [{
      name: "秦树鹏一级建造师补位",
      lastSeen: "数月前",
      evidence: "distilled_facts.md",
    }];
    historical.blindSpots = [{
      topic: "旧项目",
      signal: "历史记忆",
      evidence: "MEMORY.md",
      reflectPrompt: "查看旧项目",
    }];

    expect(isAdvisorResultGrounded(historical, input())).toBe(false);
    expect(filterAdvisorResultByEvidence(historical, input())).toMatchObject({
      graveyard: [],
      blindSpots: [],
      subconscious: [],
    });
  });
});
