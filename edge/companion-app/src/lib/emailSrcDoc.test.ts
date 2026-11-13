/** 邮件正文 srcDoc —— 拦截靠 CSP, 不靠正则。
 *
 * 这个文件钉的是"拦得住"和"顺序对", 两条都是看不出来的:
 * 跟踪像素发出去了界面上一个字都不会变, 发件人那头却已经知道你打开了。
 */
import { describe, expect, it } from "vitest";

import { buildEmailSrcDoc, countRemoteRefs } from "./emailSrcDoc";

const REMOTE_IMG = '<img src="https://tracker.example.com/pixel.gif?id=abc">';
const INLINE_IMG = '<img src="data:image/gif;base64,R0lGOD">';

describe("countRemoteRefs", () => {
  it("数得出 <img> 外链", () => {
    expect(countRemoteRefs(REMOTE_IMG)).toBe(1);
  });

  it("CSS background 里的外链也算 —— 跟踪像素不止藏在 <img> 里", () => {
    expect(
      countRemoteRefs('<div style="background:url(http://t.example.com/p.gif)">'),
    ).toBe(1);
  });

  it("内嵌图和 data: 不算", () => {
    expect(countRemoteRefs(INLINE_IMG)).toBe(0);
    expect(countRemoteRefs("<p>纯文字通知</p>")).toBe(0);
  });

  it("空输入不炸", () => {
    expect(countRemoteRefs("")).toBe(0);
  });
});

describe("buildEmailSrcDoc", () => {
  it("默认拦死一切外部加载", () => {
    const doc = buildEmailSrcDoc(REMOTE_IMG);
    expect(doc).toContain("Content-Security-Policy");
    expect(doc).toContain("img-src data:");
    expect(doc).not.toContain("img-src data: https:");
  });

  it("CSP 必须排在正文前面", () => {
    // 浏览器边解析边加载。meta 排在图片后面就等于没拦 —— 而且界面上
    // 完全看不出区别, 只有发件人那头知道。
    const doc = buildEmailSrcDoc(REMOTE_IMG);
    expect(doc.indexOf("Content-Security-Policy")).toBeLessThan(
      doc.indexOf("<img"),
    );
  });

  it("「显示图片」之后放行 https, 但仍然不放行脚本", () => {
    const doc = buildEmailSrcDoc(REMOTE_IMG, { allowRemote: true });
    expect(doc).toContain("img-src data: https:");
    expect(doc).toContain("script-src 'none'");
  });

  it("两种模式都不放行脚本", () => {
    // DetailPane 为了拿 contentDocument 砍掉了 sandbox, 注释里自己记着这是
    // 个已知缺口。这条守着它别再敞开。
    expect(buildEmailSrcDoc("x")).toContain("script-src 'none'");
    expect(buildEmailSrcDoc("x", { allowRemote: true })).toContain(
      "script-src 'none'",
    );
  });

  it("正文原样带过去, 不做任何改写", () => {
    const html = '<img width="600" src="cid:x" style="border:0">';
    expect(buildEmailSrcDoc(html)).toContain(html);
  });

  it("默认 CSS 在正文之前 —— 邮件自己的 style 要能覆盖它", () => {
    const doc = buildEmailSrcDoc("<p>正文</p>");
    expect(doc.indexOf("<style>")).toBeLessThan(doc.indexOf("<p>正文</p>"));
  });
});

// 11/13: 只有字的信跟随深浅色; 判不准就照旧白底 (深底配深字是看不见)。
import { readFileSync } from "node:fs";
import { THEME_DARK, themeableHtml } from "./emailSrcDoc";

// Apple Mail 写的普通信的真实结构 (内容是假的): 每段都写死黑字
const APPLE_PLAIN =
  '<html><head></head><body style="overflow-wrap: break-word;"><div class="Apple-Mail-URLShareUserContentTopClass"><br></div>' +
  '<p style="margin: 0px"><font face="PingFang SC" size="3" color="#000000" style="font: 12.0px \'PingFang SC\'; color: #000000; -webkit-text-stroke: 0px #000000">张工：</font></p>' +
  '<p style="margin: 0px; font: 12.0px Helvetica; color: rgb(0, 0, 0); background-color: #ffffff">附件是本期材料。</p></body></html>';

