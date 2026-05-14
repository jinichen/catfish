/** RecMode store 单测 (BL-LEARN-RECMODE Day 2 #64).
 *
 *   cd edge/companion-app
 *   npx tsx src/store/recmode.test.ts
 */

const _store = new Map<string, string>();
(globalThis as unknown as { localStorage: Storage }).localStorage = {
  length: 0,
  getItem: (k: string) => _store.get(k) ?? null,
  setItem: (k: string, v: string) => { _store.set(k, v); },
  removeItem: (k: string) => { _store.delete(k); },
  clear: () => _store.clear(),
  key: () => null,
};

import {
  useRecModeStore,
  formatRecordingElapsed,
  isValidSkillName,
  isValidSkillTitle,
  RECMODE_EXAMPLES,
  classifyRecModeError,
} from "./recmode";

let pass = 0;
let fail = 0;

function check(name: string, ok: boolean, info?: string): void {
  if (ok) {
    pass++;
    console.log(`  ✓ ${name}`);
  } else {
    fail++;
    console.error(`  ✗ ${name}${info ? ` — ${info}` : ""}`);
  }
}

// ─── 初始 state ────────────────────────────────────────────

console.log("[init]");
useRecModeStore.getState().reset();
check("初始 state idle", useRecModeStore.getState().state === "idle");
check("初始 sessionId null", useRecModeStore.getState().sessionId === null);
check("初始 setup namespace personal", useRecModeStore.getState().setup.namespace === "personal");

// ─── 状态机 转换 ───────────────────────────────────────────

console.log("[state machine]");

useRecModeStore.getState().openSetup();
check("openSetup → state=setup", useRecModeStore.getState().state === "setup");

useRecModeStore.getState().setSetup({ name: "test_skill", description: "测试" });
const s = useRecModeStore.getState().setup;
check("setSetup 部分更新 name", s.name === "test_skill");
check("setSetup 部分更新 description", s.description === "测试");
check("setSetup 没动 namespace", s.namespace === "personal");

useRecModeStore.getState().startRecording("rec_abc123");
const r = useRecModeStore.getState();
check("startRecording → state=recording", r.state === "recording");
check("startRecording 设 sessionId", r.sessionId === "rec_abc123");
check("startRecording 设 startedAt", r.startedAt !== null && r.startedAt > 0);
check("startRecording 设 isRecordingAudio=true", r.isRecordingAudio === true);

useRecModeStore.getState().stopRecording();
check("stopRecording → state=analyzing", useRecModeStore.getState().state === "analyzing");

useRecModeStore.getState().showPreview({
  skill_name: "test_skill",
  namespace: "personal",
  skill_dir: "/tmp/skills/personal/test_skill",
  steps_count: 5,
  confidence: 0.85,
  questions_for_user: ["参数 days 默认值?"],
});
const p = useRecModeStore.getState();
check("showPreview → state=preview", p.state === "preview");
check("showPreview 拿 confidence", p.preview?.confidence === 0.85);
check("showPreview 拿 questions", p.preview?.questions_for_user.length === 1);

useRecModeStore.getState().reset();
check("reset → state=idle", useRecModeStore.getState().state === "idle");
check("reset 清 sessionId", useRecModeStore.getState().sessionId === null);
check("reset 清 setup", useRecModeStore.getState().setup.name === "");

// ─── error 状态 ────────────────────────────────────────────

console.log("[error state]");

useRecModeStore.getState().startRecording("rec_err");
useRecModeStore.getState().setError("CDP ws 连接失败 — Catfish Chrome 没起");
const e = useRecModeStore.getState();
check("setError → state=error", e.state === "error");
check("setError 设 errorMessage", e.errorMessage?.includes("CDP ws") === true);

useRecModeStore.getState().reset();

// ─── helper functions ─────────────────────────────────────

console.log("[helpers]");

check("formatElapsed null → 0:00", formatRecordingElapsed(null) === "0:00");
check("formatElapsed 0s", formatRecordingElapsed(Math.floor(Date.now() / 1000)) === "0:00");
const tenSecAgo = Math.floor(Date.now() / 1000) - 10;
check("formatElapsed 10s → 0:10", formatRecordingElapsed(tenSecAgo) === "0:10");
const twoMinAgo = Math.floor(Date.now() / 1000) - 130;
check("formatElapsed 2min10s → 2:10", formatRecordingElapsed(twoMinAgo) === "2:10");

