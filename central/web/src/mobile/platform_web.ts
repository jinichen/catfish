/** 浏览器里的手机页 `/m`: 门户同一套 OIDC 登录、localStorage、同源 /relay/ws。 */
import { getCurrentUser, getIdToken, login } from "../lib/auth";
import { memoryStorage, type MobilePlatform, type MobileStorage } from "./platform";

/** localStorage 存不了 (隐私模式等) 时退到内存: 这次能用, 关掉页面后要重新配对 */
function webStorage(): MobileStorage {
  const mem = memoryStorage();
  return {
    get(k) {
      try {
        return window.localStorage.getItem(k) ?? mem.get(k);
      } catch {
        return mem.get(k);
      }
    },
    set(k, v) {
      mem.set(k, v);
      try {
        window.localStorage.setItem(k, v);
      } catch {
        // 只留在内存里
      }
    },
  };
}

/** 同源的 /relay/ws (开发时 vite 代理到 127.0.0.1:8999, 正式环境 nginx 反代到网关) */
export function sameOriginRelayUrl(loc: Location = window.location): string {
  return `${loc.protocol === "https:" ? "wss" : "ws"}://${loc.host}/relay/ws`;
}

export const webPlatform: MobilePlatform = {
  kind: "web",
  isLoggedIn: async () => !!(await getCurrentUser()),
  getToken: getIdToken,
  login: () => login("/m"),
  relayUrl: () => sameOriginRelayUrl(),
  storage: webStorage(),
};
