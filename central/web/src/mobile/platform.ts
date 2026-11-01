/** 手机页跑在哪: 浏览器 (`/m`) 还是 iOS App (edge/mobile-app) —— 登录、存储、中转站地址三处不一样, 界面共用。
 *
 * 网页版在 platform_web.ts (oidc-client-ts + localStorage + 同源 /relay/ws);
 * App 在 edge/mobile-app/src/platform_ios.ts (系统浏览器登录 + 钥匙串 + 填的服务器地址)。
 * 入口先 setPlatform(...) 再渲染 MobileRoot。
 */
export interface MobileStorage {
  /** 同步读 (App 启动时已把钥匙串整份读进内存) */
  get(key: string): string | null;
  /** 写: 内存立即生效, 落盘可以是异步的 */
  set(key: string, value: string): void;
}

export interface MobilePlatform {
  kind: "web" | "ios";
  /** 有没有登录 (没登录就去登) */
  isLoggedIn(): Promise<boolean>;
  /** 拿给中转站的单位身份令牌 (过期了自己续) */
  getToken(): Promise<string | null>;
  /** 去登录; 登完回到手机页 */
  login(): Promise<void>;
  /** 中转站 WebSocket 地址 */
  relayUrl(): string;
  storage: MobileStorage;
  /** App 才有: 换服务器 (回到填地址那页) */
  resetServer?: () => void;
}

/** 内存兜底 (浏览器隐私模式存不了时用) */
export function memoryStorage(): MobileStorage {
  const m = new Map<string, string>();
  return { get: (k) => m.get(k) ?? null, set: (k, v) => void m.set(k, v) };
}

let current: MobilePlatform | null = null;

export function setPlatform(p: MobilePlatform): void {
  current = p;
}

export function platform(): MobilePlatform {
  if (!current) throw new Error("手机页还没设置运行平台 (setPlatform)");
  return current;
}
