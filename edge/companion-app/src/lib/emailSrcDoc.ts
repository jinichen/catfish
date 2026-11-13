/**
 * 邮件正文 iframe 的 srcDoc 构造。
 *
 * 从 DetailPane.tsx 抽出来 (9/18) —— 它现在有分支了 (远程图拦不拦), 而
 * DetailPane 要跑测试得拖上 jsdom + React。这是纯字符串函数, 单独放能直接测。
 *
 * # 远程图为什么默认不加载
 *
 * 邮件里的外链图片就是跟踪像素: 你一打开, 发件人就知道你什么时候看的、看了
 * 几次、从哪个网络看的 —— 不需要你点任何东西。主流邮件客户端默认都拦。
 * 对一个替员工处理邮件的产品来说, 这个默认值不该反过来。
 *
 * 内嵌图 (cid:) 不受影响: 它的字节在邮件里, 后端已经换成 data: URL 了
 * (见 rfc822_util.embed_inline_images), 不产生任何网络请求。
 *
 * # 为什么用 CSP 而不是正则改写 <img>
 *
 * 想过把 src="http…" 逐个替换掉, 但那个方案的失败模式是错的:
 *
 *     正则漏一个 → 真的发出请求, 跟踪像素照样生效, 而且没人看得出来
 *     CSP 漏一个 → 不存在, 浏览器按协议拦
 *
 * 而且外链不止 <img>: CSS 的 background:url(…)、@font-face、<video poster>
 * 都会发请求。正则要一个个追, CSP 一行全覆盖。
 *
 * srcdoc 的 iframe 会继承父页面 CSP, 文档自己的 meta CSP 再叠加一层, 取交集。
 * 所以这里写的 meta 只会更严, 不会把父页面的限制放松。
 */

/** 邮件 HTML 里有没有会往外发请求的东西, 有几处。
 *
 *  只用来决定「显示图片」那条提示要不要出现 —— **不用来做拦截**。拦截是
 *  CSP 的事; 这里数漏了顶多是少提示一句, 不会漏出去一个请求。
 */
