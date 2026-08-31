/**
 * BL-ADVISOR-RESOLVED-HARDFILTER (P3.5.202 C 方案 7/9) 单测.
 *
 * 跑法: cd edge/companion-app && pnpm test briefing_advisor_resolved
 *
 * 撤 P3.3.40 时代 regex keyword hardcode 单测 (30+ 个 it 都是测硬编码关键字)
 * — C 方案后 chatSummaryLooksResolved / titleLooksResolved / RESOLVED_*_PATTERNS
 * 已从 briefing_advisor.ts 撤除.
 *
 * 现在的 filter 只看 chatStatus:
 *   - resolved / paused → drop 挪去 handled_silently
 *   - pending / undefined → 保守 keep 在 main_tasks (让 LLM 主判)
 *
 * 语义驱动, LLM 语言理解替 keyword regex. 边界 case 靠 summarizeTaskChat 的
 * LLM prompt + JSON schema 强约束保证.
 */

import { describe, expect, it } from "vitest";
import { __test__ } from "./briefing_advisor";
import { getEffectiveStatusByUid, getManualStatusByUid } from "./advisor_cache";
import type {
  AdvisorResult,
  MainTask,
  HandledSilentlyItem,
} from "./briefing_advisor";

const { filterResolvedTasks } = __test__;

// helper
function mkResult(mainTasks: MainTask[]): AdvisorResult {
  return {
    tier: "mid",
    mainTasks,
    handledSilently: [],
  };
}

function mkTask(overrides: Partial<MainTask> & { id: number; taskUid: string; title: string }): MainTask {
  return {
    urgency: "medium",
    reason: "",
    options: [],
    complianceFlags: [],
    politicalFlags: [],
    contextRefs: [],
    ...overrides,
  };
}

// ─── filterResolvedTasks 主逻辑 ────────────────────────────────────────