describe("themeableHtml", () => {
  it("普通信: 去掉写死的黑字白底, 别的样式留着", () => {
    const out = themeableHtml(APPLE_PLAIN)!;
    expect(out).not.toBeNull();
    expect(out).not.toMatch(/(?<![-\w])color\s*[:=]/i);
    expect(out).not.toContain("background-color");
    expect(out).toContain("font: 12.0px 'PingFang SC'");
    expect(out).toContain("-webkit-text-stroke: 0px #000000");
    expect(out).toContain("张工：");
  });

  it("深底上看得清的强调色留着 (红字提醒)", () => {
    const out = themeableHtml('<p>请于 <span style="color: #ff4d4f">周五前</span> 回复</p>');
    expect(out).toContain("color: #ff4d4f");
  });

  it("带样式表 / 表格 / 图片的普通信也跟主题 (Outlook、Foxmail 写的信几乎都带样式表)", () => {
    const html =
      "<style>p.MsoNormal{margin:0;color:windowtext;background:window} a:link{color:blue}</style>" +
      '<table><tr><td><p class="MsoNormal">你好</p></td></tr></table><img src="data:image/png;base64,AA">';
    const out = themeableHtml(html)!;
    expect(out).not.toBeNull();
    expect(out).not.toContain("windowtext");
    expect(out).not.toMatch(/background\s*:/);
    expect(out).toContain("margin:0");
    // 蓝色链接: 浅色主题还是蓝, 深色主题提亮
    expect(out).toMatch(/a:link\{color:light-dark\(#0000ff, #[0-9a-f]{6}\)\}/);
    expect(buildEmailSrcDoc(html)).toContain("prefers-color-scheme");
  });

  it("深色字提亮后在深底上够亮", () => {
    const out = themeableHtml('<p style="color: #00008b">x</p>')!;
    const lit = out.match(/light-dark\(#00008b, (#[0-9a-f]{6})\)/)![1];
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(lit.slice(i, i + 2), 16));
    expect((0.2126 * r + 0.7152 * g + 0.0722 * b) / 255).toBeGreaterThanOrEqual(0.55);
  });

  it("浅色底 (高亮 / 引用头) 留着, 上面的字定成深色", () => {
    const out = themeableHtml('<span style="background-color: rgb(255, 250, 165)">重点</span>')!;
    expect(out).toContain("background-color: rgb(255, 250, 165);color:#14232A");
  });

  it("深色底原样留着; 透明底不算底色", () => {
    expect(themeableHtml('<td style="background:#1a73e8;color:#ffffff">x</td>')).toContain("background:#1a73e8;color:#ffffff");
    expect(themeableHtml('<p style="background: rgba(0, 0, 0, 0)">x</p>')).toContain("rgba(0, 0, 0, 0)");
  });

  it.each([
    ["背景图", '<div style="background: url(data:image/png;base64,AA)">x</div>'],
    ["样式表里的背景图", "<style>.hero{background-image:url(data:image/png;base64,AA)}</style><p>x</p>"],
    ["body background 属性", '<body background="cid:bg"><p>x</p></body>'],
    ["认不出的颜色写法", '<p style="color: hsl(240 100% 20%)">x</p>'],
    ["浅色 bgcolor 属性 (没法同时定字色)", '<td bgcolor="#ffffcc">x</td>'],
  ])("%s → 不跟主题, 照旧白底", (_name, html) => {
    expect(themeableHtml(html)).toBeNull();
    expect(buildEmailSrcDoc(html)).not.toContain("prefers-color-scheme");
  });

  it("跟主题的信: 文档里带深色那套, 正文最前面的空行去掉", () => {
    const doc = buildEmailSrcDoc(APPLE_PLAIN);
    expect(doc).toContain("prefers-color-scheme: dark");
    expect(doc).not.toContain("Apple-Mail-URLShareUserContentTopClass");
    expect(doc.indexOf("Content-Security-Policy")).toBeLessThan(doc.indexOf("<style>"));
  });

  it("深色色值跟 tokens.css 一致 (iframe 拿不到父页面的变量, 只能抄一份)", () => {
    const css = readFileSync(new URL("../styles/tokens.css", import.meta.url), "utf8");
    const dark = css.slice(css.indexOf("@media (prefers-color-scheme: dark)"));
    const token = (name: string) => dark.match(new RegExp(`--catfish-${name}:\\s*(#[0-9A-Fa-f]{6})`))![1].toUpperCase();
    expect(THEME_DARK.bg).toBe(token("bg"));
    expect(THEME_DARK.text).toBe(token("text"));
    expect(THEME_DARK.link).toBe(token("cyan"));
    expect(THEME_DARK.border).toBe(token("border"));
    expect(THEME_DARK.chipBg).toBe(token("bg-elevated"));
  });
});