export function countRemoteRefs(html: string): number {
  if (!html) return 0;
  const patterns = [
    /<img\b[^>]*\bsrc\s*=\s*["']?https?:\/\//gi,
    /\burl\(\s*["']?https?:\/\//gi,          // CSS background / @font-face
    /<video\b[^>]*\bposter\s*=\s*["']?https?:\/\//gi,
  ];
  return patterns.reduce((n, re) => n + (html.match(re)?.length ?? 0), 0);
}

/** 拦下一切外部加载。
 *
 *  `img-src data:` —— 只放行内嵌图 (后端已转成 data:)。
 *  `script-src 'none'` —— 顺手补上的。DetailPane 当年为了拿 contentDocument
 *    砍掉了 sandbox 属性, 注释里自己记着"真要严格 XSS 防御应该 sanitize 砍
 *    script tag"。一行 CSP 比引 dompurify 便宜, 而且更彻底 (内联 handler 也拦)。
 *    砍 sandbox 换来的 contentDocument 访问不受影响 —— 那是父页面的 JS。
 */
const BLOCK_ALL_REMOTE =
  '<meta http-equiv="Content-Security-Policy" content="' +
  "default-src 'none'; " +
  "img-src data:; " +
  "style-src 'unsafe-inline'; " +
  "font-src data:; " +
  "script-src 'none'; " +
  "form-action 'none'" +
  '">';

/** 只拦脚本, 放行图片。「显示图片」之后走这条。
 *
 *  注意父页面 CSP 仍然生效: img-src 不含 http:, 所以纯 http 的图即使点了
 *  「显示图片」也还是出不来 —— 那是混合内容, 不为一张图松。这种情况下提示
 *  条不会消失, 员工至少知道是被挡了, 而不是以为鲶鱼坏了。
 */
const BLOCK_SCRIPTS_ONLY =
  '<meta http-equiv="Content-Security-Policy" content="' +
  "default-src 'none'; " +
  "img-src data: https:; " +
  "style-src 'unsafe-inline'; " +
  "font-src data: https:; " +
  "script-src 'none'; " +
  "form-action 'none'" +
  '">';

/** 注入的默认 CSS。
 *
 * P3.5.38.4 (6/18 鸿波 catch '内容太靠左, 部分内容超出左边界'):
 * iframe 默认 body margin 8px, 但邮件 HTML 常用 margin:0 reset + 自定 layout,
 * 内容紧贴 iframe edge; 部分 newsletter 用大 fixed-width container + negative
 * margin, 在窄 iframe 内会被裁掉左侧。
 * 邮件原 <style> 后置覆盖这些是预期 —— 这只是邮件没写 padding 时的兜底。
 */
const DEFAULT_CSS = `<style>
html,body{margin:0;padding:0;background:#fff;color:#000;word-wrap:break-word;overflow-wrap:break-word;}
body{padding:16px;box-sizing:border-box;}
img,table,video,iframe{max-width:100% !important;height:auto;}
pre,code{white-space:pre-wrap;word-break:break-word;}
a{word-break:break-all;}
</style>`;

/** 附件占位小标签 (后端 rfc822_util.replace_attachment_objects 给的 class)。 */
const CHIP_CSS = `<style>
.catfish-attach-chip{display:inline-block;padding:3px 10px;margin:4px 6px 4px 0;border:1px solid #DCE5E6;border-radius:6px;background:#F3F6F6;color:#0E6169;font-size:12px;}
</style>`;

/** 跟随鲶鱼深浅色的那套。色值抄自 styles/tokens.css (iframe 是另一个文档, 拿不到
 *  父页面的 CSS 变量; emailSrcDoc.test.ts 钉着两边一致)。浅色下仍是白底, 跟以前一样。 */
export const THEME_DARK = { bg: "#0C1317", text: "#ECEAE2", link: "#22A4B0", border: "#2B3E47", chipBg: "#19262D" };
const THEMED_CSS = `<meta name="color-scheme" content="light dark"><style>
html,body{color:#14232A;}
@media (prefers-color-scheme: dark){
html,body{background:${THEME_DARK.bg};color:${THEME_DARK.text};}
a{color:${THEME_DARK.link};}
blockquote,hr{border-color:${THEME_DARK.border};}
.catfish-attach-chip{background:${THEME_DARK.chipBg};border-color:${THEME_DARK.border};color:${THEME_DARK.link};}
}
</style>`;

type Rgb = [number, number, number];

/** CSS 颜色值 → RGB; "keep" = 不带具体颜色 (inherit 之类); null = 认不出。 */
function parseColor(raw: string): Rgb | "keep" | null {
  const v = raw.replace(/!important/i, "").trim().toLowerCase();
  if (/^(inherit|initial|unset|currentcolor|transparent|none)$/.test(v)) return "keep";
  if (v === "black") return [0, 0, 0];
  if (v === "white") return [255, 255, 255];
  let m = v.match(/^#([0-9a-f]{3})$/);
  if (m) return [...m[1]].map((c) => parseInt(c + c, 16)) as Rgb;
  m = v.match(/^#([0-9a-f]{6})(?:[0-9a-f]{2})?$/);
  if (m) return [0, 2, 4].map((i) => parseInt(m![1].slice(i, i + 2), 16)) as Rgb;
  m = v.match(/^rgba?\(\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*(?:[,/]\s*[\d.]+%?\s*)?\)$/);
  if (m) return [Number(m[1]), Number(m[2]), Number(m[3])];
  return null;
}

const luminance = ([r, g, b]: Rgb) => (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;

/** 文字颜色: "strip" = 近黑 (就是"默认黑字", 去掉后跟主题走); "keep" = 深底上也看得清;
 *  null = 深底上看不清或认不出 → 这封不跟主题。 */
function judgeTextColor(raw: string): "strip" | "keep" | null {
  const c = parseColor(raw);
  if (c === null) return null;
  if (c === "keep") return "keep";
  if (Math.max(...c) <= 0x40) return "strip";
  return luminance(c) >= 0.45 ? "keep" : null;
}

/** 背景: 白底去掉; 别的颜色 / 图片 → 这封有自己的版式, 不跟主题。 */
function judgeBackground(raw: string): "strip" | "keep" | null {
  if (/url\(/i.test(raw)) return null;
  const c = parseColor(raw);
  if (c === null) return null;
  if (c === "keep") return "keep";
  return Math.min(...c) >= 0xf0 ? "strip" : null;
}

/** 一封「只有字」的 HTML 邮件 (Apple Mail / Outlook 写的普通信) → 去掉写死的黑字白底,
 *  好让它跟随鲶鱼的深浅色; 有自己版式的 (表格 / 图片 / 样式表 / 底色 / 深色字) 返回 null,
 *  照旧白底显示。
 *
 *  判不准一律返回 null: 白底只是不好看, 深底配深字是看不见。
 */
export function themeableHtml(html: string): string | null {
  if (!html) return null;
  if (/<(style|table|img|link|svg|video|canvas)\b/i.test(html)) return null;
  if (/\sbackground\s*=/i.test(html)) return null;
  let ok = true;
  const cleanStyle = (css: string): string =>
    css
      .split(";")
      .map((decl) => {
        const i = decl.indexOf(":");
        if (i < 0) return decl;
        const prop = decl.slice(0, i).trim().toLowerCase();
        const value = decl.slice(i + 1);
        let verdict: "strip" | "keep" | null = "keep";
        if (prop === "color") verdict = judgeTextColor(value);
        else if (prop === "background-color" || prop === "background") verdict = judgeBackground(value);
        else if (prop === "background-image") verdict = /^\s*none\s*$/i.test(value) ? "keep" : null;
        if (verdict === null) ok = false;
        return verdict === "strip" ? "" : decl;
      })
      .filter((d) => d.trim())
      .join(";");
  let out = html.replace(/(\sstyle\s*=\s*)(["'])([\s\S]*?)\2/gi, (_m, pre, q, css) => `${pre}${q}${cleanStyle(css)}${q}`);
  // <font color> / <body text> 写的字色, <body bgcolor> 写的底色
  out = out.replace(/\s(color|text|bgcolor)\s*=\s*(?:(["'])(.*?)\2|([^\s>]+))/gi, (m, attr, _q, quoted, bare) => {
    const value = quoted ?? bare ?? "";
    const verdict = attr.toLowerCase() === "bgcolor" ? judgeBackground(value) : judgeTextColor(value);
    if (verdict === null) ok = false;
    return verdict === "strip" ? "" : m;
  });
  return ok ? out : null;
}

/** 正文最前面的空行 (Apple Mail 发出的信开头常有一个空 div) 去掉, 免得正文上面空一截。 */
function trimLeadingBlank(html: string): string {
  const blank = String.raw`(?:\s|&nbsp;|<br\s*/?>|<div[^>]*>(?:\s|&nbsp;|<br\s*/?>)*</div>)+`;
  const afterBody = new RegExp(String.raw`(<body\b[^>]*>)` + blank, "i");
  if (afterBody.test(html)) return html.replace(afterBody, "$1");
  return /<body\b/i.test(html) ? html : html.replace(new RegExp("^" + blank, "i"), "");
}

export function buildEmailSrcDoc(
  bodyHtml: string,
  opts: { allowRemote?: boolean } = {},
): string {
  const csp = opts.allowRemote ? BLOCK_SCRIPTS_ONLY : BLOCK_ALL_REMOTE;
  const trimmed = trimLeadingBlank(bodyHtml);
  const themed = themeableHtml(trimmed);
  // meta CSP 必须在任何会发请求的东西之前 —— 浏览器是边解析边加载的,
  // 排在图片后面就等于没拦。
  return `${csp}<base target="_blank">${DEFAULT_CSS}${CHIP_CSS}${themed !== null ? THEMED_CSS : ""}${themed ?? trimmed}`;
}
