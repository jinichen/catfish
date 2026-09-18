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

export function buildEmailSrcDoc(
  bodyHtml: string,
  opts: { allowRemote?: boolean } = {},
): string {
  const csp = opts.allowRemote ? BLOCK_SCRIPTS_ONLY : BLOCK_ALL_REMOTE;
  // meta CSP 必须在任何会发请求的东西之前 —— 浏览器是边解析边加载的,
  // 排在图片后面就等于没拦。
  return `${csp}<base target="_blank">${DEFAULT_CSS}${bodyHtml}`;
}
