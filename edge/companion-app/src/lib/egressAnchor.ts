/** 数据外发记录的每日指纹 (10/25 鸿波: 有争议时要能证明记录没被改过).
 *
 * 本机每条外发记录带指纹链, 改 / 删中间的行本机就查得出; 删末尾查不出 —— 所以每天把链头
 * (最后一条的序号 + 指纹) 报给中央 /api/egress/anchor。只有指纹, 没有内容。
 * 校验时把中央存的取回来, 交给 Rust 端 transparent_log_verify 对。
 */
import { config } from "./env";
import { fetchWithAuth } from "./me";
import {
  transparentLogAnchorSent,
  transparentLogInfo,
  type EgressAnchor,
} from "./tauri_compliance";

const DAY_MS = 20 * 3600 * 1000; // 差不多一天报一次 (留点余量, 别因为早启动几分钟就跳过一天)

/** 到时间了就报一次。返回是否报了。 */
export async function sendEgressAnchorIfDue(now: Date = new Date()): Promise<boolean> {
  const info = await transparentLogInfo();
  if (info.lastId === 0) return false; // 还没有进链的记录
  if (info.anchorLastSent && now.getTime() - Date.parse(info.anchorLastSent) < DAY_MS) return false;
  const resp = await fetchWithAuth(`${config.gatewayUrl}/api/egress/anchor`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ last_id: info.lastId, last_hash: info.lastHash, row_count: info.rows }),
  });
  if (!resp.ok) return false;
  await transparentLogAnchorSent(now.toISOString());
  return true;
}

/** 中央存的本人指纹 (新的在前) */
export async function fetchEgressAnchors(): Promise<EgressAnchor[]> {
  const resp = await fetchWithAuth(`${config.gatewayUrl}/api/egress/anchors`);
  if (!resp.ok) throw new Error(`中央返回 ${resp.status}`);
  const data = (await resp.json()) as { anchors: { last_id: number; last_hash: string; received_at: string }[] };
  return data.anchors.map((a) => ({ lastId: a.last_id, lastHash: a.last_hash, receivedAt: a.received_at }));
}

/** 启动后过一会儿报一次, 之后每小时看一眼到没到时间。返回停止函数。 */
export function startEgressAnchorLoop(): () => void {
  const tick = () => {
    sendEgressAnchorIfDue().catch((e) => console.warn("[egress anchor] 报指纹失败 (下次再试):", e));
  };
  const first = setTimeout(tick, 60_000);
  const timer = setInterval(tick, 3600_000);
  return () => {
    clearTimeout(first);
    clearInterval(timer);
  };
}
