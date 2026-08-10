/** 一次 LLM 调用要穿过的所有超时 —— 单一来源 (8/10).
 *
 * ## 为什么建这个文件
 *
 * 8/10 早安页「advisor 综合判断暂不可用」查了一整天。真因是
 * `http_proxy.ts` 里**写死的 30 秒**掐断了 Companion → hermes 这一跳,
 * 而当天在 gateway 那层查了很久 (压缩 / thinking / system 归一 / preflight)
 * —— 那些都是真 bug 也都修了, **但没有一个是它挂掉的原因**。
 *
 * 难的不是"有硬编码", 是**没人知道这条链上有几个超时**:
 *
 *   · 界面上有个「超时(秒)」输入框, 填了 180, gateway 日志也确实按 180 跑
 *   · 但请求在更外面一层被 30 秒掐了, 而**那一层在界面上不存在**
 *   · 掐断的表现是 `operation timed out`, 跟"上游慢"长得一模一样
 *
 * 而且同一个 600_000 当时散在四处 (http_proxy 两处 / profile / briefing_advisor),
 * 注释互相引用"跟 advisor 一致"、"详见 briefing_advisor.ts:731" —— 靠人肉同步。
 * `briefing_advisor.ts` 里甚至还留着"跟 LLM_TIMEOUT_MS=300_000 同步"这种早就
 * 过期的话。
 *
 * ## 这条链长什么样 (从外到内)
 *
 *   1. race sentinel        LLM_RACE_TIMEOUT_MS   UI 不干等, **不 abort 请求**
 *   2. Rust http_proxy      LLM_TRANSPORT_TIMEOUT_MS  ← **真正会掐断请求的**
 *   3. hermes agent loop    (没有显式配置, 走 SDK 默认)
 *   4. gateway → 上游       models 库里的 `timeout` 字段  ← **界面上能改的只有这个**
 *
 * ## 唯一的硬约束
 *
 * **race ≥ transport**。反过来的话 race 先触发, UI 走 stale fallback, 而
 * transport 那个数根本没机会生效 —— 配了等于没配, 而且没有任何报错。
 * assertTimeoutChain() 在启动时检查这一条。
 *
 * 第 3、4 层在别的进程里, 这里管不到 —— 但它们**更靠内**, 内层比外层小只会
 * 让内层先报错, 那个错误是有内容的 (gateway 会说清哪个模型超时), 不会像
 * 30 秒那次一样变成一句无主的 "operation timed out"。
 */

/** LLM 调用: 传输层超时。**这个数会真的掐断请求。**
 *
 * 覆盖 Companion → hermes(8642) / → gateway(8999) 的整个 HTTP 往返。
 * advisor Call 1 是 hermes agent loop, 实测每轮 10-13 秒 × 4~6 轮 = 40~70 秒;
 * profile 单次 LLM call 实测最高 116 秒。10 分钟是给"内网模型 + 多轮 agent"
 * 留的上限, 不是期望值。
 */
export const LLM_TRANSPORT_TIMEOUT_MS = 600_000;

/** LLM 调用: UI 侧的 race sentinel。**不 abort 请求**, 只让界面别干等。
 *
 * 5/21 学到的: Tauri webview 失焦会 suspend, 挂 AbortSignal 会把正常请求也
 * 掐掉。所以这里只做 Promise.race —— 超时后 UI 走 stale fallback, 后台那个
 * 请求继续跑完并写 cache, 下次时段触发就能用上。
 *
 * 必须 ≥ LLM_TRANSPORT_TIMEOUT_MS, 见 assertTimeoutChain。
 */
export const LLM_RACE_TIMEOUT_MS = 600_000;

/** 普通 API / 探活 / metadata。超了多半是真挂了, 不该陪着等。 */
export const DEFAULT_TRANSPORT_TIMEOUT_MS = 30_000;

/** 给员工看的分钟数 —— 界面文案别再各写各的。
 *
 * 8/8 踩过: AdvisorView 的超时文案硬编码 ">5min", 而实际值早就是 600s,
 * 界面告诉员工等 5 分钟, 实际要等 10 分钟。
 */
export const LLM_TIMEOUT_MINUTES = Math.round(LLM_RACE_TIMEOUT_MS / 60_000);

/** 启动自检: 超时链的顺序对不对。返回问题列表, 空数组 = 没问题。
 *
 * 纯函数 (不读全局、不打日志), 方便单测。调用点在 main.tsx 启动处。
 */
export function checkTimeoutChain(): string[] {
  const problems: string[] = [];

  if (LLM_RACE_TIMEOUT_MS < LLM_TRANSPORT_TIMEOUT_MS) {
    problems.push(
      `race sentinel (${LLM_RACE_TIMEOUT_MS}ms) < 传输层超时 ` +
        `(${LLM_TRANSPORT_TIMEOUT_MS}ms) —— race 会先触发, 传输层那个数` +
        `根本没机会生效, 配了等于没配。`,
    );
  }
  if (LLM_TRANSPORT_TIMEOUT_MS <= DEFAULT_TRANSPORT_TIMEOUT_MS) {
    problems.push(
      `LLM 超时 (${LLM_TRANSPORT_TIMEOUT_MS}ms) ≤ 普通请求超时 ` +
        `(${DEFAULT_TRANSPORT_TIMEOUT_MS}ms) —— 分档没有意义了。` +
        `8/10 那次事故就是 LLM 调用被当普通请求限了 30 秒。`,
    );
  }
  return problems;
}
