/** Markdown 渲染包装器 —— 给 ChatMessage 用。
 *
 * 用 react-markdown + remark-gfm (table/checklist) + rehype-highlight (代码高亮)
 * + rehype-raw (允许 LLM 偶尔吐出来的 <br> / <sub> 等内联 HTML).
 * highlight.js 自带主题 css 在入口加载（main.tsx 里 import）。
 *
 * 5/6 BL-D14 修: 表格不渲染 — 两个根因:
 *   1) LLM 经常把 <br> 写在表格 cell 里换行 → 默认 react-markdown 当文字
 *      → 加 rehype-raw 让 <br> / <sub> 等内联 HTML 真正渲染.
 *   2) LLM 偶尔把整张表格挤成一行 (像 "| a | b || c | d |"),
 *      GFM 表格语法要求行间有 \n, 不然不识别 → preprocess 把
 *      代码块外的 "||" 还原成 "|\n|" (markdown 表格里 || 本身不合法,
 *      只可能是行尾+行首被吃掉了换行).
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import rehypeRaw from "rehype-raw";

interface Props {
  text: string;
}

/** 5/6 BL-D14: 修 LLM "伪表格" 输出.
 *
 *  LLM (尤其 qwen) 经常吐这种格式 (没 |---| 分隔行, GFM 不识别):
 *      章节 |2024 |2025 |原因 || 第一条 |...|...|... || 第二条 |...|
 *  规律: || = 行分隔, | = 列分隔, cell 内可能还有 \n.
 *
 *  对策: 检测 "|| 模式" + 没合法 |---| 分隔行 → 重新组装成正经 markdown table.
 *  - 代码块 / inline code 内的 || 不动 (像 JS 的 a || b)
 *  - cell 内 \n → <br> (rehype-raw 让它真渲染成换行)
 *  - 列数不齐自动补空 cell
 *  - 注入 |---| 分隔行
 */
