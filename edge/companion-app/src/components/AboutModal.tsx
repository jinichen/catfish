/** 关于鲶鱼模态 — 顶部 chip + macOS app menu 共享.
 *
 * 5/18 BL-COMPANION-ABOUT-CHIP: WebPortalLink banner 右上角 chip 点开此模态.
 * 5/18 BL-COMPANION-ABOUT-HIJACK: macOS app menu "鲶鱼 Companion → 关于鲶鱼"
 *      也走这个模态 (不走原生 NSPanel). 实现: Rust 端 emit "show-about" 事件,
 *      App.tsx 监听后 useUIStore.openAbout(). 这里只负责渲染.
 *
 * 内容 (简版 / 鸿波 5/18 选): 一句话介绍 + 单一品牌版本号. 没反馈渠道 / 文档链接 /
 * 诊断信息 (那些走客服 / 控制台 tab).
 */

import { useEffect, useState } from "react";
import { getVersion } from "@tauri-apps/api/app";

import { useUIStore } from "../store/ui";

export default function AboutModal() {
  const open = useUIStore((s) => s.aboutOpen);
  const close = useUIStore((s) => s.closeAbout);
  const [version, setVersion] = useState<string>("…");

  useEffect(() => {
    if (!open) return;
    getVersion()
      .then((v) => setVersion(v))
      .catch((e) => {
        // 非 Tauri 环境 (vite preview / vitest) 兜底
        // eslint-disable-next-line no-console
        console.warn("[AboutModal] getVersion 失败:", e);
        setVersion("?");
      });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close]);

  if (!open) return null;

  return (
    <div
      onClick={close}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.4)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 9999,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--catfish-bg-elevated)",
          border: "1px solid var(--catfish-border)",
          borderRadius: "var(--radius-md)",
          padding: "var(--space-4)",
          maxWidth: 420,
          width: "90%",
          boxShadow: "0 10px 40px rgba(0,0,0,0.2)",
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-2)",
            marginBottom: "var(--space-3)",
          }}
        >
          <span style={{ fontSize: 28 }}>🐟</span>
          <div style={{ fontSize: 18, fontWeight: 600 }}>关于鲶鱼</div>
          <button
            type="button"
            onClick={close}
            aria-label="关闭"
            style={{
              marginLeft: "auto",
              background: "transparent",
              border: "none",
              fontSize: 18,
              color: "var(--catfish-text-muted)",
              cursor: "pointer",
              padding: 4,
            }}
          >
            ×
          </button>
        </div>
        <p
          style={{
            fontSize: 13,
            lineHeight: 1.6,
            color: "var(--catfish-text)",
            margin: 0,
            marginBottom: "var(--space-3)",
          }}
        >
          <strong>鲶鱼</strong>是企业内部 AI 副手 —— 帮你看邮件 / 起草回复 / 跑长任务 /
          跨 LLM 对话. 私密数据留在你本机, 不上云.
        </p>
        <div
          style={{
            fontSize: 12,
            color: "var(--catfish-text-muted)",
            paddingTop: "var(--space-2)",
            borderTop: "1px dashed var(--catfish-border)",
            display: "grid",
            gridTemplateColumns: "auto 1fr",
            columnGap: "var(--space-3)",
            rowGap: 4,
          }}
        >
          <div>版本</div>
          <div style={{ fontFamily: "var(--font-mono, monospace)" }}>v{version}</div>
        </div>
      </div>
    </div>
  );
}
