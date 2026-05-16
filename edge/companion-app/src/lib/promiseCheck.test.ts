/**
 * BL-TASK-ASSESS-2-CLIENT 单测 — promise-vs-reality 检测.
 *
 * 跑法: cd edge/companion-app && pnpm test promiseCheck
 */

import { describe, expect, it } from "vitest";

import type { TaskAssessment } from "./chat";
import { checkPromiseOnly, describePromiseCheck } from "./promiseCheck";

const fullPromiseScenario: TaskAssessment = {
  object: "task_assessment",
  model: "catfish-private-main",
  finish_reason: "stop",
  tool_call_count: 0,
  content_chars: 200,
  cum_has_tool_call: false,
  skill_guard_fired: true,
  ever_called_catfish_run_skill_in_session: false,
};

describe("checkPromiseOnly — 4 条边界全满足", () => {
  it("文字含承诺词 + 没调 tool + skill 意图 + 没进过 skill → 嘴炮", () => {
    const content = "好的, 我已生成 ~/.catfish/output/资质分析_杂志风.html, 双击在浏览器打开看效果.";
    const r = checkPromiseOnly(content, fullPromiseScenario);
    expect(r.is_promise_only).toBe(true);
    expect(r.promised_paths).toContain("~/.catfish/output/资质分析_杂志风.html");
  });

  it("中文承诺词 '完成了' 命中", () => {
    const content = "完成了, 文件在 ./output/foo.docx";
    const r = checkPromiseOnly(content, fullPromiseScenario);
    expect(r.is_promise_only).toBe(true);
  });

  it("英文承诺词 'generated' 命中", () => {
    const content = "Generated report.pdf for you.";
    const r = checkPromiseOnly(content, fullPromiseScenario);
    expect(r.is_promise_only).toBe(true);
  });
});

describe("checkPromiseOnly — 任一条不满足都不报警", () => {
  it("条件 2 不满足: 真调过 tool → 不是嘴炮", () => {
    const r = checkPromiseOnly("已生成 x.html", {
      ...fullPromiseScenario,
      cum_has_tool_call: true,
      tool_call_count: 3,
    });
    expect(r.is_promise_only).toBe(false);
  });

  it("条件 3 不满足: 不是 skill 意图 → 普通问答, 不强求出文件", () => {
    const r = checkPromiseOnly("已生成讲解", {
      ...fullPromiseScenario,
      skill_guard_fired: false,
    });
    expect(r.is_promise_only).toBe(false);
  });

  it("接力步骤里 stop+嘴炮 仍报警 (5/15 修正: 不再因 ever_called_skill 屏蔽)", () => {
    // 接力步骤里, agent 已经 read_file 但卡在 write_file. 这种"模板太长让我..."
    // 也是嘴炮, 必须报警让用户看到.
    const r = checkPromiseOnly("模板太长, 让我直接基于内存生成简化版", {
      ...fullPromiseScenario,
      ever_called_catfish_run_skill_in_session: true,
    });
    expect(r.is_promise_only).toBe(true);
  });

  it("'让我...' 暗示词独立触发嘴炮 (即便没'已生成'承诺)", () => {
    const r = checkPromiseOnly("让我手动改一下试试", fullPromiseScenario);
    expect(r.is_promise_only).toBe(true);
  });

  it("条件 1 不满足: 没承诺词 → 不报警", () => {
    const r = checkPromiseOnly("我需要更多信息", fullPromiseScenario);
    expect(r.is_promise_only).toBe(false);
  });
});

describe("checkPromiseOnly — 边界", () => {
  it("assessment=undefined → 不能断言, 默认不报警", () => {
    const r = checkPromiseOnly("已生成", undefined);
    expect(r.is_promise_only).toBe(false);
    expect(r.promised_paths).toEqual([]);
  });

  it("空 content + 满足条件 → 不报警 (没承诺词)", () => {
    const r = checkPromiseOnly("", fullPromiseScenario);
    expect(r.is_promise_only).toBe(false);
  });
});

describe("路径抽取", () => {
  it("Unix 风格 + 中文路径", () => {
    const content = "保存到 ~/.catfish/output/资质分析_2026.html 和 /Users/chen/foo.docx";
    const r = checkPromiseOnly(content, fullPromiseScenario);
    expect(r.promised_paths).toContain("~/.catfish/output/资质分析_2026.html");
    expect(r.promised_paths).toContain("/Users/chen/foo.docx");
  });

  it("Windows 风格路径", () => {
    const content = "已生成 C:\\Users\\chen\\report.pptx";
    const r = checkPromiseOnly(content, fullPromiseScenario);
    expect(r.promised_paths.some((p) => p.includes("report.pptx"))).toBe(true);
  });

  it("纯文件名 (无完整路径) 也抓", () => {
    const content = "已经创建 q3-report.xlsx";
    const r = checkPromiseOnly(content, fullPromiseScenario);
    expect(r.promised_paths).toContain("q3-report.xlsx");
  });

  it("路径去重", () => {
    const content = "保存到 foo.html, foo.html 已生成";
    const r = checkPromiseOnly(content, fullPromiseScenario);
    expect(r.promised_paths.filter((p) => p === "foo.html").length).toBe(1);
  });
});

describe("describePromiseCheck — UI 文案", () => {
  it("不是嘴炮 → 返空串", () => {
    expect(describePromiseCheck({ is_promise_only: false, promised_paths: [] })).toBe("");
  });

  it("是嘴炮但没抽出路径", () => {
    const s = describePromiseCheck({ is_promise_only: true, promised_paths: [] });
    expect(s).toContain("嘴炮");
  });

  it("单路径文案", () => {
    const s = describePromiseCheck({
      is_promise_only: true,
      promised_paths: ["~/.catfish/output/x.html"],
    });
    expect(s).toContain("~/.catfish/output/x.html");
    expect(s).toContain("检查文件");
  });

  it("多路径文案", () => {
    const s = describePromiseCheck({
      is_promise_only: true,
      promised_paths: ["a.html", "b.docx", "c.pdf"],
    });
    expect(s).toContain("3 个文件");
  });
});
