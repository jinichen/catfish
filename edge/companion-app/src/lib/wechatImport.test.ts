import { describe, expect, it } from "vitest";

import { formatFileAttachment } from "./chatWire";
import {
  choiceProblem,
  formatWeChatAttachment,
  initialChoice,
  stageSummary,
  toAttachment,
  type WeChatImportResult,
  type WeChatStage,
} from "./wechatImport";

function stage(overrides: Partial<WeChatStage["inspect"]> = {}, extra: Partial<WeChatStage> = {}): WeChatStage {
  return {
    stageId: "a".repeat(32),
    filename: "聊天记录_20260923.zip",
    sizeBytes: 2048,
    needsConsent: true,
    pickerModel: "catfish-private-main",
    inspect: {
      message_count: 44,
      start: "2026-09-14T15:06:00+08:00",
      end: "2026-09-20T16:42:00+08:00",
      senders: [{ name: "测试甲", count: 20 }, { name: "我自己", count: 3 }],
      attachments: { in_archive: 10, referenced: 12, missing: 2 },
      already_imported_group_id: null,
      matched_group_id: null,
      candidates: [],
      suggested_name: "测试甲、测试乙等 8 人",
      known_self_name: null,
      ...overrides,
    },
    ...extra,
  };
}

const result: WeChatImportResult = {
  groupId: "7767781d2216",
  name: "年审群",
  selfName: "我自己",
  alreadyImported: false,
  transcript: "[2026-09-14 15:06] 测试甲: 材料发一下",
  truncated: false,
  renderedCount: 44,
  messageCount: 44,
};

describe("确认框默认值", () => {
  it("认不出群时新建, 群名用建议名", () => {
    const c = initialChoice(stage());
    expect(c.groupId).toBeNull();
    expect(c.newGroupName).toBe("测试甲、测试乙等 8 人");
    expect(c.selfName).toBeUndefined();
    expect(c.consent).toBe(false);
  });

  it("导入过的优先于认出来的, 记过的「我」自动带上", () => {
    const c = initialChoice(stage({
      already_imported_group_id: "aaa111", matched_group_id: "bbb222", known_self_name: "我自己",
    }));
    expect(c.groupId).toBe("aaa111");
    expect(c.selfName).toBe("我自己");
  });
});

describe("能不能点导入", () => {
  it("首次必须勾同意", () => {
    const s = stage();
    expect(choiceProblem(s, initialChoice(s))).toBe("请先勾选同意");
    expect(choiceProblem(s, { ...initialChoice(s), consent: true })).toBeNull();
  });

  it("已授权过就不用勾", () => {
    const s = stage({}, { needsConsent: false });
    expect(choiceProblem(s, initialChoice(s))).toBeNull();
  });

  it("新群名不能是空白; 没选模型不行", () => {
    const s = stage({}, { needsConsent: false });
    expect(choiceProblem(s, { ...initialChoice(s), newGroupName: "  " })).toBe("请填写群名");
    expect(choiceProblem({ ...s, pickerModel: null }, initialChoice(s))).toMatch(/Picker/);
  });
});

describe("摘要与附件", () => {
  it("一行摘要写清条数 / 时间 / 人数 / 缺的附件", () => {
    expect(stageSummary(stage().inspect)).toBe(
      "44 条 · 2026-09-14 ~ 2026-09-20 · 2 人 · 附件 12 个 (2 个未随导出)",
    );
    expect(stageSummary(stage({ end: "2026-09-14T18:00:00+08:00",
      attachments: { in_archive: 0, referenced: 0, missing: 0 } }).inspect))
      .toBe("44 条 · 2026-09-14 · 2 人");
  });

  it("附件带上 session_id, 模型才能接着用工具查", () => {
    const att = toAttachment(stage(), result);
    expect(att.fileKind).toBe("wechat");
    expect(att.name).toBe("年审群");
    expect(att.meta?.session_id).toBe("7767781d2216");
    expect(att.previewText).toContain("材料发一下");
  });

  it("全文在就直接给, 并提示缺的附件", () => {
    const text = formatWeChatAttachment(toAttachment(stage(), result));
    expect(text).toContain("=== 微信聊天记录: 年审群 (44 条 · 2026-09-14 ~ 2026-09-20) ===");
    expect(text).toContain("[2026-09-14 15:06] 测试甲: 材料发一下");
    expect(text).toContain("有 2 个附件");
    expect(text).not.toContain("只放了前");
  });

  it("截断了就说清楚, 并告诉模型怎么查剩下的", () => {
    const text = formatWeChatAttachment(toAttachment(stage(), { ...result, truncated: true, renderedCount: 300 }));
    expect(text).toContain("只放了前 300 条");
    expect(text).toContain('catfish_wechat_history (session_id="7767781d2216"');
  });

  it("历史会话恢复时没有全文, 只给查询指引", () => {
    const att = toAttachment(stage(), result);
    const text = formatWeChatAttachment({ ...att, previewText: undefined });
    expect(text).toContain("没有带全文");
    expect(text).toContain("catfish_wechat_history");
  });

  it("chatWire 对微信附件走专用格式, 不给 execute_code 的文件路径提示", () => {
    const text = formatFileAttachment(toAttachment(stage(), result));
    expect(text).toContain("微信聊天记录");
    expect(text).not.toContain("execute_code");
    expect(text).not.toContain("[完整文件:");
  });
});
