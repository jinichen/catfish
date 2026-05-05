/** 桌宠副窗入口 (BL-E27 五一 sprint 5/5 凌晨, 一次到位版).
 *
 * 4 件事一次完成 (5/5 鸿波 "为什么喜欢留一块"):
 *   1. 4 状态联 LLM 真实状态 (idle / thinking / running / done) — listen
 *      'catfish:agent_status' 事件 (主窗 useChat / tool dispatch 时 emit)
 *   2. 鼠标穿透 (空白透到桌面) — body pointer-events: none
 *   3. 拖拽 — 鲶鱼容器加 data-tauri-drag-region, mousedown 不动=click,
 *      移动 >5px = drag (Tauri 自动切)
 *   4. 位置持久化 — getCurrentWindow().onMoved 存 localStorage,
 *      启动 restore (跨会话/重启位置不丢)
 *
 * 5/5 spike 验证后, 这一份就是 BL-E27.1 的真 ship.
 */

import React from "react";
import ReactDOM from "react-dom/client";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow, PhysicalPosition } from "@tauri-apps/api/window";

type AgentStatus = "idle" | "thinking" | "running" | "done";

const POS_KEY = "catfish_pet_position";

function Pet() {
  const [status, setStatus] = React.useState<AgentStatus>("idle");

  // 启动: 恢复上次位置 + 监听位移
  React.useEffect(() => {
    const win = getCurrentWindow();

    // 恢复 localStorage 存的位置
    try {
      const raw = localStorage.getItem(POS_KEY);
      if (raw) {
        const { x, y } = JSON.parse(raw) as { x: number; y: number };
        if (typeof x === "number" && typeof y === "number") {
          void win.setPosition(new PhysicalPosition(x, y));
        }
      }
    } catch (e) {
      console.warn("pet position restore 失败", e);
    }

    // 监听位移 — 员工拖完桌宠后存位置, 下次启动还原
    const unlistenPromise = win.onMoved((e) => {
      try {
        const { x, y } = e.payload;
        localStorage.setItem(POS_KEY, JSON.stringify({ x, y }));
      } catch (err) {
        console.warn("pet position save 失败", err);
      }
    });

    return () => {
      void unlistenPromise.then((u) => u());
    };
  }, []);

  // 监听主窗 emit 的 LLM 状态事件
  React.useEffect(() => {
    const unlistenPromise = listen<AgentStatus>("catfish:agent_status", (e) => {
      const newStatus = e.payload;
      if (["idle", "thinking", "running", "done"].includes(newStatus)) {
        setStatus(newStatus);
        // done 状态自动 2 秒后回 idle (跳完一下歇着)
        if (newStatus === "done") {
          setTimeout(() => setStatus("idle"), 2000);
        }
      }
    });
    return () => {
      void unlistenPromise.then((u) => u());
    };
  }, []);

  /** 5/5 鸿波"拖不动" 两轮失败后, 拖拽暂时撤销 — Tauri 2 NSPanel +
   *  transparent + alwaysOnTop 三件套下 data-tauri-drag-region / startDragging /
   *  setPosition 三条 webview API 均不响应.
   *
   *  替代方案: Option+Shift+1/2/3/4 全局快捷键切 4 屏角 (Rust 端 lib.rs 处理).
   *  BL-E27.1 真做时 (5/22) 用 Rust objc2 NSPanel 私有 API 修真拖拽. */
  const downRef = React.useRef<{ t: number } | null>(null);

  const onPointerDown = (_e: React.PointerEvent) => {
    downRef.current = { t: Date.now() };
  };

  const onPointerUp = async () => {
    const d = downRef.current;
    downRef.current = null;
    if (!d) return;
    const dt = Date.now() - d.t;
    if (dt < 500) {
      await invoke("pet_clicked").catch((err) => console.warn("pet_clicked:", err));
    }
  };

  return (
    <div
      onPointerDown={onPointerDown}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      style={{
        width: 120,
        height: 120,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        pointerEvents: "auto",
        cursor: "pointer",
        transition: "transform 200ms ease",
      }}
      title="点击唤主窗 · ⌥⇧1/2/3/4 切 4 屏角"
      onMouseEnter={(e) => {
        e.currentTarget.style.transform = "scale(1.1)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.transform = "scale(1)";
      }}
    >
      <img
        src="/catfish-mascot.svg"
        alt="鲶鱼"
        width={80}
        height={80}
        draggable={false}
        style={{
          display: "block",
          willChange: "transform",
          animation:
            status === "idle"
              ? "pet-idle-wiggle 4s ease-in-out infinite"
              : status === "thinking"
              ? "pet-thinking-pulse 1.5s ease-in-out infinite"
              : status === "running"
              ? "pet-running-spin 2s linear infinite"
              : "pet-done-bounce 0.5s ease-out",
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