describe("filterResolvedTasks — status 语义驱动 (P3.5.202 C 方案)", () => {
  it("chatStatus='resolved' → drop, id 重排", () => {
    const result = mkResult([
      mkTask({ id: 1, taskUid: "aaa", title: "预警邮件处理" }),
      mkTask({ id: 2, taskUid: "bbb", title: "季度汇报" }),
    ]);
    const prev = [
      { taskUid: "aaa", title: "预警邮件处理", urgency: "medium", chatSummary: "员工说是误报", chatStatus: "resolved" as const },
      { taskUid: "bbb", title: "季度汇报", urgency: "medium", chatSummary: "还在写", chatStatus: "pending" as const },
    ];
    const out = filterResolvedTasks(result, prev);
    expect(out.mainTasks).toHaveLength(1);
    expect(out.mainTasks[0].taskUid).toBe("bbb");
    expect(out.mainTasks[0].id).toBe(1);  // 重排
    const dropped = out.handledSilently.find((h) => h.type === "task_resolved");
    expect(dropped).toBeDefined();
    expect(dropped!.category).toContain("预警邮件处理");
  });

  it("员工撤销后 taskState='pending' 覆盖 chatStatus='resolved'", () => {
    const result = mkResult([
      mkTask({ id: 1, taskUid: "reopen", title: "重新打开的任务" }),
    ]);
    const out = filterResolvedTasks(result, [
      {
        taskUid: "reopen",
        title: "重新打开的任务",
        urgency: "high",
        chatSummary: "之前聊天里说已完成",
        chatStatus: "resolved",
        taskState: "pending",
      },
    ]);
    expect(out.mainTasks).toHaveLength(1);
    expect(out.handledSilently).toHaveLength(0);
  });

  it("chatStatus='paused' → drop (员工主动搁置)", () => {
    const result = mkResult([
      mkTask({ id: 1, taskUid: "ccc", title: "福建智能体企业征集" }),
    ]);
    const prev = [
      { taskUid: "ccc", title: "福建智能体企业征集", urgency: "high", chatSummary: "员工说暂时关闭, 等通知", chatStatus: "paused" as const },
    ];
    const out = filterResolvedTasks(result, prev);
    expect(out.mainTasks).toHaveLength(0);
    expect(out.handledSilently.some((h) => h.type === "task_resolved")).toBe(true);
  });

  it("chatStatus='pending' → keep (球在员工手里)", () => {
    const result = mkResult([
      mkTask({ id: 1, taskUid: "ddd", title: "催老李回复" }),
    ]);
    const prev = [
      { taskUid: "ddd", title: "催老李回复", urgency: "medium", chatSummary: "已发邮件催, 等回复", chatStatus: "pending" as const },
    ];
    const out = filterResolvedTasks(result, prev);
    expect(out.mainTasks).toHaveLength(1);
    expect(out.mainTasks[0].taskUid).toBe("ddd");
  });

  it("chatStatus undefined (老 cache 未迁移) → 保守 keep", () => {
    // C 方案后 filter 不再有 regex 兜底, 无 status 一律保守 keep.
    // 老 cache 会被 _ensureTaskChatSummariesFreshImpl 里 cacheValid 检查 status
    // 强制重跑, 一次后就有 status 了.
    const result = mkResult([
      mkTask({ id: 1, taskUid: "eee", title: "误报修正 (LLM 老 hint)" }),
    ]);
    const prev = [
      { taskUid: "eee", title: "误报修正 (LLM 老 hint)", urgency: "low", chatSummary: "已确认是误报", chatStatus: undefined },
    ];
    const out = filterResolvedTasks(result, prev);
    // 无 status → keep (让 LLM 主判 or 员工再说一句触发新 summarize)
    expect(out.mainTasks).toHaveLength(1);
  });

  it("多个 task 部分 drop, id 重排 1..N, handledSilently 追加", () => {
    const result = mkResult([
      mkTask({ id: 1, taskUid: "t1", title: "A 项目" }),
      mkTask({ id: 2, taskUid: "t2", title: "B 项目 已办完" }),
      mkTask({ id: 3, taskUid: "t3", title: "C 项目" }),
      mkTask({ id: 4, taskUid: "t4", title: "D 项目 员工暂缓" }),
    ]);
    const prev = [
      { taskUid: "t1", title: "A 项目", urgency: "high", chatSummary: "在做", chatStatus: "pending" as const },
      { taskUid: "t2", title: "B 项目 已办完", urgency: "high", chatSummary: "已交付", chatStatus: "resolved" as const },
      { taskUid: "t3", title: "C 项目", urgency: "medium", chatSummary: "还没聊", chatStatus: "pending" as const },
      { taskUid: "t4", title: "D 项目 员工暂缓", urgency: "medium", chatSummary: "员工说先放放", chatStatus: "paused" as const },
    ];
    const out = filterResolvedTasks(result, prev);
    expect(out.mainTasks).toHaveLength(2);
    expect(out.mainTasks.map((t) => t.taskUid)).toEqual(["t1", "t3"]);
    expect(out.mainTasks.map((t) => t.id)).toEqual([1, 2]);  // 1..N 重排
    expect(out.handledSilently.filter((h) => h.type === "task_resolved")).toHaveLength(2);
  });

  it("空 previousTasks → 全部 keep", () => {
    const result = mkResult([
      mkTask({ id: 1, taskUid: "t1", title: "全新 task" }),
    ]);
    const out = filterResolvedTasks(result, []);
    expect(out.mainTasks).toHaveLength(1);
    expect(out.handledSilently).toHaveLength(0);
  });

  it("匹配 taskUid 缺失 → 保守 keep (不误 drop)", () => {
    const result = mkResult([
      mkTask({ id: 1, taskUid: "unknown", title: "新 task" }),
    ]);
    const prev = [
      { taskUid: "different", title: "另一个", urgency: "high", chatStatus: "resolved" as const },
    ];
    const out = filterResolvedTasks(result, prev);
    expect(out.mainTasks).toHaveLength(1);
  });
});

describe("advisor cache 手工状态 selector", () => {
  it("pending 手工状态覆盖 chat resolved，且不被当成 ignored", () => {
    const cache = {
      computedAt: "2026-08-27T00:00:00Z",
      result: mkResult([]),
      taskChatSummaries: {
        reopen: {
          summary: "员工重新打开",
          status: "resolved" as const,
          manualStatus: "pending" as const,
          jsonlSize: 1,
          computedAt: "2026-08-27T00:00:00Z",
        },
      },
    };
    expect(getEffectiveStatusByUid(cache).has("reopen")).toBe(false);
    expect(getManualStatusByUid(cache).has("reopen")).toBe(false);
  });
});

// P3.3.40 时代那 30+ 个 regex 边界 case 单测已撤 — C 方案后 chatSummaryLooksResolved
// / titleLooksResolved 从 __test__ export 撤出, 硬编码 keyword regex 全部
// 从 briefing_advisor.ts 撤除. 语义边界由 summarizeTaskChat 的 LLM prompt
// (P3.5.202 强 JSON schema) 保证, 不是客户端 regex 的活.
