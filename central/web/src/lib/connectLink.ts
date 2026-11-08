/** 员工接入链接 (10/14)。Companion 端解析在 edge/companion-app/src-tauri/src/commands/connect_link.rs,
 *  落地页在 public/connect.html。gw / id 不带时 Companion 按门户主机 + 8999 / 8998 推。 */

export function catfishConnectLink(web: string, code: string | null): string {
  const p = new URLSearchParams({ web });
  if (code) p.set("code", code);
  return `catfish://connect?${p.toString()}`;
}

/** 发到微信里的那条: 落地页的 http 链接 (微信里 catfish:// 点不开, https 又会撞自签证书警告)。
 *  不知道 http 端口时退回当前门户地址。 */
export function landingPageLink(origin: string, code: string | null, httpPort?: string): string {
  const u = new URL(origin);
  const base = httpPort ? `http://${u.hostname.includes(":") ? `[${u.hostname}]` : u.hostname}:${httpPort}` : origin;
  const p = new URLSearchParams({ web: origin });
  if (code) p.set("code", code);
  return `${base}/connect.html?${p.toString()}`;
}

/** 11/08 (IP + 域名 / 公网映射): 外面的员工只够得着门户这一个 https 地址 (8999 / 8998 没开到外网,
 *  公网映射也通常只映射这一个端口)。网关和认证走门户的反代 (/v1 /api /authorize /token …),
 *  所以链接里把 gw / id 都写成门户地址本身。接入码照带: 正式证书时 Companion 导入内部 CA 也无害。 */
export function catfishConnectLinkVia(web: string, code: string | null): string {
  const p = new URLSearchParams({ web, gw: web, id: web });
  if (code) p.set("code", code);
  return `catfish://connect?${p.toString()}`;
}

/** 外部地址的落地页就在它自己的 https 上 (外网没有 http 端口可用)。 */
export function landingPageLinkVia(web: string, code: string | null): string {
  const p = new URLSearchParams({ web, gw: web, id: web });
  if (code) p.set("code", code);
  return `${web}/connect.html?${p.toString()}`;
}

export interface ConnectEntry {
  /** 门户地址 (内网 IP 或 域名 / 公网映射) */
  web: string;
  /** 内网 IP 那个 = true: 网关 / 认证按 8999 / 8998 直连, 落地页走 http */
  internal: boolean;
  page: string;
  app: string;
}

/** 全部门户地址各自的接入方式。addresses[0] 是内网主地址 (= 主 issuer)。 */
export function connectEntries(addresses: readonly string[], code: string | null, httpPort?: string): ConnectEntry[] {
  return addresses.map((web, i) =>
    i === 0
      ? { web, internal: true, page: landingPageLink(web, code, httpPort), app: catfishConnectLink(web, code) }
      : { web, internal: false, page: landingPageLinkVia(web, code), app: catfishConnectLinkVia(web, code) },
  );
}
