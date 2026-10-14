/** 「门户地址 + 接入码」自动导入中央内部 CA (10/14)。Rust: commands/server_pairing.rs。
 *  Companion 自己去 <门户>/catfish-ca.pem 取证书, 指纹跟接入码对上才保存; 对不上不保存。 */
import { invoke } from "@tauri-apps/api/core";

/** 只有 HTTPS 部署才要装证书 */
export function needsPairing(webUrl: string): boolean {
  return /^https:\/\//i.test(webUrl.trim());
}

/** 返回证书保存路径; 接入码不对 / 连不上会抛出给员工看的中文原因 */
export function pairServerCertificate(webUrl: string, code: string): Promise<string> {
  return invoke<string>("pair_server_certificate", {
    webUrl: webUrl.trim().replace(/\/+$/, ""),
    code: code.trim(),
  });
}

export const PAIRING_CODE_PLACEHOLDER = "形如 3F9A-1C2B-77D0-9E41, 找 IT 要";

/** catfish://connect 接入链接解析结果 (Rust: commands/connect_link.rs) */
export interface ConnectLink {
  webUrl: string;
  gatewayUrl: string;
  identityUrl: string;
  code: string | null;
}

export const CONNECT_LINK_EVENT = "catfish-connect-link";

export const takeConnectLink = () => invoke<ConnectLink | null>("connect_link_take");
export const parseConnectLink = (link: string) => invoke<ConnectLink>("connect_link_parse", { link: link.trim() });
