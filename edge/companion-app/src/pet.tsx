/** 桌宠副窗入口 (BL-E27 spike, 5/5 凌晨)
 *
 * 跟主 Companion (main.tsx) 平行的副入口. tauri.conf.json 里 label="pet"
 * 的 window 加载 /pet.html, 这里渲染.
 *
 * MVP spike 范围:
 *   - 透明 80×80 区域显示一个鲶鱼 SVG
 *   - CSS keyframes 慢扭尾巴 (idle 状态)
 *   - 单击 → invoke('pet_clicked') → 主窗口聚焦 (TODO 阶段 2)
 *   - 双击 → 同上 (差别先不做, 实操中员工双击概率低)
 *
 * 容错:
 *   - 整个 body pointer-events: none, 只 mascot 容器恢复 auto
 *   - 这样点击窗口的"空白"会穿透到桌面 (Finder 桌面图标可点)
 */

import React from "react";
import ReactDOM from "react-dom/client";
import { invoke } from "@tauri-apps/api/core";

function Pet() {
  // BL-E27 spike: idle 状态. 后续 BL-E27.1 加 thinking/running/done 4 状态.
  const [status] = React.useState<"idle" | "thinking" | "running" | "done">(
    "idle",
  );

  const onClick = async () => {
    try {
      // TODO BL-E27.1: 真实现 — 把 main window 拉到前台.
      // 现 spike 只 log 一下, 验证点击事件能到.
      await invoke("pet_clicked").catch((e) => console.warn("pet_clicked:", e));
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div
      onClick={onClick}
      style={{
        // 整个区域居中放 mascot. 80×80 给 SVG, 周围留 20px 防裁切.
        width: 120,
        height: 120,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        // 关键: 这一层 pointer-events 恢复 auto (mascot 接点击),
        // body 仍 none (窗口空白透明区不拦鼠标).
        pointerEvents: "auto",
        cursor: "pointer",
        // 加 hover 视觉反馈, 让员工知道这是可交互的, 不是装饰
        transition: "transform 200ms ease",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.transform = "scale(1.1)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.transform = "scale(1)";
      }}
      title="点击唤醒鲶鱼"
    >
      <img
        src="/catfish-mascot.svg"
        alt="鲶鱼"
        width={80}
        height={80}
        style={{
          display: "block",
          // CSS animation: 慢扭尾巴 (idle). 每 30s 一次, 防分散注意力.
          animation:
            status === "idle"
              ? "pet-idle-wiggle 4s ease-in-out infinite"
              : status === "thinking"
              ? "pet-thinking-pulse 1.5s ease-in-out infinite"
              : status === "running"
              ? "pet-running-spin 2s linear infinite"
              : "pet-done-bounce 0.5s ease-out",
          // GPU 加速防 CPU 飘
          willChange: "transform",
        }}
      />
      <style>
        {`
          @keyframes pet-idle-wiggle {
            0%, 90%, 100% { transform: rotate(0deg); }
            93% { transform: rotate(-5deg); }
            96% { transform: rotate(5deg); }
          }
          @keyframes pet-thinking-pulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.6; transform: scale(0.95); }
          }
          @keyframes pet-running-spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
          }
          @keyframes pet-done-bounce {
            0% { transform: translateY(0); }
            50% { transform: translateY(-10px); }
            100% { transform: translateY(0); }
          }
        `}
      </style>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("pet-root")!).render(
  <React.StrictMode>
    <Pet />
  </React.StrictMode>,
);
