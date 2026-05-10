/** Dev / Prod 模式判断 + 环境配置。
 *
 * Gateway URL 优先级 (BL-WIN9 / DEPLOY1, 5/8 改):
 *   1. **运行时**: 启动时 invoke('get_runtime_endpoints') 拿 Rust 后端读到
 *      的值 (yaml > env > default), 写回 config.gatewayUrl. **客户改
 *      ~/.catfish/companion.yaml 重启 Companion 就生效**, 不需要重新打包.
 *   2. VITE_CATFISH_GATEWAY_URL (build-time, 仅 dev 用; 打包后冻结)
 *   3. 由 host + port env 拼出 (跟 Rust 侧 services::endpoints 同名约定)
 *   4. 默认 http://127.0.0.1:8999
 *
 * App 启动时必须调一次 ``bootstrapEndpoints()`` (在 main.tsx 或 App 顶层
 * await), 把 build-time 默认值替换成运行时 yaml 值. 否则前端 fetch() 用
 * build-time 的 URL.
 *
 * 注: vite 在 build 时把 import.meta.env 静态替换。VITE_ 前缀必须的, 不然
 *     vite 不会把它打进 bundle。
 */

const DEFAULT_GATEWAY_HOST = "127.0.0.1";
const DEFAULT_GATEWAY_PORT = 8999;
// BL-ARCH2 (5/10): catfish-web 中央门户. dev vite 5173, 生产一般跟 gateway
// 同域 nginx (https://catfish.client.com), 客户可改 yaml.endpoints.web_url.
const DEFAULT_WEB_URL_DEV = "http://localhost:5173";

function readGatewayUrlBuildTime(): string {
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

function readWebUrlBuildTime(): string {
  // BL-ARCH2 fix3 (5/10): web 中央门户 URL. 优先级 (高→低):
  //   1. VITE_CATFISH_WEB_URL (build-time)
  //   2. 默认 localhost:5173 (vite dev), prod 客户**必须**改 yaml.endpoints.web_url
  //
  // ⚠ 不再 fallback gateway origin — gateway 跟 web 是两个独立服务, 同 origin
  // 只有 nginx 反代了 web/ 路径才成立, 鸿波 5/10 反馈"全部失效"就是这个 bug:
  // build 后 env.DEV=false → 走老 fallback gateway:8999 → FastAPI 返 Not Found.
  //
  // bootstrapEndpoints() 启动会从 ~/.catfish/companion.yaml 读真实 web_url
  // 覆盖, 客户改 yaml 重启 Companion 即生效.
  const env = import.meta.env;
  const explicit = env.VITE_CATFISH_WEB_URL;
  if (typeof explicit === "string" && explicit.length > 0) {
    return explicit.replace(/\/+$/, "");
  }
  return DEFAULT_WEB_URL_DEV;
}

export const isDev = import.meta.env.DEV;
export const isProd = import.meta.env.PROD;

// config 字段不再 const — bootstrapEndpoints() 启动时改 gatewayUrl / webUrl
export const config = {
  gatewayUrl: readGatewayUrlBuildTime(),
  // BL-ARCH2 (5/10): 中央门户基址. 仪表盘 "去 web 看 →" 锚点用.
  webUrl: readWebUrlBuildTime(),
  pollIntervalMs: 3000,
};

/** BL-WIN9: 启动时调一次, 从 Rust backend 拿 yaml 配置的 endpoints,
 * 替换 build-time 的默认值. 失败 silent (回退 build-time 默认), 不抛.
 *
 * 必须在 App 渲染**之前** await — 否则首次 fetch 会用错 URL.
 */
export async function bootstrapEndpoints(): Promise<void> {
  try {
    // 动态 import 避免在非 Tauri 环境 (storybook / vitest) 也加载 invoke
    const { invoke } = await import("@tauri-apps/api/core");
    const result = (await invoke("get_runtime_endpoints")) as {
      gateway_url: string;
      chrome_debug_url: string;
      web_url?: string;  // BL-ARCH2 (5/10): 可选, Rust 侧后续 ship
    };
    if (result?.gateway_url) {
      const oldUrl = config.gatewayUrl;
      config.gatewayUrl = result.gateway_url.replace(/\/+$/, "");
      if (oldUrl !== config.gatewayUrl) {
        // eslint-disable-next-line no-console
        console.info(
          `[BL-WIN9] gatewayUrl: ${oldUrl} → ${config.gatewayUrl} (来自 ~/.catfish/companion.yaml)`,
        );
      }
    }
    if (result?.web_url) {
      const oldWeb = config.webUrl;
      config.webUrl = result.web_url.replace(/\/+$/, "");
      if (oldWeb !== config.webUrl) {
        // eslint-disable-next-line no-console
        console.info(
          `[BL-ARCH2] webUrl: ${oldWeb} → ${config.webUrl} (来自 ~/.catfish/companion.yaml)`,
        );
      }
    }
  } catch (e) {
    // 非 Tauri 环境 / Rust 端没注册 / yaml 损坏 → 回退 build-time 默认
    // eslint-disable-next-line no-console
    console.warn("[BL-WIN9] get_runtime_endpoints 失败, 回退 build-time 默认", e);
  }
}
