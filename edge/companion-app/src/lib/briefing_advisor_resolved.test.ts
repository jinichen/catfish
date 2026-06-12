/**
 * BL-ADVISOR-RESOLVED-HARDFILTER (P3.3.40) 单测.
 *
 * 跑法: cd edge/companion-app && pnpm test briefing_advisor_resolved
 *
 * 这是 deterministic 客户端兜底 — 不依赖 LLM 听话. 红线:
 *   - chat summary 含"已确认是误报" / "不存在" → drop
 *   - chat summary 含"已签字" / "已结项" → drop
 *   - title 含 "误报修正" 即使 summary 没明说 → drop  (6/12 实际撞的 case)
 *   - chat summary 含"已发起申请等审批" → drop (球已踢给对方)
 *   - chat summary 含"已发邮件催" 没下文 → 仍保留 main_tasks
 *   - chat summary 空 + title 普通 → 仍保留
 *   - 多个 task 部分 drop, id 重排, handledSilently 追加
 *   - 模糊不命中时保守保留 — 错放 cost 小, 错 drop cost 大
 */

import { describe, expect, it } from "vitest";
import { __test__ } from "./briefing_advisor";
import type {
  AdvisorResult,
  MainTask,
  HandledSilentlyItem,
} from "./briefing_advisor";

const { chatSummaryLooksResolved, titleLooksResolved, filterResolvedTasks } =
  __test__;

// ─── chatSummaryLooksResolved 单元 ────────────────────────────────

describe("chatSummaryLooksResolved", () => {
  it("空 summary → false", () => {
    expect(chatSummaryLooksResolved("")).toBe(false);
    expect(chatSummaryLooksResolved("   ")).toBe(false);
  });

  it("已确认是误报 → true", () => {
    expect(
      chatSummaryLooksResolved(
        "员工已确认 5 封安全预警邮件不存在, 待办为误报",
      ),
    ).toBe(true);
  });

  it("已确认不存在 → true", () => {
    expect(
      chatSummaryLooksResolved("员工已确认该流程不存在, 不需要跟进"),
    ).toBe(true);
  });

  it("是误报 直接命中 → true", () => {
    expect(chatSummaryLooksResolved("查证后发现是误报, 不再跟进")).toBe(true);
  });

  it("属误报 → true", () => {
    expect(chatSummaryLooksResolved("该预警属误报")).toBe(true);
  });

  it("已结项 → true", () => {
    expect(chatSummaryLooksResolved("该项目已结项")).toBe(true);
  });

  it("已签字交付 → true", () => {
    expect(
      chatSummaryLooksResolved("黄捷已签字, 资质方案已交付给办公室"),
    ).toBe(true);
  });

  it("已发起申请等审批 → true (球已踢给对方)", () => {
    expect(
      chatSummaryLooksResolved(
        "员工已发起加计扣除申请流程, 当前等财务总监审批",
      ),
    ).toBe(true);
  });

  it("已提交流程等批复 → true", () => {
    expect(
      chatSummaryLooksResolved("已提交审批流程, 等待主管批复"),
    ).toBe(true);
  });

  it("已发邮件催了等回复 → false (球还在员工手里, 不算 resolved)", () => {
    expect(
      chatSummaryLooksResolved("已发邮件催了对方, 还没收到回复"),
    ).toBe(false);
  });

  it("讨论了一下还在拿不定主意 → false", () => {
    expect(
      chatSummaryLooksResolved("跟 AI 讨论了三个口径, 员工还在权衡"),
    ).toBe(false);
  });

  it("不再有效 → true", () => {
    expect(chatSummaryLooksResolved("该批文不再有效")).toBe(true);
  });

  it("已作废 → true", () => {
    expect(chatSummaryLooksResolved("文件已作废, 不需要再处理")).toBe(true);
  });

  it("确认无风险 → true", () => {
    expect(
      chatSummaryLooksResolved("经过员工跟 AI 核查后确认无风险"),
    ).toBe(true);
  });
});

