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
