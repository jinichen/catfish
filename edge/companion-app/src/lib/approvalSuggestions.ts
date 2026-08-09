/**
 * 命令审批建议的读取端 (配 P47)。
 *
 * 后端是 hermes 的 `approvals_suggest` —— 它挖 `~/.hermes/state.db`, 找出那些被
 * 危险命令分类器判过、但**实际执行了**的命令 (说明当时你批准了), 把反复出现的
 * 模式排名, 提议加进 `command_allowlist`。加进去之后同款命令不再弹审批。
 *
 * # 这是安全设置, 不是便利设置
 *
 * 每加一条 = 一类命令从此**静默放行**。所以这里的协议是"确认你看到的那一条":
 * 应用时把**显示过的 pattern 原文**送回去, 服务端重算提议、核对仍在集合里才写;
 * 对不上的拒掉并报回 `rejected`。
 *
 * 不用序号 —— 渲染和点击之间列表会变 (新会话被挖进来), 按序号会加错条目。
 * 详见 `edge/hermes-plugins/catfish-xcatfish-user/approvals_bridge.py` 顶部。
 *
 * # 不做的事
 *
 * 不在前端做任何安全过滤。hermes 那边已经承诺"破坏性/提权/凭据类永不提议",
 * 前端再滤一层是两套判据打架 —— 真要变严, 该在 hermes 那层变。
 */
import { config } from "./env";
import { fetchWithAuth } from "./me";

const LIST_PATH = "/api/catfish/approval-suggestions";
const APPLY_PATH = "/api/catfish/approval-suggestions/apply";

export interface ApprovalProposal {
  /** 服务端给的序号, 仅用于展示 —— **不要拿它当应用的凭据**。 */
  n: number;
  /** 命令通配 (`git push *`) 或危险类名。应用时送回的就是它。 */
  pattern: string;
  kind: "glob" | "class" | string;
  /** 这个模式被批准过多少次。 */
  count: number;
  /** 命中的危险类描述。 */
  classes: string[];
  /** 最多 3 条真实命令样例, hermes 侧已截断到 100 字。 */
  examples: string[];
}

export interface ApprovalSuggestions {
  ok: boolean;
  /** ok=false 时的原因码 (封闭词表, 见 approvals_bridge.py)。 */
  reason?: string;
  db?: string;
  days?: number;
  existing_allowlist_size?: number;
  proposals: ApprovalProposal[];
}

export interface ApplyResult {
  ok: boolean;
  reason?: string;
  /** 真正写进 allowlist 的。 */
  applied: string[];
  /** 送回去但已经不在提议里的 —— 必须显示给员工, 不能当没发生。 */
  rejected: string[];
  allowlist_size?: number;
}

/** 原因码 → 人话。认不出的原样显示 (跟 agentActivityI18n 同策略)。 */
export function describeReason(reason: string | undefined): string {
  switch (reason) {
    case "hermes_module_missing":
      return "当前 hermes 版本没有这个功能";
    case "session_db_missing":
      return "还没有会话记录可分析";
    case "scan_failed":
      return "分析出错了，详情看 hermes 日志";
    case "no_matching_proposal":
      return "选中的条目已经不在建议列表里（列表刚刷新过），请重新查看再选";
    case "auth_unavailable":
      return "鉴权组件没就位，请重启 hermes";
    case "bad_request":
      return "请求格式不对";
    default:
      return reason ? `未知错误：${reason}` : "未知错误";
  }
}

/** 拉建议列表。**永不抛。** */
export async function fetchApprovalSuggestions(): Promise<ApprovalSuggestions> {
  try {
    const resp = await fetchWithAuth(`${config.backendUrl}${LIST_PATH}`, { method: "GET" });
    if (!resp.ok) {
      return { ok: false, reason: `http_${resp.status}`, proposals: [] };
    }
    const data = (await resp.json()) as ApprovalSuggestions;
    if (!data || typeof data !== "object" || !Array.isArray(data.proposals)) {
      return { ok: false, reason: "bad_shape", proposals: [] };
    }
    return data;
  } catch {
    return { ok: false, reason: "unreachable", proposals: [] };
  }
}

/**
 * 应用选中的 pattern。**永不抛。**
 *
 * @param patterns 必须是列表里**显示过的原文** —— 服务端会核对。
 */
export async function applyApprovalPatterns(patterns: string[]): Promise<ApplyResult> {
  try {
    const resp = await fetchWithAuth(`${config.backendUrl}${APPLY_PATH}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ patterns }),
    });
    const data = (await resp.json()) as ApplyResult;
    if (!data || typeof data !== "object") {
      return { ok: false, reason: "bad_shape", applied: [], rejected: patterns };
    }
    return {
      ok: Boolean(data.ok),
      reason: data.reason,
      applied: Array.isArray(data.applied) ? data.applied : [],
      rejected: Array.isArray(data.rejected) ? data.rejected : [],
      allowlist_size: data.allowlist_size,
    };
  } catch {
    return { ok: false, reason: "unreachable", applied: [], rejected: patterns };
  }
}
