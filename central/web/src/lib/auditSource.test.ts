import { describe, expect, it } from "vitest";

import { auditSourceLabel } from "./auditSource";

describe("配额日志「来源」(11/18 鸿波「这么多插件说明, 看不出来是什么插件」)", () => {
  it("学习各功能 / 会中问小鲶 / 对话卡片都翻成中文", () => {
    expect(auditSourceLabel("plugin:toolbridge-learning", "a@b.cn")).toBe("学习 · 摸底 / 讲解");
    expect(auditSourceLabel("plugin:toolbridge-learning-drill", "a@b.cn")).toBe("学习 · 答辩陪练");
    expect(auditSourceLabel("plugin:toolbridge-learning-lang", "a@b.cn")).toBe("学习 · 外语");
    expect(auditSourceLabel("plugin:toolbridge-meeting-live", "a@b.cn")).toBe("会议 · 会中问小鲶 / 提示");
    expect(auditSourceLabel("plugin:toolbridge-card", "a@b.cn")).toBe("对话 · 卡片");
    expect(auditSourceLabel("plugin:memory-wiki-ground", "client:hermes-cli")).toBe("后台 · 知识卡核对");
  });

  it("没登记的新来源按前缀给大类, 不原样甩英文", () => {
    expect(auditSourceLabel("plugin:toolbridge-new-thing", "a@b.cn")).toBe("小鲶后台 · new-thing");
    expect(auditSourceLabel("plugin:memory-foo", "a@b.cn")).toBe("后台 · 记忆 (foo)");
    expect(auditSourceLabel("companion-bar", "a@b.cn")).toBe("Companion · bar");
    expect(auditSourceLabel("something-else", "a@b.cn")).toBe("something-else");
  });
});
