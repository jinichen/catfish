/** 员工接入码 (10/14): 内部 CA 证书的指纹, 管理员连同门户地址发给员工,
 *  Companion 拿它核对自动取回的证书 (edge/companion-app/src-tauri/src/commands/server_pairing.rs)。
 *
 *  算法跟 delivery/catfish-poc/tools/certgen.py::pairing_code 一致:
 *  SHA-256(CA 证书 DER) 前 8 字节, 大写十六进制, 4 位一组。三处改一处必须一起改。
 */

export function pemToDer(pem: string): Uint8Array<ArrayBuffer> | null {
  const m = /-----BEGIN CERTIFICATE-----([\s\S]+?)-----END CERTIFICATE-----/.exec(pem);
  if (!m) return null;
  const bin = atob(m[1].replace(/\s+/g, ""));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

export async function pairingCodeFromPem(pem: string): Promise<string | null> {
  const der = pemToDer(pem);
  if (!der || !globalThis.crypto?.subtle) return null;
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", der));
  const hex = Array.from(digest.slice(0, 8), (b) => b.toString(16).padStart(2, "0")).join("").toUpperCase();
  return hex.match(/.{4}/g)!.join("-");
}

/** 当前门户的接入码。HTTP 部署 (没有 /catfish-ca.pem, 拿回来的是首页 HTML) → null。 */
export async function fetchPairingCode(): Promise<string | null> {
  const r = await fetch("/catfish-ca.pem", { cache: "no-store" });
  if (!r.ok) return null;
  return pairingCodeFromPem(await r.text());
}