check("isValidSkillName eis_qual_check", isValidSkillName("eis_qual_check") === true);
check("isValidSkillName 拒大写", isValidSkillName("EisQualCheck") === false);
check("isValidSkillName 拒数字开头", isValidSkillName("1eis") === false);
check("isValidSkillName 拒短", isValidSkillName("ab") === false);
check("isValidSkillName 拒中文", isValidSkillName("企业资质") === false);
check("isValidSkillName 拒空格", isValidSkillName("eis check") === false);
check("isValidSkillName 接 N 段下划线", isValidSkillName("a_b_c_d_e") === true);

// ─── isValidSkillTitle (5/14 鸿波 UI 反馈后加, 人话标题校验) ──────

console.log("[isValidSkillTitle 人话标题]");

check("isValidSkillTitle 中文 OK", isValidSkillTitle("检查 EIS 资质过期") === true);
check("isValidSkillTitle 英文 OK", isValidSkillTitle("EIS qual check") === true);
check("isValidSkillTitle 中英混 OK", isValidSkillTitle("EIS 资质 check") === true);
check("isValidSkillTitle 空格 OK", isValidSkillTitle("a b c") === true);
check("isValidSkillTitle 拒空", isValidSkillTitle("") === false);
check("isValidSkillTitle 拒纯空白", isValidSkillTitle("   ") === false);
check("isValidSkillTitle 拒太短 (2 字)", isValidSkillTitle("ab") === false);
check("isValidSkillTitle 接 3 字", isValidSkillTitle("abc") === true);
check("isValidSkillTitle 接 100 字", isValidSkillTitle("a".repeat(100)) === true);
check("isValidSkillTitle 拒 101 字", isValidSkillTitle("a".repeat(101)) === false);

// ─── RECMODE_EXAMPLES (示例 prefill) ─────────────────────

console.log("[RECMODE_EXAMPLES]");
check("有 ≥ 3 个示例", RECMODE_EXAMPLES.length >= 3);
check("每个示例 title 都 valid", RECMODE_EXAMPLES.every((e) => isValidSkillTitle(e.title)));
check("每个示例 description 非空", RECMODE_EXAMPLES.every((e) => e.description.length > 0));
check("EIS 资质 示例在", RECMODE_EXAMPLES.some((e) => e.title.includes("EIS")));

// ─── classifyRecModeError (5/14 G 错分类) ───────────────

console.log("[classifyRecModeError]");
check("CDP/ws/9222/chrome → cdp_unavailable",
  classifyRecModeError("ws connection refused on 9222") === "cdp_unavailable");
check("Catfish Chrome 没起 → cdp_unavailable",
  classifyRecModeError("CDP listener 连不上 Catfish Chrome") === "cdp_unavailable");
check("whisper / ffmpeg → whisper_failed",
  classifyRecModeError("whisper.cpp 跑挂") === "whisper_failed");
check("speech_start_recording 失败 → whisper_failed",
  classifyRecModeError("speech_start_recording 失败") === "whisper_failed");
check("timeout → aggregator_timeout",
  classifyRecModeError("LLM 综合超时 (300s)") === "aggregator_timeout");
check("超时 → aggregator_timeout",
  classifyRecModeError("aggregator 超时啦") === "aggregator_timeout");
check("JSON parse → llm_parse_failed",
  classifyRecModeError("LLM 输出 JSON parse 失败") === "llm_parse_failed");
check("找不到 → llm_parse_failed",
  classifyRecModeError("LLM 输出找不到 JSON") === "llm_parse_failed");
check("network → network",
  classifyRecModeError("network connection error") === "network");
check("不可达 → network",
  classifyRecModeError("gateway 不可达") === "network");
check("未知 → unknown",
  classifyRecModeError("奇怪的 wat") === "unknown");

// 跑 setError 看自动分类生效
useRecModeStore.getState().setError("ws connection failed");
check("setError 自动分类 cdp_unavailable",
  useRecModeStore.getState().errorCategory === "cdp_unavailable");
useRecModeStore.getState().reset();

// ─── 报告 ─────────────────────────────────────────────────

console.log(`\n[recmode store 单测] pass=${pass} fail=${fail}`);
if (fail > 0) {
  process.exit(1);
}
