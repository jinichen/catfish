/** deadline 主动信号 (9/29 重做): 看任务库, 按日历天算。
 *
 * 鸿波 9/29 撞的两条: 气泡说 "今天是 9/30 — 你 journal 里提过这个: ISO系列资质采购
 * 招投标 (一期增补/二期) | 9/30" —— 那件事 9/7 就改名、截止改到 12/31 了; 而且
 * 当时是 9/29 早上, "9/30" 是明天不是今天。
 */
import { describe, expect, it } from "vitest";
import { calendarDaysUntil, detectDeadline } from "./triggers";

const NOW = new Date(2026, 8, 29, 7, 27); // 9/29 07:27

describe("detectDeadline", () => {
  it("9/29 早上看 9/30 是「明天」, 不是「今天」", () => {
    expect(calendarDaysUntil(new Date("2026-09-30T00:00:00"), NOW)).toBe(1);
    expect(calendarDaysUntil(new Date("2026-09-29T23:59:00"), NOW)).toBe(0);
    const r = detectDeadline({
      tasks: [{ text: "资质集中采购二期取证", due_date_iso: "2026-09-30T18:00:00" }],
      now: NOW,
    });
    expect(r?.message).toContain("明天是 9/30");
    expect(r?.message).toContain("资质集中采购二期取证");
    expect(r?.context.days_until).toBe(1);
    expect(r?.dedupe_key).toBe("deadline:9/30:资质集中采购二期取证");
  });

  it("只看任务库里的活跃待办 —— 没有 3 天内到期的就不出声", () => {
    expect(
      detectDeadline({
        tasks: [
          { text: "高新资质申报跟进", due_date_iso: "2026-12-31T00:00:00" },
          { text: "已过期的", due_date_iso: "2026-09-20T00:00:00" },
          { text: "没截止", due_date_iso: null },
        ],
        now: NOW,
      }),
    ).toBeNull();
    expect(detectDeadline({ tasks: [], now: NOW })).toBeNull();
  });

  it("几条都快到期时挑最急的, 同一天按优先级; 备注带进气泡", () => {
    const r = detectDeadline({
      tasks: [
        { text: "三天后", due_date_iso: "2026-10-02T00:00:00" },
        { text: "今天但不急", due_date_iso: "2026-09-29T00:00:00", priority: 0 },
        { text: "今天要紧", due_date_iso: "2026-09-29T00:00:00", priority: 1, body: "等采购部回复\n再发标" },
      ],
      now: NOW,
    });
    expect(r?.context.task_title).toBe("今天要紧");
    expect(r?.message).toBe("今天是 9/29 — 待办「今天要紧」到期（备注: 等采购部回复 再发标）. 还差啥, 我帮你?");
  });
});
