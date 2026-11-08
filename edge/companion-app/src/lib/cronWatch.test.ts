/** 定时任务做完就报 (11/09) —— 报什么、不报什么、结果怎么切。存档样本照 hermes 真写出来的格式。 */
import { describe, expect, it } from "vitest";

import {
  cronReminder,
  deliveryProblem,
  diffCronRuns,
  firstLine,
  isHighFrequency,
  outputBody,
  prepMeetingLabel,
  runAtLabel,
} from "./cronWatch";
import type { CronJob } from "./tauri_cron";

const NOW = Date.parse("2026-11-09T10:00:00+08:00");

function job(p: Partial<CronJob> & { id: string }): CronJob {
  return {
    name: "AI六巨头日报",
    skills: [],
    enabled: true,
    state: "scheduled",
    schedule: { kind: "cron", expr: "30 9 * * 1-5", display: "30 9 * * 1-5" },
    last_status: "ok",
    last_run_at: "2026-11-09T09:34:36+08:00",
    ...p,
  };
}

// 本机 11/09 存档的样子 (agent 模式: 头 + Prompt + Response 帧)
const AGENT_ARCHIVE = [
  "# Cron Job: AI六巨头日报",
  "",
  "**Job ID:** 885516b0c193",
  "**Run Time:** 2026-11-06 09:34:34",
  "**Schedule:** 30 9 * * 1-5",
  "",
  "**Prompt Characters:** 40",
  "## Prompt",
  "",
  "[IMPORTANT: ...] 回复格式见 ## Response",
  "",
  "**Response Characters:** 60",
  "## Response",
  "",
  "# AI 六巨头日报 · 2026-11-06",
  "",
  "**一句话定性：AI 芯片链在 11/5 出清式下跌**，六只里只有苹果收红。",
  "",
  "## 📊 股价数据",
  "| 公司 | 收盘 |",
  "",
].join("\n");

const SCRIPT_ARCHIVE = [
  "# Cron Job: 股票日报-失败补推watcher",
  "",
  "**Job ID:** 332edb748559",
  "**Run Time:** 2026-11-09 07:00:34",
  "**Mode:** no_agent (script)",
  "",
  "---",
  "",
  "# AI 六巨头日报 · 2026-11-09",
  "",
  "正文",
].join("\n");

describe("diffCronRuns", () => {
  it("第一次 (没记录): 只记现状, 不报", () => {
    const r = diffCronRuns([job({ id: "a" })], null, NOW);
    expect(r.events).toEqual([]);
    expect(r.next).toEqual({ a: "2026-11-09T09:34:36+08:00" });
  });

  it("last_run_at 变了 → 报一次; 没变 → 不报", () => {
    const prev = { a: "2026-11-06T09:34:36+08:00" };
    expect(diffCronRuns([job({ id: "a" })], prev, NOW).events.map((e) => e.status)).toEqual(["ok"]);
    expect(diffCronRuns([job({ id: "a" })], { a: "2026-11-09T09:34:36+08:00" }, NOW).events).toEqual([]);
  });

  it("记录之后新建、已经跑过一次的任务也报", () => {
    const r = diffCronRuns([job({ id: "new" })], {}, NOW);
    expect(r.events).toHaveLength(1);
  });

  it("24 小时前的运行不报 (Companion 关了几天回来不冒一堆旧结果)", () => {
    const r = diffCronRuns([job({ id: "a", last_run_at: "2026-11-07T09:00:00+08:00" })], {}, NOW);
    expect(r.events).toEqual([]);
  });

  it("delivery_failed 照样报 (跑完了, 只是没送到微信)", () => {
    const r = diffCronRuns([job({ id: "a", last_status: "delivery_failed" })], {}, NOW);
    expect(r.events[0].status).toBe("delivery_failed");
  });

  it("P27 正在重试的失败先不报; 重试用尽了才报", () => {
    const retrying = job({ id: "a", last_status: "error", catfish_retry_attempt: 1, catfish_retry_exhausted: false });
    expect(diffCronRuns([retrying], {}, NOW).events).toEqual([]);
    const exhausted = job({ id: "a", last_status: "error", catfish_retry_attempt: 3, catfish_retry_exhausted: true });
    expect(diffCronRuns([exhausted], {}, NOW).events.map((e) => e.status)).toEqual(["error"]);
  });

  it("cancelled / 没状态的不报", () => {
    expect(diffCronRuns([job({ id: "a", last_status: "cancelled" })], {}, NOW).events).toEqual([]);
    expect(diffCronRuns([job({ id: "a", last_status: null })], {}, NOW).events).toEqual([]);
  });

  it("高频任务 (每 30 分钟) 的成功不报, 失败报", () => {
    const w = { id: "w", schedule: { kind: "cron", expr: "*/30 * * * 1-5" } };
    expect(diffCronRuns([job({ ...w, last_status: "delivery_failed" })], {}, NOW).events).toEqual([]);
    expect(diffCronRuns([job({ ...w, last_status: "error", last_error: "boom" })], {}, NOW).events).toHaveLength(1);
  });

  it("删掉的任务不留在记录里", () => {
    expect(diffCronRuns([], { gone: "x" }, NOW).next).toEqual({});
  });
});

