/** 11/08 (IP + 域名 / 公网映射同时用): 门户从哪个地址打开, 就用哪个 OIDC issuer。
 *
 * identity 按请求来源签 iss (identity issuers.py), 浏览器的 authority 必须跟它一致 ——
 * 员工在内网打 https://<IP>, 外面的人打 https://<域名> 或 https://<公网IP>:<端口>,
 * 两边都得能登录。config.js 的 oidcIssuers 是全部地址 (主地址在前), 这里挑
 * origin 一样的那个; 都对不上 (比如 dev 环境) 用第一个, 跟以前只有一个地址时一样。
 */

function originOf(url: string): string | null {
  try {
    return new URL(url).origin;
  } catch {
    return null;
  }
}

export function pickIssuer(issuers: readonly string[], origin: string): string | undefined {
  const list = issuers.map((i) => i.trim().replace(/\/+$/, "")).filter(Boolean);
  const want = originOf(origin);
  return list.find((i) => want !== null && originOf(i) === want) ?? list[0];
}
