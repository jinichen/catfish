/** 够技能说明用的 Markdown 渲染 (10/22)。
 *
 * 只认: # 标题、- / 1. 列表、``` 代码块、> 引用、| 表格 |、段落, 行内 `代码` 和 **加粗**。
 * 全部拼成 React 元素, 不用 innerHTML —— 技能是员工上传的, 不能让内容里的 HTML 跑起来。
 * 中央门户没有引 Markdown 库; 只为一个详情页加依赖, 离线打包和审计都多一份负担。
 */

import type { ReactNode } from "react";

function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let last = 0;
  let i = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("`")) {
      out.push(
        <code
          key={`${keyBase}-${i++}`}
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "0.9em",
            background: "var(--bg-secondary)",
            padding: "1px 4px",
            borderRadius: "var(--radius-sm)",
          }}
        >
          {tok.slice(1, -1)}
        </code>,
      );
    } else {
      out.push(<strong key={`${keyBase}-${i++}`}>{tok.slice(2, -2)}</strong>);
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const cell = { border: "1px solid var(--border)", padding: "4px 8px", textAlign: "left" as const };

export function SimpleMarkdown({ source }: { source: string }) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let k = 0;
  while (i < lines.length) {
    const line = lines[i];
    const key = `b${k++}`;
    if (line.trim() === "") {
      i++;
      continue;
    }
    if (line.startsWith("```")) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) code.push(lines[i++]);
      i++;
      blocks.push(
        <pre
          key={key}
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 12,
            background: "var(--bg-secondary)",
            padding: "var(--space-3)",
            borderRadius: "var(--radius-sm)",
            overflow: "auto",
            margin: "var(--space-2) 0",
          }}
        >
          {code.join("\n")}
        </pre>,
      );
      continue;
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      const level = Math.min(h[1].length + 2, 6);
      const Tag = `h${level}` as "h3" | "h4" | "h5" | "h6";
      blocks.push(
        <Tag key={key} style={{ margin: "var(--space-3) 0 var(--space-2)" }}>
          {inline(h[2], key)}
        </Tag>,
      );
      i++;
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const start = ordered ? Number(/\d+/.exec(line)?.[0] ?? 1) : undefined;
      const items: string[][] = [];
      while (i < lines.length) {
        if (/^\s*([-*]|\d+\.)\s+/.test(lines[i])) {
          items.push([lines[i].replace(/^\s*([-*]|\d+\.)\s+/, "")]);
        } else if (/^\s+\S/.test(lines[i]) && items.length > 0) {
          items[items.length - 1].push(lines[i].trim()); // 缩进的续行归上一项, 不打断编号
        } else {
          break;
        }
        i++;
      }
      const List = ordered ? "ol" : "ul";
      blocks.push(
        <List key={key} start={start} style={{ margin: "var(--space-1) 0", paddingLeft: 22, lineHeight: 1.7 }}>
          {items.map((parts, j) => (
            <li key={j}>
              {parts.map((t, n) => (
                <div key={n} style={n > 0 ? { color: "var(--text-muted)", fontSize: 13 } : undefined}>
                  {inline(t, `${key}-${j}-${n}`)}
                </div>
              ))}
            </li>
          ))}
        </List>,
      );
      continue;
    }
    if (line.startsWith(">")) {
      const quote: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) quote.push(lines[i++].replace(/^>\s?/, ""));
      blocks.push(
        <blockquote
          key={key}
          style={{
            margin: "var(--space-2) 0",
            padding: "var(--space-1) var(--space-3)",
            borderLeft: "3px solid var(--border)",
            color: "var(--text-muted)",
          }}
        >
          {inline(quote.join(" "), key)}
        </blockquote>,
      );
      continue;
    }
    if (line.trim().startsWith("|")) {
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        const cells = lines[i].trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        if (!cells.every((c) => /^:?-+:?$/.test(c))) rows.push(cells);
        i++;
      }
      const [head, ...rest] = rows;
      blocks.push(
        <table key={key} style={{ borderCollapse: "collapse", margin: "var(--space-2) 0", fontSize: 13 }}>
          <thead>
            <tr>
              {head.map((c, j) => (
                <th key={j} style={cell}>{inline(c, `${key}-h${j}`)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rest.map((r, ri) => (
              <tr key={ri}>
                {r.map((c, j) => (
                  <td key={j} style={cell}>{inline(c, `${key}-${ri}-${j}`)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>,
      );
      continue;
    }
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !/^(#{1,6}\s|```|>|\s*([-*]|\d+\.)\s+|\s*\|)/.test(lines[i])
    ) {
      para.push(lines[i++]);
    }
    if (para.length === 0) para.push(lines[i++]); // 兜底: 哪条规则都没吃掉的行, 当正文, 免得死循环
    blocks.push(
      <p key={key} style={{ margin: "var(--space-2) 0", lineHeight: 1.7 }}>
        {inline(para.join(" "), key)}
      </p>,
    );
  }
  return <div style={{ fontSize: 14 }}>{blocks}</div>;
}
