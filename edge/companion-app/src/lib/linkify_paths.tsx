/**
 * BL-COMPANION-FILE-PATH-CLICKABLE (5/19):
 *
 * 在 chat markdown 渲染时, 把 inline 出现的绝对文件路径 (eg
 *   "周报已生成: /Users/chenhongbo/.catfish/output/2026-05-19/...xlsx")
 * 替换成可点击的 <a>. 点击调 Tauri shell `openFile` 用默认 app 打开,
 * cmd+click 走系统 open. 兼容路径里有空格 / 中文 (catfish skill 输出常有,
 * 比如 "周报 - 鸿波 -20260519.xlsx").
 *
 * 为什么不复用 path_detect.ts 的 UNIX_PATH_RE:
 *   - 那个正则不允许路径里有空格 (`[^\s...]`), 但 skill 输出常带空格.
 *   - 那个正则给 FilePill 用 (整段提取去重), 这里要 inline 原位替换,
 *     需要保留 match 的 index + 长度信息.
 *
 * 设计:
 *   - 用更宽松的字符集 (允许空格 / 中文), 非贪婪到 `.ext` 收住.
 *   - 仅识别 macOS 常见根 (`/Users`, `/var`, `/tmp`, `/opt`, `/private`)
 *     + `~/` home 缩写. 不识别 Windows 路径 (Companion 仅 macOS).
 *   - 扩展名白名单跟 path_detect.ts 对齐 (员工常用办公格式).
 *   - 路径不能跨行 — 换行符强制截断.
 *   - URL (http:// / https:// / file://) 不识别 — 前置字符黑名单防误伤.
 *   - 渲染层: linkifyChildren(children) 递归走 React children, 仅替换
 *     string 节点, 不动 <code> / <a> / <strong> 等已结构化的内容
 *     (那些是 LLM 自己写了 markdown, 别二次解析).
 */

import React from "react";
import { openFile } from "./tauri";
import { basename, fileEmoji } from "./path_detect";

// 跟 path_detect.ts SUPPORTED_EXT 对齐 (单一来源会更稳, 但跨模块 import
// 一个 const tuple 太啰嗦, 这里手抄一份并加注释).
const EXT_PATTERN = [
  "docx", "xlsx", "pptx", "doc", "xls", "ppt",
  "csv", "tsv", "json", "yaml", "yml", "txt", "md", "log",
  "pdf", "png", "jpg", "jpeg", "gif", "svg", "webp",
  "zip", "tar", "gz",
  "py", "js", "ts", "tsx", "rs", "go", "html",
].join("|");

/**
 * inline 路径识别正则.
 *
 * 关键边界:
 *   - 前置: 前一个字符不能是 字母/数字/`/`/`~`/`:` —— 防止
 *       1) URL 的 `https:` / `file:` 被吃掉
 *       2) `~/foo.docx` 里的 `/foo.docx` 被独立截
 *       3) `pkg/path/foo.go` import 路径误识别
 *   - 主体: 可选 `~`, 再 `/` 根, 根 segment 必须是
 *     Users/var/tmp/opt/private 之一 (其他位置不太可能是用户文件),
 *     然后非贪婪吃直到 `.ext`. 中间允许空格 + 中文 + Unicode 字符.
 *   - 路径字符黑名单: 换行符 + 引号 + 反引号 + 尖括号 + 大括号 + 竖线 —
 *     这些是 markdown / JSON / log 里的明显边界字符, 不会出现在文件名里.
 *   - 后置: `.ext` 后不能跟字母数字 (防 `.docx2` / `.pdfa` 这种伪扩展).
 */
const INLINE_PATH_RE = new RegExp(
  "(?<![A-Za-z0-9/~:])" +
    "(~?/(?:Users|var|tmp|opt|private)/[^\\n\\r\"'`<>{}|]+?\\.(?:" +
    EXT_PATTERN +
    "))" +
    "(?![A-Za-z0-9])",
  "gi",
);

/** 内部使用: 一个 string 拆成 (text|FilePathLink) 节点序列. */
function splitTextWithLinks(text: string, keyPrefix: string): React.ReactNode[] {
  if (!text) return [];
  // 没匹配快速路径 (chat 大多消息没文件路径, 避免每次都建 RegExp 状态机)
  INLINE_PATH_RE.lastIndex = 0;
  if (!INLINE_PATH_RE.test(text)) return [text];

  INLINE_PATH_RE.lastIndex = 0;
  const nodes: React.ReactNode[] = [];
  let lastIdx = 0;
  let match: RegExpExecArray | null;
  let count = 0;

  while ((match = INLINE_PATH_RE.exec(text)) !== null) {
    const path = match[1];
    const start = match.index;
    const end = start + path.length;

    // 一些显然不对的兜底剔除 (跟 extractFilePaths 一致, 防误识别)
    if (path.endsWith("..") || path.includes("<") || path.includes(">")) {
      continue;
    }

    if (start > lastIdx) {
      nodes.push(text.slice(lastIdx, start));
    }
    nodes.push(
      <FilePathLink key={`${keyPrefix}-${count}-${start}`} path={path} />,
    );
    lastIdx = end;
    count++;

    // 极端安全: 一段文字识别 >50 个路径基本是误匹配, 退出避免渲染卡死
    if (count > 50) break;
  }
  if (lastIdx < text.length) {
    nodes.push(text.slice(lastIdx));
  }
  return nodes;
}

