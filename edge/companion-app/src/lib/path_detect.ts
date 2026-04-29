/**
 * 文件路径自动识别 —— 给 ChatToolCall / ChatMessage 用.
 *
 * 设计:
 *   - 只识别**绝对路径**, 避免误匹配 markdown / shell 命令片段
 *   - 只识别员工常见办公格式 + 一些通用文件扩展名
 *   - 同一段文本可能出现多次同一路径 → 去重保持第一次出现顺序
 *   - 针对 macOS / Linux / Windows 三套分别匹配
 *
 * 不在这里判断"文件是否存在" —— 那是后端 reveal_in_finder 的事;
 * 前端只做正则提取, 错的路径点了报错就报错, 不是关键路径.
 */

// 员工实际拿得出去的文件类型. 加新格式时三处注意:
//   1. 这里
//   2. emoji map (FILE_EMOJI)
//   3. is_blocked_path Rust 端 (file.rs)
const SUPPORTED_EXT = [
  // Office
  "docx",
  "xlsx",
  "pptx",
  "doc",
  "xls",
  "ppt",
  // 数据 / 文本
  "csv",
  "tsv",
  "json",
  "yaml",
  "yml",
  "txt",
  "md",
  "log",
  // PDF / 图片
  "pdf",
  "png",
  "jpg",
  "jpeg",
  "gif",
  "svg",
  "webp",
  // 压缩
  "zip",
  "tar",
  "gz",
  // 代码 (skill 偶尔生成)
  "py",
  "js",
  "ts",
  "tsx",
  "rs",
  "go",
  "html",
] as const;

const EXT_PATTERN = SUPPORTED_EXT.join("|");

/**
 * 匹配规则:
 *   - macOS / Linux: `/<segment>/.../<file>.<ext>` 绝对路径,
 *     或 `~/<segment>/.../<file>.<ext>` 波浪号 home 路径 (Rust 端会展开).
 *   - Windows: `<drive>:\path\to\file.ext` —— C: / D: 等 + 反斜杠
 *
 * 因为 tool result 大量是 JSON, 路径会被双引号包住, 所以路径里也不允许 " 出现.
 * 也排除回车换行避免跨行.
 *
 * 关键边界: `~/Desktop/x.docx` 必须整体匹配, 不能让 `/Desktop/x.docx` 被另一条
 * Unix 规则截出来 — 那是错的相对片段, 后端展开会失败. 所以前导字符黑名单要包含 `~`.
 */
const UNIX_PATH_RE = new RegExp(
  // 前置保护: 前一个字符不是 字母/数字/斜杠/波浪号/冒号 — 防止
  //   1) 把 url ('https:' / 'file:') 当文件路径
  //   2) 把 `~/foo/bar.docx` 里的 `/foo/bar.docx` 截出来当独立路径
  //   3) `pkg/path/foo.go` 这种 import 路径
  "(?<![A-Za-z0-9/~:])" +
    // 主体: 可选 `~`, 然后 / 开头, 至少 1 段 segment, 最后 .<ext>.
    // `/` 后第一个字符不能是 `/` (排除 `//host/path` scheme-less URL).
    "(~?/[^/\\s\"'`<>(){}\\[\\]][^\\s\"'`<>(){}\\[\\]]*\\.(?:" +
    EXT_PATTERN +
    "))" +
    // 后边界: 不是字母/数字 (避免 .docx2 / .pdfa 这种伪扩展名)
    "(?![A-Za-z0-9])",
  "gi",
);

const WINDOWS_PATH_RE = new RegExp(
  "(?<![A-Za-z0-9])" +
    "([A-Za-z]:\\\\[^\\s\"'`<>|]+\\.(?:" +
    EXT_PATTERN +
    "))" +
    "(?![A-Za-z0-9])",
  "gi",
);

/**
 * 从一段文本里提取所有可能的文件路径, 去重保持顺序.
 *
 * 输入可能是:
 *   - tool result 的 JSON 字符串 (常见: hermes 返回 {"file": "/Users/.../report.docx"})
 *   - assistant message 的 markdown ("文件已生成: /Users/.../foo.xlsx")
 *   - 错误消息 (路径在错误文本里)
 *
 * 返回: 路径数组, 已去重, 最多 10 个 (避免极端情况下渲染爆炸).
 */
export function extractFilePaths(text: string): string[] {
  if (!text) return [];

  const seen = new Set<string>();
  const result: string[] = [];

  const collect = (re: RegExp) => {
    re.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) {
      const p = m[1];
      if (!p || seen.has(p)) continue;
      // 排除一些明显不对的: 路径里以 .. 结尾, 或全是占位符
      if (p.endsWith("..") || p.includes("<") || p.includes(">")) continue;
      seen.add(p);
      result.push(p);
      if (result.length >= 10) return;
    }
  };

  collect(UNIX_PATH_RE);
  if (result.length < 10) collect(WINDOWS_PATH_RE);

  return result;
}

/** 文件名从路径取最后一段 */
export function basename(path: string): string {
  const m = path.match(/[^/\\]+$/);
  return m ? m[0] : path;
}

/** 扩展名 (lower, 不含点) */
export function extname(path: string): string {
  const m = path.match(/\.([^./\\]+)$/);
  return m ? m[1].toLowerCase() : "";
}

/** 不同文件类型的 emoji —— 视觉区分 office / 数据 / 图片 */
const FILE_EMOJI: Record<string, string> = {
  docx: "📄",
  doc: "📄",
  pdf: "📕",
  pptx: "📊",
  ppt: "📊",
  xlsx: "📈",
  xls: "📈",
  csv: "📈",
  tsv: "📈",
  json: "🧾",
  yaml: "🧾",
  yml: "🧾",
  txt: "📝",
  md: "📝",
  log: "📝",
  png: "🖼",
  jpg: "🖼",
  jpeg: "🖼",
  gif: "🖼",
  svg: "🖼",
  webp: "🖼",
  zip: "🗜",
  tar: "🗜",
  gz: "🗜",
};

export function fileEmoji(path: string): string {
  return FILE_EMOJI[extname(path)] || "📁";
}
