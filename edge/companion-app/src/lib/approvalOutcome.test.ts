import { describe, expect, it } from "vitest";

import {
  approvalErrorMessage, approvalNotification, approvalTimeoutNote, approvalWindowText, matchesPreapproval,
  retryAfterTimeoutPrompt, RETRY_PREAPPROVAL_MS, shouldNotifyApproval,
} from "./approvalOutcome";

describe("approvalOutcome (10/16: 批准失败 / 超时不能没声音)", () => {
  it("409 = 等太久已失效, 要说清楚得重来", () => {
    // tauri_services.toolBridgeChatApproval 抛的原文
    const msg = approvalErrorMessage(new Error('chat_approval HTTP 409: {"error":"approval already resolved"}'));
    expect(msg).toContain("超时失效");
    expect(msg).toContain("重做");
    expect(msg).not.toContain("409");  // 员工不该看到状态码
  });

  it("404 / 401 各有各的说法, 其它错误带上原文", () => {
    expect(approvalErrorMessage(new Error("chat_approval HTTP 404: no such run"))).toContain("已经结束");
    expect(approvalErrorMessage(new Error("chat_approval HTTP 403: forbidden"))).toContain("没权限");
    expect(approvalErrorMessage(new Error("connection refused"))).toContain("connection refused");
    expect(approvalErrorMessage("boom")).toContain("boom");
  });

  it("超时那句话说明白了: 发生过、等了多久、已拒绝、怎么补", () => {
    const note = approvalTimeoutNote(1800);
    for (const w of ["批准", "30 分钟", "自动拒绝", "批准并继续"]) expect(note).toContain(w);
  });

  it("时长按 hermes 实际配置说, 不写死 5 分钟", () => {
    expect(approvalWindowText(1800)).toBe("30 分钟");
    expect(approvalWindowText(3600)).toBe("1 小时");
    expect(approvalWindowText(300)).toBe("5 分钟");
    expect(approvalWindowText(90)).toBe("90 秒");
  });

  it("批准并继续: 告诉小鲶现在批准了、重做那一步、接着做完", () => {
    const text = retryAfterTimeoutPrompt("rm -f /tmp/a.xlsx");
    for (const w of ["现在我批准", "重做这一步", "接着把任务做完", "rm -f /tmp/a.xlsx"]) expect(text).toContain(w);
    expect(retryAfterTimeoutPrompt("")).not.toContain("那一步是");
  });

  it("预先批准只认同一条命令、只在时限内", () => {
    const now = 1_000_000;
    const pre = { command: "rm -f /tmp/a.xlsx", until: now + RETRY_PREAPPROVAL_MS };
    expect(matchesPreapproval(pre, "rm -f /tmp/a.xlsx", now)).toBe(true);
    expect(matchesPreapproval(pre, " rm -f /tmp/a.xlsx\n", now)).toBe(true);
    expect(matchesPreapproval(pre, "rm -rf /tmp", now)).toBe(false);
    expect(matchesPreapproval(pre, "rm -f /tmp/a.xlsx", now + RETRY_PREAPPROVAL_MS + 1)).toBe(false);
    expect(matchesPreapproval(null, "rm -f /tmp/a.xlsx", now)).toBe(false);
    expect(matchesPreapproval({ command: "  ", until: now + 1 }, "  ", now)).toBe(false);
  });

  it("系统通知带上要执行什么, 太长截断", () => {
    const n = approvalNotification("remindctl update --uid 1AB56410 --notes '素材路径…'", 1800);
    expect(n.title).toBe("小鲶等你批准");
    expect(n.body).toContain("remindctl");
    expect(n.body).toContain("30 分钟");
    const long = approvalNotification("x".repeat(300), 1800);
    expect(long.body).toContain("…");
    expect(long.body.length).toBeLessThan(140);
    expect(approvalNotification("", 1800).body).toContain("有一步要你确认");
  });
});

describe("shouldNotifyApproval", () => {
  it("人在界面上就不弹通知, 切走 / 最小化才弹", () => {
    expect(shouldNotifyApproval({ hidden: false, hasFocus: () => true })).toBe(false);
    expect(shouldNotifyApproval({ hidden: false, hasFocus: () => false })).toBe(true);
    expect(shouldNotifyApproval({ hidden: true, hasFocus: () => true })).toBe(true);
    expect(shouldNotifyApproval(undefined)).toBe(true);  // 拿不到 document 宁可弹
    expect(shouldNotifyApproval({ hidden: false })).toBe(false);
  });
});
