/** 上游把错误当正文返回时的识别 (8/8).
 *
 * # 病
 *
 * 8/8 鸿波点「拟稿」, 正文框里出现的是:
 *
 *     API call failed after 3 retries: An error occurred during streaming
 *
 * HTTP **200**, `choices[0].message.content` 就是这段英文。真因是百炼
 * token-plan 一周配额耗尽 (429 insufficient_quota), hermes 的 agent loop
 * 重试三次之后把自己的错误信息当成"助手的回答"返了回来。
 *
 * 这是最坏的一种失败 —— **看起来像成功了**。员工拿到一句英文报错当草稿,
 * 而且没有任何提示告诉他这不是模型写的。
 *
 * # 为什么网关那道防御盖不到
 *
 * 网关有同款检测 (app.py:2081 BL-ADVISOR-UPSTREAM-ERROR-AS-CONTENT, 6/1 加,
 * 命中就转 502)。但那只盖住"经过网关"的调用。拟稿 / 早安卡片打的是
 * hermes 8642, 错误文本正是 hermes 自己产生的 —— 网关根本不在这条链上。
 *
 * 所以客户端必须有自己的一道。`briefing_advisor.ts` 6/1 就加了 (它的注释写着
 * "双层防御之一"), 但只加在了它自己那一处 —— emailDraft 和 briefing 的
 * 三个调用点一直裸着。抽出来共用, 别再靠人记得复制那行正则。
 *
 * # 判据
 *
 * 关键词 + **长度上限**两个条件同时成立才算。只看关键词会误伤
 * (员工真问"API call failed 是什么意思"时 LLM 的正常回答会复读这些词),
 * 而上游的错误文本都很短 —— 500 字符这个阈值沿用 briefing_advisor 6/1
 * 的原值, 它在生产里跑了两个月没误伤过。
 */

/** 上游 (hermes / LiteLLM / 私有 LLM 包装层) 把错误当 content 返时的特征词。
 *
 *  实测来源: ~/.hermes/logs/gateway.log 与 8/8 的拟稿框。
 *  跟网关 app.py:2086 的清单保持同源 —— 那边加了新词, 这边也该跟。 */
const UPSTREAM_ERROR_PATTERN =
  /API call failed|after \d+ retries|during streaming|retries exhausted/i;

/** 超过这个长度就不当错误看 —— 真错误都很短, 长文本更可能是 LLM 正常复读。 */
const MAX_ERROR_LEN = 500;

/** content 是不是"上游错误伪装成的正文"。 */
export function isUpstreamErrorAsContent(content: unknown): boolean {
  if (typeof content !== "string") return false;
  return content.length < MAX_ERROR_LEN && UPSTREAM_ERROR_PATTERN.test(content);
}

/** 命中就 warn 一声并返 true, 让调用方走各自的失败路径。
 *
 *  统一在这里打日志: 四个调用点各写各的话术, 排查时还得先认出它们是同一件事。
 *  `where` 用调用点自己的标签 (advisor / email-draft / briefing-...)。 */
export function warnIfUpstreamError(where: string, content: unknown): boolean {
  if (!isUpstreamErrorAsContent(content)) return false;
  console.warn(
    `[${where}] 上游把错误当正文返了 (HTTP 200 + 错误文本), 不是模型的输出。` +
      ` 常见真因: 上游配额耗尽 / 服务挂 / 重试用尽。原文:`,
    String(content).slice(0, 200),
  );
  return true;
}
