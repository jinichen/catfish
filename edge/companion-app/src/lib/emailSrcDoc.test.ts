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
