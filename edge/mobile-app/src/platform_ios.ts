/** iOS App 平台层 (docs/MOBILE-CLIENT-DESIGN.md §13.2): 填的服务器地址、系统浏览器登录、钥匙串。
 *
 * 钥匙串里存: 服务器地址、续期令牌、手机页要的那几项 (设备号 / 长期私钥 / 配过的电脑 / 上次连的电脑)。
 * 启动时整份读进内存 (手机页按同步方式读), 写的时候内存立即生效、钥匙串异步落。
 * 不开 iCloud 钥匙串同步 —— 密钥只属于这一部手机。
 */
import { App } from "@capacitor/app";
import { Browser } from "@capacitor/browser";
import { SecureStorage } from "@aparajita/capacitor-secure-storage";

import type { MobilePlatform, MobileStorage } from "../../../central/web/src/mobile/platform";
import * as oidc from "./oidc";

const K_SERVER = "server";
const K_REFRESH = "refresh_token";
const PREFIX = "catfish.mobile.";

const mem = new Map<string, string>();

async function persist(key: string, value: string | null): Promise<void> {
  try {
    if (value === null) await SecureStorage.removeItem(key);
    else await SecureStorage.setItem(key, value);
  } catch (e) {
    console.warn("[钥匙串] 写失败", key, e);
  }
}

/** 启动时调一次: 把钥匙串里本 App 的条目读进内存 */
export async function loadKeychain(): Promise<void> {
  const keys = await SecureStorage.keys().catch(() => [] as string[]);
  for (const k of keys) {
    const v = await SecureStorage.getItem(k).catch(() => null);
    if (v !== null) mem.set(k, v);
  }
}

const storage: MobileStorage = {
  get: (k) => mem.get(k) ?? null,
  set: (k, v) => {
    mem.set(k, v);
    void persist(k, v);
  },
};

export function server(): string | null {
  return mem.get(K_SERVER) ?? null;
}

/** 规范服务器地址: 去掉尾巴的 / 和 /m, 没写协议补 https */
export function normalizeServer(input: string): string {
  let s = input.trim().replace(/\/+$/, "").replace(/\/m$/, "");
  if (!/^https?:\/\//i.test(s)) s = `https://${s}`;
  return s;
}

export function setServer(url: string | null): void {
  if (url === null) {
    mem.delete(K_SERVER);
    void persist(K_SERVER, null);
  } else {
    storage.set(K_SERVER, url);
  }
}

export function relayUrlFor(serverUrl: string): string {
  return `${serverUrl.replace(/^http/i, "ws")}/relay/ws`;
}

// ── 登录 ────────────────────────────────────────────────

let tokens: oidc.Tokens | null = null;
let discovery: oidc.Discovery | null = null;

async function disco(): Promise<oidc.Discovery> {
  const s = server();
  if (!s) throw new Error("还没填服务器地址");
  if (!discovery) discovery = await oidc.discover(s);
  return discovery;
}

function saveTokens(t: oidc.Tokens) {
  tokens = t;
  if (t.refreshToken) storage.set(PREFIX + K_REFRESH, t.refreshToken);
}

async function getToken(): Promise<string | null> {
  const now = Math.floor(Date.now() / 1000);
  if (tokens && tokens.expiresAt - 60 > now) return tokens.idToken;
  const rt = mem.get(PREFIX + K_REFRESH);
  if (!rt) return null;
  try {
    saveTokens(await oidc.refresh(await disco(), rt));
    return tokens!.idToken;
  } catch (e) {
    console.warn("[登录] 续期失败, 要重新登录", e);
    return null;
  }
}

/** 弹系统浏览器登录; 回调由 appUrlOpen 收, 换完令牌后整页重载回到手机页 */
async function login(): Promise<void> {
  const d = await disco();
  const verifier = oidc.randomString(48);
  const state = oidc.randomString(16);
  const challenge = await oidc.pkceChallenge(verifier);
  const handle = await App.addListener("appUrlOpen", async ({ url }) => {
    const code = oidc.codeFromCallback(url, state);
    if (!code) return;
    await handle.remove();
    await Browser.close().catch(() => undefined);
    try {
      saveTokens(await oidc.exchangeCode(d, code, verifier));
      window.location.reload();
    } catch (e) {
      alert(`登录没成功: ${e instanceof Error ? e.message : e}`);
    }
  });
  await Browser.open({ url: oidc.authorizeUrl(d, state, challenge), presentationStyle: "popover" });
}

export const iosPlatform: MobilePlatform = {
  kind: "ios",
  isLoggedIn: async () => (await getToken()) !== null,
  getToken,
  login,
  relayUrl: () => relayUrlFor(server() ?? ""),
  storage,
  resetServer: () => {
    setServer(null);
    tokens = null;
    discovery = null;
    mem.delete(PREFIX + K_REFRESH);
    void persist(PREFIX + K_REFRESH, null);
    window.location.reload();
  },
};
