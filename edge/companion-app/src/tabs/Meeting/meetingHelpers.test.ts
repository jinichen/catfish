import { describe, expect, it } from "vitest";

import type { WikiFileInfo } from "../../lib/tauri_wiki";
import {
  actionItemToTask, estimatePercent, mergeHotwords, minutesWikiBody, minutesWikiTitle, wikiHotwords,
} from "./meetingHelpers";
import { recorderNotices } from "./meetingHelpers";

function entry(p: Partial<WikiFileInfo>): WikiFileInfo {
  return {
    rel_path: "wiki/entities/x.md", kind: "entity", slug: "x", title: "x", subtype: null, tags: [],
    related: [], sources: [], aliases: [], size_bytes: 0, mtime: 0, ...p,
  } as WikiFileInfo;
}

describe("wikiHotwords", () => {
  it("取人名 / 项目 / 单位的标题和别名, 去重", () => {
    const files = [
      entry({ title: "中电福富", subtype: "org", aliases: ["中电福富信息科技有限公司", "福富"] }),
      entry({ title: "陈秀平", subtype: "person" }),
      entry({ title: "资质集采二期", subtype: "project", aliases: ["福富"] }),
      entry({ title: "CSMM-4 评估方法", subtype: "method" }), // 不是人/项目/单位
      entry({ title: "某概念", kind: "concept", subtype: "person" }),
      entry({ title: "待确认的人", subtype: "person", ontology_status: "pending" }),
    ];
    expect(wikiHotwords(files)).toEqual(["中电福富", "中电福富信息科技有限公司", "福富", "陈秀平", "资质集采二期"]);
  });

  it("太长的不要, 最多 100 个", () => {
    const long = entry({ title: "这是一个特别特别特别特别特别特别长的标题不像热词", subtype: "project" });
    expect(wikiHotwords([long])).toEqual([]);
    const many = Array.from({ length: 150 }, (_, i) => entry({ title: `人${i}`, subtype: "person" }));
    expect(wikiHotwords(many)).toHaveLength(100);
  });
});

describe("mergeHotwords", () => {
  it("保留已填的, 追加没有的", () => {
    expect(mergeHotwords("达华，福富", ["福富", "高新"])).toBe("达华 福富 高新");
    expect(mergeHotwords("", ["a"])).toBe("a");
  });
});

describe("actionItemToTask", () => {
  it("带来源和序号, 空截止日期不传", () => {
    expect(actionItemToTask("mtg_x", "周会", 2, { owner: "张三", task: "交材料", due: "2026-10-03" })).toEqual({
      title: "交材料", due_date_iso: "2026-10-03", body: "负责人: 张三\n来自会议「周会」的纪要",
      source: "meeting", source_id: "mtg_x#2",
    });
    expect(actionItemToTask("mtg_x", "周会", 0, { owner: "", task: "t", due: "" }).due_date_iso).toBeUndefined();
  });
});

describe("estimatePercent", () => {
  it("按 8 倍实时估, 封顶 95", () => {
    expect(estimatePercent(15, 240)).toBe(50);
    expect(estimatePercent(999, 240)).toBe(95);
    expect(estimatePercent(10, null)).toBeNull();
  });
});

describe("minutesWikiTitle", () => {
  const ok = (t: string) => /^[\p{L}\p{N}_\- ]+$/u.test(t) && new TextEncoder().encode(t.replace(/\s+/g, "-")).length <= 100;

  it("去掉条目名不允许的字符", () => {
    const t = minutesWikiTitle("资质集采·二期/周会: 10-01", "2026-10-01");
    expect(t).toBe("会议纪要 资质集采 二期 周会 10-01 2026-10-01");
    expect(ok(t)).toBe(true);
  });

  it("太长截短到 100 字节以内, 日期保留", () => {
    const t = minutesWikiTitle("很长".repeat(40), "2026-10-01");
    expect(ok(t)).toBe(true);
    expect(t.endsWith("2026-10-01")).toBe(true);
  });

  it("标题全是符号也能出一个合法名字", () => {
    expect(minutesWikiTitle("···///", "2026-10-01")).toBe("会议纪要 2026-10-01");
  });
});

