import { describe, expect, it } from "vitest";

import {
  advisorCacheMatchesInput,
  advisorCacheSourceMeta,
  type AdvisorCache,
} from "./advisor_cache";
import type { AdvisorInput } from "./briefing_advisor_common";

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
      updatedAt: "2026-08-31T08:00:00+08:00",
      nextRecomputeAt: "2026-09-07T08:00:00+08:00",
    },
    emails: [],
    events: [],
    todos: [
      {
        text: "本周跟进 ISO 资质采购",
        line: 0,
        source: "reminder",
        section: "工作",
        origin: "reminders",
        reminder_id: "reminder-1",
        due_date_iso: "2026-09-05T10:00:00+08:00",
      },
    ],
    ctx: {
      distilledFacts: "历史：秦树鹏入职后，一级建造师补位已归档。",
      recentSessionBriefs: [],
      workplan: "本周完成 ISO 资质采购跟进",
      projects: "",
      weeklyReports: [],
      hermesMemoryRecent: "秦树鹏的旧事项已经 resolved。",
    },
    urgencyMap: {},
    model: "catfish-public-qwen-flash",
  };
}

function cacheFor(current: AdvisorInput): AdvisorCache {
  return {
    computedAt: new Date().toISOString(),
    result: {
      tier: "mid",
      mainTasks: [],
      handledSilently: [],
    },
    model: current.model,
    sourceMeta: advisorCacheSourceMeta(current),
  };
}

describe("advisor cache source validity", () => {
  it("same current snapshot matches regardless of source array order", () => {
    const current = input();
    const reordered = {
      ...current,
      todos: [...current.todos].reverse(),
    };
    expect(advisorCacheMatchesInput(cacheFor(current), reordered)).toBe(true);
  });

  it("changed Reminder identity invalidates the cache", () => {
    const current = input();
    const changed = {
      ...current,
      todos: current.todos.map((todo) => ({ ...todo, text: "本周跟进 CMMI 资质" })),
    };
    expect(advisorCacheMatchesInput(cacheFor(current), changed)).toBe(false);
  });

  it("old cache without source metadata is never current", () => {
    const current = input();
    const oldCache = cacheFor(current);
    delete oldCache.sourceMeta;
    expect(advisorCacheMatchesInput(oldCache, current)).toBe(false);
  });
});