// ─── titleLooksResolved 单元 ──────────────────────────────────────

describe("titleLooksResolved", () => {
  it("空 title → false", () => {
    expect(titleLooksResolved("")).toBe(false);
  });

  it("误报修正 (6/12 实际撞的 case) → true", () => {
    expect(titleLooksResolved("6 月安全预警邮件督办 — 误报修正")).toBe(true);
  });

  it("是误报 → true", () => {
    expect(titleLooksResolved("CSMM-4 评估事项 - 是误报")).toBe(true);
  });

  it("已结项 → true", () => {
    expect(titleLooksResolved("中电福富立项 已结项")).toBe(true);
  });

  it("已作废 → true", () => {
    expect(titleLooksResolved("资质方案 - 已作废")).toBe(true);
  });

  it("普通待办 → false", () => {
    expect(titleLooksResolved("老李催资质方案范围")).toBe(false);
  });

  it("报告标题里有"报告"二字不算误报 → false", () => {
    expect(titleLooksResolved("Q2 项目复盘报告撰写")).toBe(false);
  });
});

// ─── filterResolvedTasks 整段 ─────────────────────────────────────

function makeTask(
  taskUid: string,
  title: string,
  overrides: Partial<MainTask> = {},
): MainTask {
  return {
    id: 1,
    taskUid,
    title,
    urgency: "medium",
    reason: undefined,
    options: [],
    complianceFlags: [],
    politicalFlags: [],
    contextRefs: [],
    ...overrides,
  };
}

function makeResult(
  mainTasks: MainTask[],
  handledSilently: HandledSilentlyItem[] = [],
): AdvisorResult {
  return { tier: "mid", mainTasks, handledSilently };
}