/**
 * 递归把 React children 里的 string 节点替换成 (string + FilePathLink) 序列.
 *
 * 不递归进的节点 (LLM 已经结构化的, 二次解析会乱):
 *   - <a>: 已经是链接了
 *   - <code> / <pre>: 代码块 / inline code, 路径在代码里就当代码看
 *
 * 其他容器 (<p> / <strong> / <em> / <li> / <td> 等) 是 markdown
 * paragraph-level, 内部 string 安全替换.
 */
export function linkifyChildren(
  children: React.ReactNode,
  keyPrefix = "lp",
): React.ReactNode {
  if (children == null || typeof children === "boolean") return children;

  if (typeof children === "string") {
    const parts = splitTextWithLinks(children, keyPrefix);
    if (parts.length <= 1) return children;
    return <>{parts}</>;
  }

  if (typeof children === "number") return children;

  if (Array.isArray(children)) {
    return children.map((c, i) =>
      <React.Fragment key={`${keyPrefix}-f-${i}`}>
        {linkifyChildren(c, `${keyPrefix}-${i}`)}
      </React.Fragment>
    );
  }

  if (React.isValidElement(children)) {
    const el = children as React.ReactElement<{ children?: React.ReactNode }>;
    const type = el.type;
    // 跳过 code / pre / a — 它们要么是代码要么已经是链接
    if (type === "code" || type === "pre" || type === "a") {
      return el;
    }
    // 其他元素递归处理 children
    const inner = el.props?.children;
    if (inner == null) return el;
    return React.cloneElement(el, undefined, linkifyChildren(inner, keyPrefix));
  }

  return children;
}

/** 内联文件链接 — 蓝色下划线 + 文件 emoji + basename. hover 显示完整路径.
 *
 * BL-COMPANION-FILE-PATH-CLICKABLE 修 (5/19 晚): **不能用 `<a href="file://...">`**.
 *
 * 真根因 (用户实测单击没反应): Tauri WKWebView 对 `<a href="file://...">` 走
 * `WKNavigationDelegate` 的 navigation policy 分支 — 即使 onClick `preventDefault`,
 * CSP `default-src 'self'` 也会把 file:// 跳转静默 cancel; 但 WKWebView 的 navigation
 * 处理路径会在 JS onClick 触发前/同时抢占, 导致 React click 走不到 invoke, 或者走到了
 * 但 webview 当下正在 "决定是否导航" 状态, IPC 被丢. FilePill 的 `<button>` 没这层
 * navigation 分发, 所以那边 work.
 *
 * 修法: 改用 `<span role="button">` (语义按钮, 无 href = 无 navigation 分发). 仍保留:
 *   - 蓝色下划线视觉
 *   - cursor: pointer
 *   - 键盘可达 (tabIndex + Enter/Space 触发)
 *   - hover title 显示完整路径
 *   - cmd+click 不分流 (保持原决定: 一致体验比智能猜好, reveal 走 FilePill 副按钮)
 */
function FilePathLink({ path }: { path: string }) {
  const trigger = () => {
    void (async () => {
      try {
        await openFile(path);
      } catch (err) {
        // 失败不弹窗 — Tauri webview alert 体验差.
        // console.warn 让员工开 devtools 能看到, 不打断 chat 流.
        // eslint-disable-next-line no-console
        console.warn("[linkifyPaths] openFile failed", path, err);
      }
    })();
  };

  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    trigger();
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // 键盘可达: Enter / Space 等价单击 (a11y, 跟 native <a> 一致)
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      e.stopPropagation();
      trigger();
    }
  };

  return (
    <span
      role="button"
      tabIndex={0}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      title={path}
      style={{
        color: "var(--catfish-cyan-dim)",
        textDecoration: "underline",
        cursor: "pointer",
        wordBreak: "break-all",
      }}
    >
      <span aria-hidden style={{ marginRight: 2 }}>{fileEmoji(path)}</span>
      {basename(path)}
    </span>
  );
}

/** export 给单测用. */
export const __test__ = { INLINE_PATH_RE, splitTextWithLinks };
