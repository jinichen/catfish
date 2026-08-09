/**
 * hermes 活动文案的中文化 (8/9 加)。
 *
 * # 为什么需要
 *
 * 进度行的内容来自 hermes 的 `get_activity_summary()`, 而 hermes 是英文上游 ——
 * `current_tool` 是工具标识符 (`read_file`), `last_activity_description` 是它自己
 * 拼的英文句子 (`API error recovery (attempt 3/3)`)。全中文界面里突然冒一行英文。
 *
 * 我第一版写的是"原样透出不加工", 理由是「hermes 改形状该在 contract test 上红,
 * 不是在这里被悄悄兼容掉」。那条对**字段结构**成立 —— 不猜、不补默认值。但把它
 * 延伸到**展示文案**是错的: 结构不加工是原则, 文案不翻译只是懒。
 *
 * # 做法: 抄 P28
 *
 * 插件侧 `plugin_weixin_zh.py` 早就在干同一件事 (把 hermes 的英文审批提示翻成中文
 * 再发微信), 而且踩过一次教训, 注释原话:
 *
 *     ⚠ 7/22 鸿波 catch: hermes v0.19 改了原文 → 老 pattern silent miss →
 *       英文全条泄漏到微信
 *
 * 所以策略照它: **认得的翻, 认不得的原样显示 + warn 一条**。宁可丑一行, 不显示
 * 错的内容; warn 让下次一发现就补表, 而不是静默烂着。
 *
 * # 表是查出来的不是编的
 *
 * 文案全集来自 hermes 源码里 `_touch_activity(...)` 的调用点:
 *   agent/conversation_loop.py  —— API 调用 / 工具执行 / 错误重试
 *   agent/conversation_compression.py —— 上下文压缩那组
 * 工具名来自网关日志里实际下发的 33 个 tools。
 */

/** 每个没认出来的串只 warn 一次 —— 探针 3 秒一轮, 不去重会把控制台刷爆。 */
const warned = new Set<string>();

function warnOnce(kind: string, raw: string): void {
  const key = `${kind}:${raw}`;
  if (warned.has(key)) return;
  warned.add(key);
  console.warn(
    `[agentActivity] hermes ${kind} 没有中文对应, 原样显示: ${JSON.stringify(raw)}\n` +
      `  → 补表: src/lib/agentActivityI18n.ts (跟插件侧 P28 _P28_REPLACEMENTS 同思路)`,
  );
}

/**
 * hermes 工具名 → 中文。
 *
 * 覆盖网关日志里实际下发的那 33 个。hermes 加新工具时这里会 miss → warn。
 */
export const TOOL_ZH: Readonly<Record<string, string>> = {
  browser_back: "浏览器后退",
  browser_cdp: "浏览器底层调试",
  browser_click: "点网页元素",
  browser_console: "看浏览器控制台",
  browser_dialog: "处理网页弹窗",
  browser_get_images: "取网页图片",
  browser_navigate: "打开网页",
  browser_press: "按键",
  browser_scroll: "滚动页面",
  browser_snapshot: "抓网页结构",
  browser_type: "网页输入",
  browser_vision: "看网页截图",
  cronjob: "定时任务",
  delegate_task: "派子任务",
  execute_code: "跑代码",
  memory: "记忆",
  patch: "改文件",
  process: "管进程",
  read_file: "读文件",
  search_files: "搜文件",
  session_search: "搜历史会话",
  skill_manage: "管技能",
  skill_view: "看技能",
  skills_list: "列技能",
  terminal: "终端命令",
  todo: "待办",
  tool_call: "调工具",
  tool_describe: "查工具说明",
  tool_search: "搜工具",
  vision_analyze: "看图",
  web_extract: "抓网页正文",
  web_search: "联网搜索",
  write_file: "写文件",
};

/** 工具名中文化; 认不出就原样返回并 warn 一次。 */
export function translateTool(name: string | null | undefined): string {
  const raw = (name || "").trim();
  if (!raw) return "";
  const zh = TOOL_ZH[raw];
  if (zh) return zh;
  warnOnce("工具名", raw);
  return raw;
}

/**
 * 活动描述的规则表。**顺序敏感**: 具体的排前面。
 *
 * 每条 = [匹配模式, 用捕获组拼中文]。带参数的用捕获组, 不做整句精确匹配 ——
 * P28 那张表是整句匹配, 套不过来 (hermes 这些描述都是 f-string 模板)。
 */
const DESC_RULES: ReadonlyArray<[RegExp, (m: RegExpMatchArray) => string]> = [
  // conversation_loop.py:4180
  [/^API error recovery \(attempt (\d+)\/(\d+)\)$/, (m) => `上游报错, 正在重试 (第 ${m[1]}/${m[2]} 次)`],
  [/^starting API call #(\d+)$/, (m) => `发起第 ${m[1]} 次模型调用`],
  [/^API call #(\d+) completed$/, (m) => `第 ${m[1]} 次模型调用完成`],
  [/^tool results posted, continuing iteration #(\d+)$/, (m) => `工具结果已回填, 继续第 ${m[1]} 轮`],
  [/^executing tool: (.+)$/, (m) => `正在执行 ${translateTool(m[1])}`],
  [/^executing (\d+) tools concurrently: (.+)$/, (m) =>
    `并发执行 ${m[1]} 个工具: ${m[2].split(/,\s*/).map(translateTool).join("、")}`],
  [/^tool completed: (\S+) \(([\d.]+)s/, (m) => `${translateTool(m[1])} 完成 (${m[2]} 秒)`],
  [/^receiving stream response$/, () => "正在接收模型输出"],
  [/^waiting for non-streaming API response$/, () => "等模型返回 (非流式)"],
  [/^waiting for provider response/, () => "等模型返回"],
  // agent/conversation_compression.py —— session_activity 契约里点名的那组 provenance
  [/^context compression started$/, () => "开始压缩上下文"],
  [/^context compression in progress$/, () => "正在压缩上下文"],
  [/^context compression completed$/, () => "上下文压缩完成"],
  [/^context compression failed$/, () => "上下文压缩失败"],
  [/^context compression cancelled$/, () => "上下文压缩已取消"],
];

/**
 * 活动描述中文化; 认不出就原样返回并 warn 一次。
 *
 * 空串直接返空 —— 那不是"没认出来", 是 hermes 本来就没给描述, 不该 warn。
 */
export function translateActivityDescription(desc: string | null | undefined): string {
  const raw = (desc || "").trim();
  if (!raw) return "";
  for (const [pattern, render] of DESC_RULES) {
    const m = raw.match(pattern);
    if (m) return render(m);
  }
  warnOnce("活动描述", raw);
  return raw;
}

/** 测试用: 清掉 warn 去重表。 */
export function _resetWarnedForTest(): void {
  warned.clear();
}
