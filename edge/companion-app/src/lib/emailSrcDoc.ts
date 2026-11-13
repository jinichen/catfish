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

/** 邮件里实际会写的颜色名 (Outlook 爱写 windowtext / window)。认不出的名字 → 这封不跟主题。 */
const NAMED: Record<string, Rgb> = {
  black: [0, 0, 0], windowtext: [0, 0, 0], white: [255, 255, 255], window: [255, 255, 255],
  red: [255, 0, 0], blue: [0, 0, 255], navy: [0, 0, 128], green: [0, 128, 0], gray: [128, 128, 128],
  grey: [128, 128, 128], silver: [192, 192, 192], yellow: [255, 255, 0], orange: [255, 165, 0],
  purple: [128, 0, 128], maroon: [128, 0, 0], teal: [0, 128, 128],
};

/** CSS 颜色值 → RGB; "keep" = 不带具体颜色 (inherit / 透明); null = 认不出。 */
function parseColor(raw: string): Rgb | "keep" | null {
  const v = raw.replace(/!important/i, "").trim().toLowerCase();
  if (/^(inherit|initial|unset|currentcolor|transparent|none)$/.test(v)) return "keep";
  if (NAMED[v]) return NAMED[v];
  let m = v.match(/^#([0-9a-f]{3})$/);
  if (m) return [...m[1]].map((c) => parseInt(c + c, 16)) as Rgb;
  m = v.match(/^#([0-9a-f]{6})(?:[0-9a-f]{2})?$/);
  if (m) return [0, 2, 4].map((i) => parseInt(m![1].slice(i, i + 2), 16)) as Rgb;
  m = v.match(/^rgba?\(\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*(?:[,/]\s*([\d.]+%?)\s*)?\)$/);
  if (m) return m[4] !== undefined && parseFloat(m[4]) === 0 ? "keep" : [Number(m[1]), Number(m[2]), Number(m[3])];
  return null;
}

const luminance = ([r, g, b]: Rgb) => (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
const hex = (c: Rgb) => "#" + c.map((n) => Math.round(n).toString(16).padStart(2, "0")).join("");

/** 深色字提亮到深底上看得清 (往白色靠, 色相不变): 蓝 #0000ff → 浅蓝。 */
function lighten(c: Rgb): Rgb {
  let out = c;
  for (let t = 0.3; luminance(out) < 0.55 && t <= 1; t += 0.1) {
    out = c.map((n) => n + (255 - n) * t) as Rgb;
  }
  return out;
}

/** 浅色底上的字用的深色 (= 浅色主题的正文色)。 */
const INK = "#14232A";

type Verdict = { kind: "keep" } | { kind: "strip" } | { kind: "replace"; value: string } | null;

/** 文字颜色。近黑 = "默认黑字", 去掉后跟主题走; 别的深色字深底上看不清, 换成
 *  light-dark(原色, 提亮后的) —— 浅色主题下还是原色; 认不出 → null。 */
function judgeTextColor(raw: string): Verdict {
  const c = parseColor(raw);
  if (c === null) return null;
  if (c === "keep") return { kind: "keep" };
  if (Math.max(...c) <= 0x40) return { kind: "strip" };
  if (luminance(c) >= 0.45) return { kind: "keep" };
  return { kind: "replace", value: `light-dark(${hex(c)}, ${hex(lighten(c))})` };
}

/** 背景。白底去掉 (跟主题走); 浅色底 (高亮 / 表头) 留着, 同时把字定成深色 ——
 *  不然深色主题的浅字落在浅底上; 深色底原样; 背景图 / 认不出 → null。 */
function judgeBackground(raw: string): Verdict & { ink?: boolean } | null {
  if (/url\(/i.test(raw)) return null;
  const plain = raw.replace(/!important/i, "").trim();
  let c = parseColor(plain);
  if (c === null) {
    // `background: #fff none repeat scroll 0 0` 这种简写: 找里面那个颜色
    const tokens = plain.match(/rgba?\([^)]*\)|[^\s]+/g) ?? [];
    for (const t of tokens) {
      const parsed = parseColor(t);
      if (parsed !== null && parsed !== "keep") { c = parsed; break; }
      if (parsed === "keep") c = "keep";
    }
  }
  if (c === null) return null;
  if (c === "keep") return { kind: "keep" };
  if (Math.min(...c) >= 0xf0) return { kind: "strip" };
  return luminance(c) >= 0.6 ? { kind: "keep", ink: true } : { kind: "keep" };
}

const DECL = /(^|[;{\s])(color|background-color|background-image|background)(\s*:\s*)([^;{}]*)/gi;

/** 一段 CSS (style 属性的值 / <style> 块的内容) 里的字色和底色按上面的规则改写。
 *  返回 null = 里面有判不了的东西。 */
function rewriteCss(css: string): string | null {
  let ok = true;
  const out = css.replace(DECL, (m, pre: string, prop: string, sep: string, value: string) => {
    const p = prop.toLowerCase();
    if (p === "background-image") {
      if (!/^\s*none\s*(!important)?\s*$/i.test(value)) ok = false;
      return m;
    }
    const verdict = p === "color" ? judgeTextColor(value) : judgeBackground(value);
    if (verdict === null) { ok = false; return m; }
    if (verdict.kind === "strip") return pre;
    if (verdict.kind === "replace") return `${pre}${prop}${sep}${verdict.value}`;
    return (verdict as { ink?: boolean }).ink ? `${m};color:${INK}` : m;
  });
  return ok ? out : null;
}

/** 让一封 HTML 邮件跟随鲶鱼的深浅色: 写死的黑字白底去掉, 深色字提亮, 浅色底上的字
 *  定成深色。返回 null = 这封判不了 (背景图 / 认不出的颜色写法), 照旧白底显示。
 *
 *  11/13 第一版见到样式表 / 表格 / 图片就整封放弃 —— 而 Outlook、Foxmail 写的普通信
 *  几乎都带一段样式表, 真机收件箱 25 封里 23 封因此还是白底。现在只看颜色本身。
 *
 *  判不准一律返回 null: 白底只是不好看, 深底配深字是看不见。
 */
export function themeableHtml(html: string): string | null {
  if (!html) return null;
  if (/\sbackground\s*=/i.test(html)) return null;   // <body background="图">
  let ok = true;
  const css = (text: string): string => {
    const r = rewriteCss(text);
    if (r === null) { ok = false; return text; }
    return r;
  };
  let out = html.replace(/(<style\b[^>]*>)([\s\S]*?)(<\/style>)/gi, (_m, open, body, close) => `${open}${css(body)}${close}`);
  out = out.replace(/(\sstyle\s*=\s*)(["'])([\s\S]*?)\2/gi, (_m, pre, q, body) => `${pre}${q}${css(body)}${q}`);
  // <font color> / <body text> 写的字色, <td bgcolor> 写的底色 (属性里放不了 light-dark, 只能去或留)
  out = out.replace(/\s(color|text|bgcolor)\s*=\s*(?:(["'])(.*?)\2|([^\s>]+))/gi, (m, attr, _q, quoted, bare) => {
    const value = quoted ?? bare ?? "";
    if (attr.toLowerCase() === "bgcolor") {
      const v = judgeBackground(value);
      if (v === null || (v as { ink?: boolean }).ink) { ok = false; return m; }
      return v.kind === "strip" ? "" : m;
    }
    const v = judgeTextColor(value);
    if (v === null) { ok = false; return m; }
    return v.kind === "keep" ? m : "";
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