describe("filterResolvedTasks", () => {
  it("无 prev task → 原 result", () => {
    const r = makeResult([
      makeTask("aaa111", "正常任务"),
      makeTask("bbb222", "另一任务"),
    ]);
    const out = filterResolvedTasks(r, []);
    expect(out.mainTasks).toHaveLength(2);
    expect(out.handledSilently).toHaveLength(0);
  });

  it("6/12 实际撞 case — title 含'误报修正' → drop + 挪 handledSilently", () => {
    const r = makeResult(
      [
        makeTask("a1b2c3", "6 月安全预警邮件督办 — 误报修正", {
          id: 1,
          urgency: "medium",
        }),
        makeTask("d4e5f6", "老李催资质方案", { id: 2, urgency: "high" }),
      ],
      [{ type: "email_archive", count: 5, category: "低优先归档" }],
    );
    const prev = [
      {
        taskUid: "a1b2c3",
        title: "6 月安全预警邮件督办",
        urgency: "medium" as const,
        chatSummary: "员工已确认 5 封安全预警邮件不存在, 待办为误报",
      },
    ];
    const out = filterResolvedTasks(r, prev);

    // 误报 task 已 drop
    expect(out.mainTasks).toHaveLength(1);
    expect(out.mainTasks[0].taskUid).toBe("d4e5f6");
    // id 重排成 1 (原 2)
    expect(out.mainTasks[0].id).toBe(1);

    // handledSilently 多了一条 task_resolved
    expect(out.handledSilently).toHaveLength(2);
    const resolved = out.handledSilently.find(
      (h) => h.type === "task_resolved",
    );
    expect(resolved).toBeDefined();
    expect(resolved!.count).toBe(1);
    expect(resolved!.category).toContain("误报修正");
  });

  it("summary 含'已签字' → drop", () => {
    const r = makeResult([
      makeTask("uid001", "资质方案签字推进", { id: 1 }),
      makeTask("uid002", "其他任务", { id: 2 }),
    ]);
    const prev = [
      {
        taskUid: "uid001",
        title: "资质方案签字推进",
        urgency: "high" as const,
        chatSummary: "黄捷已签字, 方案已交付给办公室",
      },
    ];
    const out = filterResolvedTasks(r, prev);
    expect(out.mainTasks).toHaveLength(1);
    expect(out.mainTasks[0].taskUid).toBe("uid002");
  });

  it("summary 含'已发起申请等审批' → drop (球已踢出去)", () => {
    const r = makeResult([
      makeTask("uid003", "加计扣除申报", { id: 1 }),
    ]);
    const prev = [
      {
        taskUid: "uid003",
        title: "加计扣除申报",
        urgency: "medium" as const,
        chatSummary: "员工已发起加计扣除申请流程, 当前等财务总监审批",
      },
    ];
    const out = filterResolvedTasks(r, prev);
    expect(out.mainTasks).toHaveLength(0);
    expect(out.handledSilently).toHaveLength(1);
  });

  it("summary 含'已发邮件催了等回复' → 仍保留 (球还在员工手里)", () => {
    const r = makeResult([
      makeTask("uid004", "催老李回复", { id: 1 }),
    ]);
    const prev = [
      {
        taskUid: "uid004",
        title: "催老李回复",
        urgency: "medium" as const,
        chatSummary: "员工已发邮件催了老李, 还没收到回复",
      },
    ];
    const out = filterResolvedTasks(r, prev);
    expect(out.mainTasks).toHaveLength(1);
    expect(out.handledSilently).toHaveLength(0);
  });

  it("正常 task summary 不命中 → 保留", () => {
    const r = makeResult([
      makeTask("uid005", "老李催资质方案", { id: 1 }),
    ]);
    const prev = [
      {
        taskUid: "uid005",
        title: "老李催资质方案",
        urgency: "high" as const,
        chatSummary: "跟 AI 讨论了三个口径, 员工还在权衡 B 和 C",
      },
    ];
    const out = filterResolvedTasks(r, prev);
    expect(out.mainTasks).toHaveLength(1);
  });

  it("多 task 部分 drop, id 重排连续", () => {
    const r = makeResult([
      makeTask("uid006", "task 1", { id: 1, urgency: "high" }),
      makeTask("uid007", "task 2 - 误报修正", { id: 2, urgency: "medium" }), // title drop
      makeTask("uid008", "task 3", { id: 3, urgency: "medium" }), // summary drop
      makeTask("uid009", "task 4", { id: 4, urgency: "low" }),
    ]);
    const prev = [
      {
        taskUid: "uid008",
        title: "task 3",
        urgency: "medium" as const,
        chatSummary: "已确认该流程不存在, 是误报",
      },
    ];
    const out = filterResolvedTasks(r, prev);
    expect(out.mainTasks).toHaveLength(2);
    expect(out.mainTasks.map((t) => t.taskUid)).toEqual([
      "uid006",
      "uid009",
    ]);
    // id 重排成 1, 2
    expect(out.mainTasks.map((t) => t.id)).toEqual([1, 2]);
    // handledSilently 多了 2 条 (uid007 + uid008)
    expect(
      out.handledSilently.filter((h) => h.type === "task_resolved"),
    ).toHaveLength(2);
  });

  it("没 prev summary 但 title 命中 → drop", () => {
    // 此 case: LLM 自己生成的 title 就含"误报", 即使我们没 prev summary 也 drop
    const r = makeResult([
      makeTask("uid010", "Q1 安全审计 — 误报修正", { id: 1 }),
    ]);
    const out = filterResolvedTasks(r, []);
    // 走的是 title 路径, prev 即使空也 drop
    expect(out.mainTasks).toHaveLength(0);
  });

  it("保守原则 — 含'催'但没 resolved 信号 → 仍保留", () => {
    const r = makeResult([makeTask("uid011", "继续催财务出报表")]);
    const prev = [
      {
        taskUid: "uid011",
        title: "继续催财务出报表",
        urgency: "high" as const,
        chatSummary: "员工准备明天再催一次",
      },
    ];
    const out = filterResolvedTasks(r, prev);
    expect(out.mainTasks).toHaveLength(1);
  });
});
