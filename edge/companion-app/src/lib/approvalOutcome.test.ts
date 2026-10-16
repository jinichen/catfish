import { describe, expect, it } from "vitest";

import {
  APPROVAL_TIMEOUT_NOTE, approvalErrorMessage, approvalNotification, shouldNotifyApproval,
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

  it("超时那句话说明白了三件事: 发生过、已拒绝、怎么补", () => {
    for (const w of ["批准", "自动拒绝", "重来"]) expect(APPROVAL_TIMEOUT_NOTE).toContain(w);
  });

  it("系统通知带上要执行什么, 太长截断", () => {
    const n = approvalNotification("remindctl update --uid 1AB56410 --notes '素材路径…'");
    expect(n.title).toBe("小鲶等你批准");
    expect(n.body).toContain("remindctl");
    expect(n.body).toContain("5 分钟");
    const long = approvalNotification("x".repeat(300));
    expect(long.body).toContain("…");
    expect(long.body.length).toBeLessThan(140);
    expect(approvalNotification("").body).toContain("有一步要你确认");
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
