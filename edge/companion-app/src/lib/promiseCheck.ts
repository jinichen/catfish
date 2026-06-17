/**
 * BL-TASK-ASSESS-2-CLIENT (5/15 鸿波"客户端没有评估这个任务的完成情况"):
 * promise-vs-reality 检测.
 *
 * # 场景
 *
 * Qwen 122B 间歇性嘴炮: assistant 输出文字"已生成 ~/.catfish/output/x.html"
 * 但 stream finish_reason=stop, cum_has_tool_call=false, 真没调过 write_file.
 * 用户在 Companion UI 看不出 agent 在嘴炮, 一直点"继续"也没用.
 *
 * # 实现
 *
 * 输入: assistant message content + gateway 给的 task_assessment metadata.
 * 输出: { is_promise_only, promised_paths }.
 *
 * 触发 is_promise_only=true 必须同时满足:
 *   1. 文字里有"承诺词" (已生成 / 完成 / 写入 / 创建 / 保存 / 输出 + 文件名扩展)
 *   2. cum_has_tool_call=false (这一轮真没调 tool)
 *   3. skill_guard_fired=true (用户原 task 是 skill 意图, 应该出文件)
 *   4. ever_called_catfish_run_skill_in_session=false (整 session 还没进过 skill 入口)
 *
 * 4 条全满足 = 嘴炮断言成立.
 *
 * # 不做什么
 *
 * - 不做 fs.exists() 真访问磁盘 — Companion (Tauri WebView) 走 IPC 访问文件
 *   会增加 latency, 且 PWA 场景没文件权限. UI 渲染时显示"模型说 X / 你确认存在
 *   再点已生成" 让用户判断, 比客户端瞎猜准.
 * - 不做 retry — 用户决定催不催, 不自动烧 token.
 */

import type { TaskAssessment } from "./chat";

/** 承诺词模式 — 两类:
 *
 * 1. **完成承诺** (模型说"我做完了"):
 *    已生成 / 完成了 / 已写入 / 已保存 / generated / done / saved ...
 *
 * 2. **"让我..." 暗示** (5/15 鸿波 'PPT 模板太长 agent stop "让我直接..."' bug):
 *    模型说"让我..."然后 stop 嘴炮, 不真调工具. 这种 plan-only stop 也算嘴炮.
 *    "让我直接 / 让我先 / 让我手动 / 让我用 / 让我想想 / 让我去 / 让我重新"
 *    Let me / I will / I'll
 */
// BL-PROMISE-CHECK-FIX (6/1): 加 "保存到 / 已经创建" 等口语化承诺词. 5/15 ship
// 时只覆盖 "已 X" 紧凑式, 实测 LLM 偶尔说 "保存到 ~/.catfish/xxx" / "已经创建"
// 等 — 5/15 时已有测试 case 期待抽路径但 PROMISE_PATTERN 没覆盖, 测试 fail.
//
// P3.5.23 (6/17 鸿波): 加 "我先[用做读写跑去执拿调把开]" — Qwen 122B Turn 1 嘴炮
// "我先用 execute_code 完整读取两份数据..." (60 chars stop, tool_turns=0) 真没匹配
// 现有 pattern (现有 "让我X"+"我现在"+"我立刻", 缺 "我先X" 这种 plan-only stop).
// 鸿波 15:33:59 真证: turn 1 0 tool 调用, LLM 只输出 plan-only 文字就 stop, UI 没显
// 嘴炮 badge → 鸿波误以为"路径不对". 真因 = PROMISE_PATTERN 漏 "我先X" 形态.
const PROMISE_PATTERN =
  /已生成|生成完毕|生成完成|完成了|已完成|写入完成|已写入|已保存|保存到|保存成|已创建|已经创建|创建完成|已输出|输出完成|done|generated|saved|created|让我[直先手用想去重再立]|让我[直先手用想去重再立].{0,2}[基用执做继读写改试想]|Let me\s|I'll\s|I will\s|我现在|我立刻|我先[用做读写跑去执拿调把开看试]/i;

/** 扫文件路径 (中文/英文目录, 含扩展名) */
const PATH_PATTERN = /(~\/[\w./\-_一-鿿]+|\/Users\/[\w./\-_一-鿿]+|[A-Za-z]:\\[\w.\\\-_一-鿿]+|\.\/[\w./\-_一-鿿]+|[\w\-_一-鿿]+\.(?:html?|docx?|xlsx?|pptx?|pdf|md|txt|csv|json|py|js|ts|jsx|tsx))/gi;

export interface PromiseCheckResult {
  is_promise_only: boolean;
  promised_paths: string[];
}

export function checkPromiseOnly(
  assistantContent: string,
  assessment: TaskAssessment | undefined,
): PromiseCheckResult {
  // 没拿到 gateway metadata — 不能断言, 默认不报警
  if (!assessment) {
    return { is_promise_only: false, promised_paths: [] };
  }

  // 条件 2: 真调过 tool — 不是嘴炮
  if (assessment.cum_has_tool_call || assessment.tool_call_count > 0) {
    return { is_promise_only: false, promised_paths: [] };
  }

  // 条件 3: 不是 skill 意图 — 普通问答, 不强求出文件
  if (!assessment.skill_guard_fired) {
    return { is_promise_only: false, promised_paths: [] };
  }

  // 条件 4 (5/15 修): 不再"进过 skill 入口就不报警". 接力步骤里 stop "模板
  // 太长让我直接..." 也是嘴炮 — agent 已经 read_file 拿了模板, 卡在 write_file
  // 不肯发. 真正不该报警的只有"真做完, 文件存在"那种情形 — 但客户端不访问
  // 磁盘 (Tauri IPC 慢), 让用户自己看 ⚠ 文案判断比客户端瞎猜准.
  // 改成: 不分进没进 skill 入口, 都看当前轮是否 stop+无 tool+有承诺词.

  // 条件 1: 文字含承诺词 OR "让我..." 暗示词
  if (!PROMISE_PATTERN.test(assistantContent)) {
    return { is_promise_only: false, promised_paths: [] };
  }

  // 抽文字里宣称的路径 (给 UI 显示用)
  const promised_paths = Array.from(
    new Set(
      Array.from(assistantContent.matchAll(PATH_PATTERN)).map((m) => m[0]),
    ),
  );

  return {
    is_promise_only: true,
    promised_paths,
  };
}

/** 仅给 UI 用的简化文本: "模型说生成了 X, 但没真调工具" */
export function describePromiseCheck(check: PromiseCheckResult): string {
  if (!check.is_promise_only) return "";
  if (check.promised_paths.length === 0) {
    return "⚠ 模型说做完了, 但这一轮没真调任何工具 — 可能是嘴炮";
  }
  if (check.promised_paths.length === 1) {
    return `⚠ 模型说生成了 ${check.promised_paths[0]}, 但没真调工具 — 检查文件是否存在`;
  }
  return `⚠ 模型说生成了 ${check.promised_paths.length} 个文件, 但没真调工具 — 嘴炮可能性大`;
}
