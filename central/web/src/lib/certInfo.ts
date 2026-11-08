/** 门户「服务状态」里的证书到期 (11/08, IP + 域名 / 正式证书)。
 *
 * 数据来自 /catfish-cert-info.json —— 中央的 certgen.py (装机 / 每天的自动续期) 写进
 * certs/cert-info.json, web 容器的 443 原样给出。只有公开信息 (到期日 / 证书上的名字),
 * TLS 握手时本来就看得到。HTTP 部署没有这个文件 (拿回来的是首页 HTML) → null, 不显示。
 *
 *   internal  内部 CA 签的服务器证书 (IP 访问用), 397 天, 每天自动检查、剩 30 天自动重签
 *   official  客户给的正式证书 (域名用), **不会**自动续 —— 到期前要换, 所以 30 天内要显眼
 */

export interface CertEntry {
  /** ISO 时间 (UTC) */
  not_after: string;
  /** SAN 里的名字 (IP / 域名) */
  names: string[];
  /** 签发者 (正式证书看得出是哪家 CA) */
  issuer?: string;
}

export interface CertInfo {
  internal?: CertEntry | null;
  official?: CertEntry | null;
}

export const WARN_DAYS = 30;

export function daysLeft(notAfter: string, now: Date = new Date()): number | null {
  const t = Date.parse(notAfter);
  if (Number.isNaN(t)) return null;
  return Math.floor((t - now.getTime()) / 86_400_000);
}

export type CertLevel = "ok" | "warn" | "expired" | "unknown";

export function certLevel(days: number | null): CertLevel {
  if (days === null) return "unknown";
  if (days < 0) return "expired";
  if (days < WARN_DAYS) return "warn";
  return "ok";
}

/** 给人看的一句话。official 不会自动续, 文案要说清楚该干什么。 */
export function certSummary(kind: "internal" | "official", entry: CertEntry, now: Date = new Date()): string {
  const days = daysLeft(entry.not_after, now);
  const date = entry.not_after.slice(0, 10);
  const level = certLevel(days);
  if (level === "unknown") return `到期日读不出来 (${entry.not_after})`;
  if (level === "expired") {
    return kind === "official"
      ? `已于 ${date} 过期 —— 用域名打开会报证书错误, 尽快换新证书 (UPGRADE=1 TLS_CERT=… TLS_KEY=… 重跑装机)`
      : `已于 ${date} 过期 —— 在服务器上跑 bash renew-cert.sh`;
  }
  if (level === "warn") {
    return kind === "official"
      ? `${date} 到期, 还剩 ${days} 天 —— 正式证书不会自动续, 请向证书供应商续期后换上`
      : `${date} 到期, 还剩 ${days} 天 (每天自动检查, 应该很快会自动重签; 一直不变请看 logs/cert-renew.log)`;
  }
  return `${date} 到期, 还剩 ${days} 天${kind === "internal" ? " (自动续期)" : ""}`;
}

export async function fetchCertInfo(): Promise<CertInfo | null> {
  try {
    const r = await fetch("/catfish-cert-info.json", { cache: "no-store" });
    if (!r.ok || !(r.headers.get("content-type") || "").includes("json")) return null;
    const j = (await r.json()) as CertInfo;
    return j && typeof j === "object" ? j : null;
  } catch {
    return null;
  }
}