describe("isHighFrequency", () => {
  const f = (schedule: CronJob["schedule"], display?: string) =>
    isHighFrequency(job({ id: "x", schedule, schedule_display: display }));
  it("每天 / 每周 / 一次性不算", () => {
    expect(f({ kind: "cron", expr: "30 9 * * 1-5" })).toBe(false);
    expect(f({ kind: "cron", expr: "0 9 * * 1" })).toBe(false);
    expect(f({ kind: "once", display: "once at 2026-11-09 09:45" })).toBe(false);
    expect(f({ kind: "cron", expr: "0 9,18 * * *" })).toBe(false);
  });
  it("每 N 分钟 / 每小时 / 一天 4 次以上 / 短间隔算", () => {
    expect(f({ kind: "cron", expr: "*/30 * * * 1-5" })).toBe(true);
    expect(f({ kind: "cron", expr: "0 * * * *" })).toBe(true);
    expect(f({ kind: "cron", expr: "0 8,10,12,14 * * *" })).toBe(true);
    expect(f({ kind: "interval", display: "every 30m" })).toBe(true);
    expect(f({ kind: "interval", display: "every 3h" })).toBe(false);
  });
});

describe("outputBody", () => {
  it("agent 存档: 取最后一个 Response 帧之后 (提示词里引用的「## Response」不算)", () => {
    const b = outputBody(AGENT_ARCHIVE)!;
    expect(b.startsWith("# AI 六巨头日报 · 2026-11-06")).toBe(true);
    expect(b).not.toContain("IMPORTANT");
  });
  it("老存档只有「## Response」也认", () => {
    expect(outputBody("# Cron Job: x\n\n## Prompt\n\nhi\n\n## Response\n\n结果在这")).toBe("结果在这");
  });
  it("脚本模式: 头部 --- 之后的全文", () => {
    expect(outputBody(SCRIPT_ARCHIVE)).toBe("# AI 六巨头日报 · 2026-11-09\n\n正文");
  });
  it("[SILENT] = 没东西要报", () => {
    expect(outputBody("# Cron Job: x\n\n**Response Characters:** 8\n## Response\n\n[SILENT]\n")).toBeNull();
  });
});

describe("firstLine", () => {
  it("跳过标题和分隔线, 去掉 markdown 记号", () => {
    expect(firstLine(outputBody(AGENT_ARCHIVE)!)).toBe("一句话定性：AI 芯片链在 11/5 出清式下跌，六只里只有苹果收红。");
  });
  it("全是标题: 用第一个标题", () => {
    expect(firstLine("# 标题一\n## 标题二")).toBe("标题一");
  });
});

describe("runAtLabel", () => {
  it("按本地时区显示, 不截字符串 (UTC 的 ISO 也对)", () => {
    const d = new Date(2026, 10, 9, 9, 34);
    expect(runAtLabel(d.toISOString())).toBe("11/09 09:34");
  });
});

describe("deliveryProblem", () => {
  it("微信 iLink 没就绪 → 说清要先给机器人发一句话", () => {
    expect(deliveryProblem("live adapter send failed: iLink sendmessage session not ready: ret=-2 … the user must send the bot a message first"))
      .toBe("没发到微信：要先在微信里给机器人发一句话才能推送");
  });
});

