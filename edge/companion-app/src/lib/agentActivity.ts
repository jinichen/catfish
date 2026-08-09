/**
 * hermes agent 进度快照的读取端 (8/9 加, 配 P44)。
 *
 * # 本模块只管一件事
 *
 * 拉 `GET /api/catfish/agent-activity`, 把 hermes 的 activity 快照原样交出去。
 *
 * # 什么不归这里管
 *
 * **超时策略不在这里。** hermes 的契约 (`agent/session_activity.py` 开头) 写死了:
 *
 *     Observation-only: timestamp + bounded description/provenance.
 *     Notification, timeout, kill, and retry policy stay in their own components.
 *
 * 所以 `CLIENT_TIMEOUT_MS`、`_advisorInFlight` 这些留在 `briefing_advisor.ts` ——
 * 它们是 catfish 自己的超时/去重策略, **不是"跟 hermes 平行的进度源"**。
 * 8/9 一度打算把它们砍掉改读 hermes, 那是误读了契约。
 *
 * 本模块提供的是**判据的原料**: `seconds_since_activity` 让 advisor 可以把
 * "绝对等了 600 秒" 换成 "已经 N 秒没动", 那才是这份契约的正确用法。判据怎么
 * 用、超时了做什么, 都归调用方。
 *
 * # 为什么不做成"自动重连的长连接"
 *
 * advisor 那几个请求是 `stream: false`, 本来就没有中途通道; 这里是**旁路轮询**,
 * 跟那条请求完全独立。轮询失败不该影响主请求 —— 所以所有错误都吞掉返 null,
 * 让 UI 退回"没有进度信息"这个已知状态, 而不是把主流程带崩。
 */
import { translateActivityDescription, translateTool } from "./agentActivityI18n";
import { config } from "./env";
import { fetchWithAuth } from "./me";

/** 端点路径 —— 跟插件 `activity_probe.py:ROUTE_PATH` 必须一致。 */
const ACTIVITY_PATH = "/api/catfish/agent-activity";

/** 轮询间隔。比 hermes 的 SessionDB 心跳 (>=60s) 密, 因为这里读的是内存快照。 */
export const POLL_INTERVAL_MS = 3_000;

/** 单次请求的兜底超时 —— 探针不该比它观察的东西还能拖。 */
const PROBE_TIMEOUT_MS = 4_000;

/**
 * 一个正在跑的 turn 的快照。
 *
 * 字段是 hermes `AIAgent.get_activity_summary()` 的**原样输出**, 我们不改名不补
 * 默认值。全部标成可选 —— hermes 换版本可能加减字段, 少一个不该让 UI 崩。
 * 真要"形状变了必须知道", 那是 contract test 的事 (见 agentActivity.test.ts),
 * 不是在这里用默认值把它盖掉。
 */
export interface AgentTurnActivity {
  /** 插件补的, 不是 hermes 的字段。 */
  session_key?: string;
  /** 上次活动的 epoch 秒。 */
  last_activity_at?: number | null;
  /** 已经多久没动 —— 做 idle 判据就用它。 */
  seconds_since_activity?: number | null;
  /** ≤120 字的活动描述 (hermes 侧 bound_activity_description 保证)。 */
  last_activity_description?: string;
  /** 来源枚举: unknown / agent.compression / …… */
  last_activity_provenance?: string;
  /** 当前在跑的工具名; 没有就是 null。 */
  current_tool?: string | null;
  /** 第几轮 API 调用。 */
  api_call_count?: number;
  max_iterations?: number;
  budget_used?: number;
  budget_max?: number;
}

export interface AgentActivityResult {
  available: boolean;
  /** available=false 时的原因码 (封闭词表, 见 activity_probe.py)。 */
  reason?: string;
  turns: AgentTurnActivity[];
}

/** 探针自己拿不到结果时的表示 —— 跟"hermes 说没有正在跑的 turn"要分得开。 */
export const PROBE_UNREACHABLE = "probe_unreachable";

/**
 * 拉一次快照。**永不抛**; 拿不到返回 `{available:false, reason:...}`。
 *
 * 之所以不返 null: 调用方需要区分"探不到"和"探到了但没有正在跑的 turn"——
 * 前者是我们的问题 (要不要退回盲等), 后者是正常态 (agent 还没开始 / 已结束)。
 * 用 null 表示会把这两件事糊在一起。
 */
