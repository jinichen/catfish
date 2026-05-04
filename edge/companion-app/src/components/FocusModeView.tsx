/** BL-E15 专注模式视图 (五一 sprint 5/3 晚)
 *
 * 触发: Cmd+Shift+F → store.active=true → 这个视图 fullscreen 替换 AppShell.
 *
 * 设计立场 (央企语境):
 *   - **不嘲讽**, 不"领导来了 233" 风, 不假装写代码骗领导. 改名"专注模式".
 *   - 员工真的可以用它**专注**: 屏蔽聊天/通知/Dashboard 杂讯, 只剩一个简洁
 *     "正在工作"卡片 + 一段计时.
 *   - 看上去也像"在认真工作的状态", 副作用上仍能挡领导 1 眼, 但定位是工具不是恶搞.
 *
 * 退出: Esc / 再按 Cmd+Shift+F / 点右上角"退出".
 */

import { useEffect, useRef, useState } from "react";

import { useFocusStore } from "../store/focus";

function formatElapsed(secs: number): string {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

// 滚动的"工作状态"行 (拟真 build / 测试 / lint 输出, 跟员工日常 IDE 看到的差不多).
// 不是程序员的同事看了也认不出, 但不会出现明显假梗. 央企 IT 评估能看的内容.
const STATUS_LINES: string[] = [
  "[INFO] 加载工作上下文…",
  "[INFO] 同步本地缓存 → 已是最新",
  "[OK]   依赖检查通过 (12 项)",
  "[INFO] 运行静态扫描…",
  "[OK]   静态扫描完成, 0 警告",
  "[INFO] 准备增量构建…",
  "[OK]   增量构建完成 (耗时 2.3s)",
  "[INFO] 启动单元测试 (并行 4 路)…",
  "[OK]   测试通过: 142 / 142",
  "[INFO] 生成覆盖率报告…",
  "[OK]   覆盖率 87.4% (基线 85.0%, 提升 +2.4%)",
  "[INFO] 校验配置一致性…",
  "[OK]   配置校验通过",
  "[INFO] 等待下一次输入…",
];

export default function FocusModeView() {
  const exit = useFocusStore((s) => s.exit);
  const [elapsed, setElapsed] = useState(0);
  const [visibleLines, setVisibleLines] = useState<string[]>([]);
  const lineIdxRef = useRef(0);

  // 计时器 (仅显示)
  useEffect(() => {
    const t = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(t);
  }, []);

  // 状态行逐条出 (每 1.4s 一条)
  useEffect(() => {
    setVisibleLines([STATUS_LINES[0]]);
    lineIdxRef.current = 1;
    const t = setInterval(() => {
      const idx = lineIdxRef.current;
      if (idx >= STATUS_LINES.length) {
        // 滚到最后一条后, 把"等待下一次输入" 反复尾追加 (不重复全 list, 防滚到无限长)
        setVisibleLines((prev) => {
          if (prev.length > 200) return prev.slice(-150); // 防内存涨
          return [...prev, "[INFO] 等待下一次输入…"];
        });
      } else {
        setVisibleLines((prev) => [...prev, STATUS_LINES[idx]]);
        lineIdxRef.current = idx + 1;
      }
    }, 1400);
    return () => clearInterval(t);
  }, []);

  // Esc 退出
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        exit();
      }
    };
    // capture 阶段, 抢在 App.tsx 那个 Esc-hide-window 之前
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [exit]);

  // 自动滚到底
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (scrollerRef.current) {
      scrollerRef.current.scrollTop = scrollerRef.current.scrollHeight;
    }
  }, [visibleLines.length]);

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "#0d1117", // GitHub dark, 像 IDE
        color: "#c9d1d9",
        fontFamily: "var(--font-mono, ui-monospace, 'SF Mono', Menlo, monospace)",
        display: "flex",
        flexDirection: "column",
        zIndex: 9999,
      }}
      role="region"
      aria-label="专注模式"
    >
      {/* 顶栏: 状态 + 计时 + 退出 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "8px 16px",
          borderBottom: "1px solid #21262d",
          fontSize: 12,
          color: "#8b949e",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span
            style={{
              display: "inline-block",
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: "#3fb950",
              boxShadow: "0 0 6px rgba(63, 185, 80, 0.6)",
            }}
            aria-hidden
          />
          <span>专注模式</span>
          <span style={{ color: "#484f58" }}>·</span>
          <span>已专注 {formatElapsed(elapsed)}</span>
        </div>
        <button
          type="button"
          onClick={exit}
          style={{
            background: "transparent",
            border: "1px solid #30363d",
            color: "#8b949e",
            fontSize: 11,
            padding: "4px 10px",
            borderRadius: 4,
            cursor: "pointer",
            fontFamily: "inherit",
          }}
          title="Cmd+Shift+F 或 Esc"
        >
          退出 (Esc)
        </button>
      </div>

      {/* 主区: 拟真状态滚动 */}
      <div
        ref={scrollerRef}
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "16px 24px",
          fontSize: 13,
          lineHeight: 1.7,
          minHeight: 0,
        }}
      >
        {visibleLines.map((line, i) => (
          <div key={i} style={{ display: "flex", gap: 12 }}>
            <span style={{ color: "#484f58", flexShrink: 0, userSelect: "none" }}>
              {String(i + 1).padStart(4, " ")}
            </span>
            <span style={{ color: lineColor(line) }}>{line}</span>
          </div>
        ))}
        {/* 闪烁光标在最后一行下方 */}
        <div style={{ display: "flex", gap: 12, marginTop: 4 }}>
          <span style={{ color: "#484f58", flexShrink: 0 }}>
            {String(visibleLines.length + 1).padStart(4, " ")}
          </span>
          <span style={{ color: "#c9d1d9" }}>$&nbsp;</span>
          <span
            style={{
              display: "inline-block",
              width: 7,
              height: 14,
              background: "#c9d1d9",
              animation: "catfish-cursor-blink 1.05s steps(2) infinite",
            }}
            aria-hidden
          />
        </div>
      </div>

      {/* 底栏 (像 VSCode 状态栏) */}
      <div
        style={{
          padding: "4px 16px",
          background: "#1f2428",
          borderTop: "1px solid #21262d",
          fontSize: 11,
          color: "#8b949e",
          display: "flex",
          justifyContent: "space-between",
        }}
      >
        <span>main · 0 issues · 0 warnings</span>
        <span>UTF-8 · LF · 自动保存</span>
      </div>

      {/* 闪烁光标的 keyframes 只这里有, 不污染全局 */}
      <style>{`
        @keyframes catfish-cursor-blink {
          to { visibility: hidden; }
        }
      `}</style>
    </div>
  );
}

function lineColor(line: string): string {
  if (line.startsWith("[OK]")) return "#3fb950";
  if (line.startsWith("[WARN]")) return "#d29922";
  if (line.startsWith("[ERR]") || line.startsWith("[FAIL]")) return "#f85149";
  return "#c9d1d9";
}
