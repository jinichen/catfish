/** Markdown 渲染包装器 —— 给 ChatMessage 用。
 *
 * 用 react-markdown + remark-gfm (table/checklist) + rehype-highlight (代码高亮)。
 * highlight.js 自带主题 css 在入口加载（main.tsx 里 import）。
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

interface Props {
  text: string;
}

export function Markdown({ text }: Props) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
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
          // 表格：简单边框
          table: ({ children }) => (
            <table
              style={{
                borderCollapse: "collapse",
                margin: "var(--space-2) 0",
                fontSize: 13,
              }}
            >
              {children}
            </table>
          ),
          th: ({ children }) => (
            <th
              style={{
                border: "1px solid var(--catfish-border)",
                padding: "4px 8px",
                background: "var(--catfish-bg)",
                textAlign: "left",
              }}
            >
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td
              style={{
                border: "1px solid var(--catfish-border)",
                padding: "4px 8px",
              }}
            >
              {children}
            </td>
          ),
          // 链接外开（Tauri webview 里默认会在内嵌打开，要让用户在系统浏览器开）
          a: ({ children, href }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "var(--catfish-cyan-dim)" }}
            >
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
