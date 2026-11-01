/** 「添加到主屏幕」要的几个标签 —— 只在 /m 上加, 不影响门户其他页面。 */
export function installPwaTags(doc: Document = document) {
  const add = (tag: string, attrs: Record<string, string>) => {
    const sel = Object.entries(attrs).filter(([k]) => k === "rel" || k === "name").map(([k, v]) => `[${k}="${v}"]`).join("");
    if (doc.head.querySelector(`${tag}${sel}`)) return;
    const el = doc.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    doc.head.appendChild(el);
  };
  add("link", { rel: "manifest", href: "/m-manifest.webmanifest" });
  add("link", { rel: "apple-touch-icon", href: "/catfish-mascot.svg" });
  add("meta", { name: "apple-mobile-web-app-capable", content: "yes" });
  add("meta", { name: "apple-mobile-web-app-title", content: "小鲶" });
  add("meta", { name: "theme-color", content: "#2d8a87" });
  doc.title = "小鲶";
}

/** 申请持久存储: 浏览器清理空间时不动这个网站存的东西 (配对密钥在里面)。Chrome / 安卓基本都给。 */
export function requestPersistentStorage(): void {
  try {
    void navigator.storage?.persist?.().catch(() => undefined);
  } catch {
    // 老浏览器没有这个接口: 照常用
  }
}

/** iPhone / iPad 上用浏览器 (不是从主屏幕) 打开的 —— Safari 一周不打开会清掉网站数据, 配对就丢了。
 *  从主屏幕打开的网页应用不受这条限制, 所以要提示加到主屏幕。 */
export function needsHomeScreenHint(
  ua: string = typeof navigator === "undefined" ? "" : navigator.userAgent,
  standalone: boolean = typeof window !== "undefined" && (
    (navigator as { standalone?: boolean }).standalone === true ||
    window.matchMedia?.("(display-mode: standalone)")?.matches === true),
): boolean {
  return /iPhone|iPad|iPod/.test(ua) && !standalone;
}
