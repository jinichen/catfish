/**
 * 文件 Pill —— skill 生成 .docx / .xlsx / .pptx 等之后,
 * 在聊天里展示一个可点击的胶囊, 员工点 → 在 Finder 显示 / 用默认 app 打开.
 *
 * 设计:
 *   - 胶囊本体显示 emoji + 文件名 (不显示完整路径, 路径太长难看, hover title 看)
 *   - 主按钮: "在 Finder 显示" (macOS) —— 这是主操作, 因为打开 docx 大概率走 Word
 *     而 Word 启动慢且员工要先看一下文件在哪
 *   - 次按钮: "打开" —— 隐藏在 ⋯ 里? 不, 直接放出来, 操作要平铺
 *   - 错误处理: revealInFinder 失败 (路径不存在 / 权限不够) → 短暂红字提示, 不弹 alert
 */

import { useState } from "react";
import { revealInFinder, openFile } from "../lib/tauri";
import { basename, fileEmoji } from "../lib/path_detect";

interface Props {
  path: string;
}

export default function FilePill({ path }: Props) {
  const [err, setErr] = useState<string | null>(null);

  const handleReveal = async () => {
    setErr(null);
    try {
      await revealInFinder(path);
    } catch (e) {
      setErr(formatErr(e));
    }
  };

  const handleOpen = async () => {
    setErr(null);
    try {
      await openFile(path);
    } catch (e) {
      setErr(formatErr(e));
    }
  };

  return (
    <div
      style={{
        display: "inline-flex",
        flexDirection: "column",
        gap: 2,
        margin: "4px 6px 4px 0",
        verticalAlign: "middle",
      }}
    >
      <div
        title={path}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          padding: "4px 8px 4px 10px",
          background: "var(--catfish-bg)",
          border: "1px solid var(--catfish-border)",
          borderRadius: 999,
          fontSize: 12,
          lineHeight: 1.4,
          maxWidth: 360,
        }}
      >
        <span style={{ fontSize: 13 }} aria-hidden>
          {fileEmoji(path)}
        </span>
        <span
          style={{
            fontFamily: "var(--font-mono)",
            color: "var(--catfish-text)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            maxWidth: 200,
          }}
        >
          {basename(path)}
        </span>
        <button
          type="button"
          onClick={handleReveal}
          style={pillBtnStyle("primary")}
          title="在 Finder 显示"
        >
          在 Finder 显示
        </button>
        <button
          type="button"
          onClick={handleOpen}
          style={pillBtnStyle("secondary")}
          title="用默认 app 打开"
        >
          打开
        </button>
      </div>
      {err && (
        <div
          style={{
            fontSize: 10,
            color: "var(--status-err)",
            paddingLeft: 10,
            fontFamily: "var(--font-mono)",
            maxWidth: 360,
            wordBreak: "break-all",
          }}
        >
          ✗ {err}
        </div>
      )}
    </div>
  );
}

/**
 * 一行多个文件 pill 的容器. 也可以单独 import 用.
 */
export function FilePillList({ paths }: { paths: string[] }) {
  if (!paths || paths.length === 0) return null;
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 0,
        marginTop: 6,
      }}
    >
      {paths.map((p) => (
        <FilePill key={p} path={p} />
      ))}
    </div>
  );
}

function pillBtnStyle(kind: "primary" | "secondary"): React.CSSProperties {
  return {
    border: "none",
    background:
      kind === "primary" ? "var(--catfish-cyan-dim)" : "transparent",
    color:
      kind === "primary" ? "white" : "var(--catfish-text-muted)",
    fontSize: 11,
    padding: "2px 8px",
    borderRadius: 999,
    cursor: "pointer",
    whiteSpace: "nowrap",
    fontFamily: "inherit",
  };
}

function formatErr(e: unknown): string {
  const s = typeof e === "string" ? e : (e as Error)?.message || String(e);
  if (s.includes("文件不存在")) return "文件已被删除或路径已变更";
  if (s.includes("敏感路径")) return "拒绝打开敏感系统目录";
  if (s.includes("不支持")) return "当前系统暂不支持此操作";
  return s.length > 80 ? s.slice(0, 80) + "…" : s;
}
