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