function normalizeLLMTable(text: string): string {
  const segments = text.split(/(```[\s\S]*?```|`[^`\n]*`)/g);
  return segments
    .map((seg, i) => {
      if (i % 2 === 1) return seg; // 代码块/inline code 原样
      // 已是合法 markdown table (有 |---| 分隔行) → 不动
      if (/^\s*\|[\s\-:|]+\|\s*$/m.test(seg)) return seg;
      // 含 || → LLM 伪表格, 重新组装
      if (seg.includes("||")) return reformatPseudoTable(seg);
      return seg;
    })
    .join("");
}

/** 把 LLM "|| 行分隔, | 列分隔" 的伪表格重组成正经 GFM table. */
function reformatPseudoTable(text: string): string {
  // 1. 切出表格前的 prose. 第一个 | 前的内容如果是独立段落 (末尾 \n),
  //    保留为 prose 不进表格.
  let prefix = "";
  let tableBlock = text;
  const firstPipe = text.indexOf("|");
  if (firstPipe > 0) {
    const before = text.slice(0, firstPipe);
    // before 末尾是 \n → 独立 prose 段; 否则可能是 cell 内文字, 不切
    if (/\n\s*$/.test(before)) {
      prefix = before.replace(/\s+$/, "");
      tableBlock = text.slice(firstPipe);
    }
  }

  // 2. 行: 切 ||
  const rawRows = tableBlock.split(/\s*\|\|\s*/);

  // 3. 每行: cell 内 \n → <br>, 去掉行首尾 |, 切列
  const parsedRows = rawRows.map((row) => {
    const oneLine = row.replace(/\n+/g, "<br>");
    const stripped = oneLine.replace(/^\s*\|\s*/, "").replace(/\s*\|\s*$/, "");
    return stripped.split(/\s*\|\s*/).map((c) => c.trim());
  });

  // 4. 过滤: 空行 + LLM 自己写的 :--- 分隔行 (cells 全是 :--- / --- / :--- /
  //    带可选 <br> 的形式)
  const validRows = parsedRows
    .filter((r) => r.some((c) => c.length > 0))
    .filter((r) => !r.every((c) => c === "" || /^:?-+:?(<br>)?$/.test(c)));
  if (validRows.length < 2) return text;

  // 5. 列数 = 最大值, 短行补空 cell (天然支持"合并单元格" — 短行视觉等同合并)
  const colCount = Math.max(...validRows.map((r) => r.length));
  if (colCount < 2) return text;

  const padded = validRows.map((r) => {
    const copy = [...r];
    while (copy.length < colCount) copy.push("");
    return copy;
  });

  const out: string[] = [];
  out.push("| " + padded[0].join(" | ") + " |");
  out.push("|" + " --- |".repeat(colCount));
  for (let i = 1; i < padded.length; i++) {
    out.push("| " + padded[i].join(" | ") + " |");
  }
  // 6. prose + 空行 + 表格 + 空行
  return (prefix ? prefix + "\n\n" : "") + out.join("\n") + "\n\n";
}

export function Markdown({ text }: Props) {
  const normalized = normalizeLLMTable(text);
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // 注意 plugin 顺序: rehype-raw 先 (把 raw HTML 转成 hast 节点),
        // rehype-highlight 后 (给 code 块染色). 两个都需要.
        rehypePlugins={[rehypeRaw, rehypeHighlight]}
        components={{
          // 代码块外层 <pre>，加上滚动 + padding（高亮 css 只管语法色）
          pre: ({ children }) => (
            <pre
              style={{
                background: "var(--catfish-bg)",
                border: "1px solid var(--catfish-border)",
                borderRadius: "var(--radius-sm)",
                padding: "var(--space-3)",
                overflow: "auto",
                fontSize: 12,
                margin: "var(--space-2) 0",
              }}
            >
              {children}
            </pre>
          ),
          // inline code：跟代码块区分开，浅底色
          code: ({ children, className, ...props }) => {
            const isBlock = className?.startsWith("hljs") || className?.startsWith("language-");
            if (isBlock) {
              return (
                <code className={className} {...props}>
                  {children}
                </code>
              );
            }
            return (
              <code
                style={{
                  background: "var(--catfish-bg)",
                  padding: "1px 5px",
                  borderRadius: 3,
                  fontSize: "0.9em",
                  fontFamily: "var(--font-mono)",
                }}
              >
                {children}
              </code>
            );
          },
          // 表格: 包一层横向滚动, 资质对照表那种 4-5 列表格不至于挤
          table: ({ children }) => (
            <div
              style={{
                overflowX: "auto",
                margin: "var(--space-2) 0",
                maxWidth: "100%",
              }}
            >
              <table
                style={{
                  borderCollapse: "collapse",
                  fontSize: 13,
                  minWidth: "100%",
                }}
              >
                {children}
              </table>
            </div>
          ),
          th: ({ children }) => (
            <th
              style={{
                border: "1px solid var(--catfish-border)",
                padding: "6px 10px",
                background: "var(--catfish-bg)",
                textAlign: "left",
                verticalAlign: "top",
                // 多行内容 (LLM 用 <br> 换行 / 单元格里有 \n) 要保留
                whiteSpace: "normal",
                wordBreak: "break-word",
              }}
            >
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td
              style={{
                border: "1px solid var(--catfish-border)",
                padding: "6px 10px",
                verticalAlign: "top",
                whiteSpace: "normal",
                wordBreak: "break-word",
              }}
            >
              {children}
            </td>
          ),
          // <br> 单独 style 一下, 让表格里的换行显得自然
          br: () => <br />,
          // 链接外开 — Tauri webview 默认吞 <a target="_blank">, 必须程序化
          // 调 shell.open 才能真在系统浏览器开 (BL-ARCH2 fix1, 5/10 鸿波反馈).
          a: ({ children, href }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => {
                if (!href) return;
                e.preventDefault();
                void (async () => {
                  try {
                    const { open } = await import("@tauri-apps/plugin-shell");
                    await open(href);
                  } catch {
                    try { window.open(href, "_blank", "noopener,noreferrer"); } catch { /* ignore */ }
                  }
                })();
              }}
              style={{ color: "var(--catfish-cyan-dim)", cursor: "pointer" }}
            >
              {children}
            </a>
          ),
        }}
      >
        {normalized}
      </ReactMarkdown>
    </div>
  );
}