describe("minutesWikiBody", () => {
  it("去掉首行 H1", () => {
    expect(minutesWikiBody("# 周会\n\n- 日期: x\n")).toBe("- 日期: x\n");
  });
});

describe("recorderNotices (11/06)", () => {
  it("丢一小段 / 换麦克风是淡色提示, 真出错时不叠", () => {
    expect(recorderNotices({ error: null, glitches: 0, notice: null })).toEqual([]);
    expect(recorderNotices({ error: null, glitches: 3, notice: null })[0]).toContain("有 3 次丢了一小段声音");
    expect(recorderNotices({ error: null, glitches: 0, notice: "系统换了录音设备" })).toEqual(["系统换了录音设备"]);
    expect(recorderNotices({ error: "录音设备出错: 设备不在了", glitches: 5, notice: "x" })).toEqual([]);
  });
});

// ── 11/11 线上会议 ──
import { clampLocalSpeakers, defaultSysKey, sysPickFor, systemTrackNotice, SYSTEM_SILENT_WARN_SECS } from "./meetingHelpers";
import type { SysApp, SysSources } from "../../lib/tauri_meeting";

const app = (p: Partial<SysApp>): SysApp => ({ name: "x", bundle_id: "", pids: [1], playing: false, meeting: false, ...p });
const srcOf = (apps: SysApp[], mode: SysSources["mode"] = "tap"): SysSources => ({ mode, apps, hint: "" });

describe("线上会议: 默认录哪个程序", () => {
  it("正在出声的会议软件优先, 其次开着的 (微信不算), 都没有录全部", () => {
    const wechat = app({ name: "微信", bundle_id: "com.tencent.xinWeChat", meeting: true });
    const tm = app({ name: "腾讯会议", bundle_id: "com.tencent.meeting", meeting: true });
    const aone = app({ name: "AONE", bundle_id: "com.alibaba.aone", meeting: true, playing: true });
    expect(defaultSysKey(srcOf([wechat, tm, aone]))).toBe("com.alibaba.aone");
    expect(defaultSysKey(srcOf([wechat, tm]))).toBe("com.tencent.meeting");
    expect(defaultSysKey(srcOf([wechat, app({ name: "Chrome", bundle_id: "com.google.Chrome" })]))).toBe("");
    expect(defaultSysKey(srcOf([tm], "loopback"))).toBe("");
  });

  it("选了程序按 bundle id 录 (进出会议室换了帮手进程也认得); 没 bundle id 才按进程号; 空 = 全部", () => {
    const s = srcOf([app({ name: "腾讯会议", bundle_id: "com.tencent.meeting", pids: [5, 6] }), app({ name: "进程", pids: [9] })]);
    expect(sysPickFor(s, "com.tencent.meeting")).toEqual({ name: "腾讯会议", bundle_id: "com.tencent.meeting", pids: [] });
    expect(sysPickFor(s, "name:进程")).toEqual({ name: "进程", bundle_id: null, pids: [9] });
    expect(sysPickFor(s, "")).toEqual({ name: "", bundle_id: null, pids: [] });
  });

  it("本地人数至少 1、至少给对方留 1 个", () => {
    expect(clampLocalSpeakers("", 4)).toBe(1);
    expect(clampLocalSpeakers("3", 4)).toBe(3);
    expect(clampLocalSpeakers("9", 4)).toBe(3);
    expect(clampLocalSpeakers("0", null)).toBe(1);
  });

  it("对方那边没声音: 30 秒内不吵; 出错时不重复提示", () => {
    const sys = { source: "腾讯会议", level: 0, seconds: 40, mode: "tap", error: null };
    expect(systemTrackNotice(sys, SYSTEM_SILENT_WARN_SECS - 1, "h")).toBeNull();
    expect(systemTrackNotice(sys, SYSTEM_SILENT_WARN_SECS, "去开权限")).toContain("去开权限");
    expect(systemTrackNotice({ ...sys, error: "坏了" }, 99, "h")).toBeNull();
  });
});
