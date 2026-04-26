/** Dev / Prod 模式判断 + 环境配置。
 *
 * Gateway URL 优先级:
 *   1. VITE_CATFISH_GATEWAY_URL (build-time / dev-time 注入的完整 URL)
 *   2. 由 host + port env 拼出 (跟 Rust 侧 services::endpoints 同名约定)
 *   3. 默认 http://127.0.0.1:8999
 *
 * 注: vite 在 build 时把 import.meta.env 静态替换。VITE_ 前缀必须的, 不然
 *     vite 不会把它打进 bundle。
 */

const DEFAULT_GATEWAY_HOST = "127.0.0.1";
const DEFAULT_GATEWAY_PORT = 8999;

function readGatewayUrl(): string {
  const env = import.meta.env;
  const fullUrl = env.VITE_CATFISH_GATEWAY_URL;
  if (typeof fullUrl === "string" && fullUrl.length > 0) {
    return fullUrl.replace(/\/+$/, "");
  }
  const host =
    (typeof env.VITE_CATFISH_GATEWAY_HOST === "string" &&
      env.VITE_CATFISH_GATEWAY_HOST) ||
    DEFAULT_GATEWAY_HOST;
  const portRaw =
    (typeof env.VITE_CATFISH_GATEWAY_PORT === "string" &&
      env.VITE_CATFISH_GATEWAY_PORT) ||
    String(DEFAULT_GATEWAY_PORT);
  const port = Number(portRaw) || DEFAULT_GATEWAY_PORT;
  return `http://${host}:${port}`;
}

export const isDev = import.meta.env.DEV;
export const isProd = import.meta.env.PROD;

export const config = {
  gatewayUrl: readGatewayUrl(),
  pollIntervalMs: 3000,
};
