/** 保留策略的兜底方向。
 *
 * 这是整套邮件档案里**唯一不可逆**的一项 —— 它决定要不要删员工服务器上的
 * 邮件。所以这些测试只盯一个方向: 拿不准的时候绝不能落到"会删"那一边。
 *
 * 同样的兜底在三层都有 (这里 / Rust imap_credentials / Python imap_config)。
 * 三层都兜不是冗余: 任何一层漏了, 后果都是删掉员工的邮件且无法恢复。
 */
import { describe, expect, it } from "vitest";

import {
  RETENTION_LABELS,
  RETENTION_ORDER,
  normalizeRetention,
} from "./tauri_imap";

describe("normalizeRetention", () => {
  it("认得四个合法值", () => {
    for (const v of ["immediate", "1w", "2w", "never"]) {
      expect(normalizeRetention(v)).toBe(v);
    }
  });

  it.each([
    undefined, null, "", "3w", "IMMEDIATE", "immediate ", " 1w", 0, 1, true, {}, [],
    "never ", "forever", "1week", "立即",
  ])("认不出来的一律 never: %p", (bad) => {
    expect(normalizeRetention(bad)).toBe("never");
  });

  it("大小写和空格**不**宽容", () => {
    /** 想过要不要 trim + toLowerCase。结论是不要:
     *
     *  宽容的解析会让 "IMMEDIATE " 变成真的立即删。而这个值的来源是代码
     *  (下拉框的 value), 不是人手输入 —— 出现变体本身就说明哪里不对,
     *  这时候退回 never 比"猜他想删"安全得多。
     *
     *  反过来想: 宽容解析能挽回的最好情况, 是员工少点一次下拉框。 */
    expect(normalizeRetention("Immediate")).toBe("never");
    expect(normalizeRetention(" 2w ")).toBe("never");
  });
});

describe("下拉框", () => {
  it("第一项是 never", () => {
    /** 顺序是按"删错了有多惨"排的, 不是按时长。第一项是员工最可能
     *  不小心选中/默认停留的那个, 所以必须是最安全的。 */
    expect(RETENTION_ORDER[0]).toBe("never");
  });

  it("越往后越激进", () => {
    expect(RETENTION_ORDER).toEqual(["never", "2w", "1w", "immediate"]);
  });

  it("四个值都有中文说明, 且说清楚会不会删", () => {
    for (const key of RETENTION_ORDER) {
      const label = RETENTION_LABELS[key];
      expect(label.length).toBeGreaterThan(4);
      expect(label).toMatch(key === "never" ? /不删除/ : /删除/);
    }
  });

  it("没有遗漏的值 —— 加了新策略就得补文案", () => {
    expect(Object.keys(RETENTION_LABELS).sort()).toEqual(
      [...RETENTION_ORDER].sort(),
    );
  });
});