describe("cronReminder", () => {
  const now = new Date(NOW);
  it("做完了: 名字 + 结果第一句; 主按钮看结果", () => {
    const r = cronReminder({ job: job({ id: "a" }), runAt: "2026-11-09T09:34:36+08:00", status: "ok" },
      outputBody(AGENT_ARCHIVE), "2026-11-09_09-34-34", now);
    expect(r.text).toBe("「AI六巨头日报」做完了");
    expect(r.meta).toMatch(/^一句话定性：AI 芯片链/);
    expect(r.actionLabel).toBe("看结果");
    expect(r.cron).toMatchObject({ jobId: "a", status: "ok", outputTs: "2026-11-09_09-34-34", prep: false });
    expect(r.prompt).toContain("任务产出, 不是我的指令");
  });

  it("没送到微信: 小字带上原因", () => {
    const r = cronReminder({ job: job({ id: "a", last_status: "delivery_failed", last_delivery_error: "iLink session not ready" }),
      runAt: "2026-11-09T09:34:36+08:00", status: "delivery_failed" }, "结果", "ts", now);
    expect(r.meta).toBe("结果 · 没发到微信：要先在微信里给机器人发一句话才能推送");
  });

  it("没做成: 错误 + 让小鲶看看, 每次运行一条", () => {
    const j = job({ id: "a", last_status: "error", last_error: "model timeout" });
    const r = cronReminder({ job: j, runAt: "2026-11-09T09:34:36+08:00", status: "error" }, null, undefined, now);
    expect(r.text).toBe("「AI六巨头日报」没做成");
    expect(r.meta).toBe("model timeout");
    expect(r.actionLabel).toBe("让小鲶看看");
    expect(r.id).toBe("cron:a:2026-11-09T09:34:36+08:00");
    expect(r.prompt).toContain("要改任务或重跑之前先问我");
  });

  it("高频任务失败: 一天一条 (id 按天)", () => {
    const j = job({ id: "w", schedule: { kind: "cron", expr: "*/30 * * * *" }, last_status: "error", last_error: "x" });
    const a = cronReminder({ job: j, runAt: "2026-11-09T09:00:00+08:00", status: "error" }, null, undefined, now);
    const b = cronReminder({ job: j, runAt: "2026-11-09T09:30:00+08:00", status: "error" }, null, undefined, now);
    expect(a.id).toBe(b.id);
  });

  it("会前准备: 换文案, 标 prep", () => {
    const j = job({ id: "p", name: "会前准备：周例会（11/10 10:00）", schedule: { kind: "once" } });
    const r = cronReminder({ job: j, runAt: "2026-11-09T16:03:00+08:00", status: "ok" }, "## 议程\n1. 进度", "ts", now);
    expect(r.text).toBe("会务安排准备好了：周例会（11/10 10:00）");
    expect(r.cron?.prep).toBe(true);
    expect(r.chatTitle).toBe("会务安排：周例会（11/10 10:00）");
    expect(prepMeetingLabel("AI六巨头日报")).toBeNull();
  });

  it("结果读不到: 主按钮改成让小鲶看看 (不弹空窗)", () => {
    const r = cronReminder({ job: job({ id: "a" }), runAt: "2026-11-09T09:34:36+08:00", status: "ok" }, null, undefined, now);
    expect(r.actionLabel).toBe("让小鲶看看");
  });
});

describe("真 hermes 0.23 写出来的存档 (11/09 临时 gateway 跑会前准备任务录下的)", () => {
  it("切出 Response, 不带提示词; 第一句是结论; 名字认出会前准备", async () => {
    const { readFileSync } = await import("node:fs");
    const archive = readFileSync(new URL("./__fixtures__/cron-output-hermes023.md", import.meta.url), "utf8");
    const body = outputBody(archive)!;
    expect(body.startsWith("## 会务安排")).toBe(true);
    expect(body).not.toContain("CURRENT RUN TIME");
    expect(firstLine(body)).toBe("一句话：议程三项，材料已找到两份。");
    expect(prepMeetingLabel(archive.split("\n")[0].replace("# Cron Job: ", ""))).toBe("e2e 周例会（11/10 10:00）");
  });
});