export async function fetchAgentActivity(): Promise<AgentActivityResult> {
  const url = `${config.backendUrl}${ACTIVITY_PATH}`;
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), PROBE_TIMEOUT_MS);
  try {
    // 这里挂 AbortSignal 是安全的 —— 跟 advisor 主请求不同, 探针中断了没有
    // 任何副作用, 下一轮 3 秒后再来。advisor 那边不挂是因为 Tauri 失焦会
    // abort in-flight fetch, 中断会丢掉真结果 (5/21)。
    // skipReauth: **后台探针绝不能把员工拽去登录页。**
    //
    // 8/9 实撞: 这个端点被 me.ts 的 "含 /api/ 就是 gateway" 规则判错, 拿 OAuth
    // JWT 去撞 hermes _check_auth → 401 → invoke("auth_login") → 弹浏览器登录页,
    // 而这里 3 秒一轮 → 登录页一遍遍弹, 而且看起来跟"进度显示"毫无关系。
    //
    // 路由那条已经在 me.ts 的 HERMES_OWNED_API_PREFIXES 修了。这里再加一道:
    // 无论以后因为什么原因 401, 一个静默轮询都不该有资格打断员工。
    const resp = await fetchWithAuth(
      url,
      { method: "GET", signal: ctl.signal },
      { skipReauth: true },
    );
    if (!resp.ok) {
      return { available: false, reason: `http_${resp.status}`, turns: [] };
    }
    const data = (await resp.json()) as AgentActivityResult;
    if (!data || typeof data !== "object" || !Array.isArray(data.turns)) {
      return { available: false, reason: "bad_shape", turns: [] };
    }
    return data;
  } catch {
    // 探针失败不打 console.warn —— 它每 3 秒跑一次, 会把控制台刷爆。
    return { available: false, reason: PROBE_UNREACHABLE, turns: [] };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 挑一条最该显示的 turn。
 *
 * 员工机上同时跑的 turn 通常 0-1 个; 真有多条时取"最近动过的"那条 —— 它是
 * 用户此刻最可能在等的。没有可比时间就退回第一条, 不假装排过序。
 */
export function pickPrimaryTurn(
  turns: AgentTurnActivity[],
): AgentTurnActivity | null {
  if (!turns.length) return null;
  let best = turns[0];
  for (const t of turns) {
    const a = t.last_activity_at;
    const b = best.last_activity_at;
    if (typeof a === "number" && (typeof b !== "number" || a > b)) best = t;
  }
  return best;
}

/**
 * 把快照渲染成一句人话。返回空串表示"没什么可说的", 调用方自己决定显示什么。
 *
 * 刻意不在这里编"正在思考…"之类的兜底文案 —— 那是 UI 的措辞决定, 放这儿会让
 * 两个地方都以为对方负责。
 */
export function describeTurn(turn: AgentTurnActivity | null): string {
  if (!turn) return "";
  const parts: string[] = [];
  // 8/9: 工具名和活动描述都来自 hermes, 是英文。认得的翻成中文, 认不得的原样
  // 显示 + warn 一次 —— 见 agentActivityI18n.ts (跟插件侧 P28 同策略)。
  if (turn.current_tool) parts.push(`正在用 ${translateTool(turn.current_tool)}`);
  const desc = translateActivityDescription(turn.last_activity_description);
  if (desc && !turn.current_tool) parts.push(desc);
  if (typeof turn.api_call_count === "number" && turn.api_call_count > 0) {
    const max = turn.max_iterations;
    parts.push(
      typeof max === "number" && max > 0
        ? `第 ${turn.api_call_count}/${max} 轮`
        : `第 ${turn.api_call_count} 轮`,
    );
  }
  const idle = turn.seconds_since_activity;
  if (typeof idle === "number" && idle >= 30) {
    parts.push(`已 ${Math.round(idle)} 秒没动静`);
  }
  return parts.join(" · ");
}

/**
 * 起一个轮询, 返回停止函数。
 *
 * 调用方负责在组件卸载 / 主请求结束时调停止 —— 这里不猜生命周期。
 */
export function startActivityPolling(
  onUpdate: (result: AgentActivityResult) => void,
  intervalMs: number = POLL_INTERVAL_MS,
): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const tick = async () => {
    if (stopped) return;
    const result = await fetchAgentActivity();
    if (stopped) return;
    onUpdate(result);
    // 用 setTimeout 链而不是 setInterval: 慢响应时不会堆积重叠请求。
    timer = setTimeout(tick, intervalMs);
  };

  void tick();

  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
  };
}
