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
import { getCurrentWindow, PhysicalPosition } from "@tauri-apps/api/window";

type AgentStatus = "idle" | "thinking" | "running" | "done";

const POS_KEY = "catfish_pet_position";

interface BubbleState {
  text: string;
  /** 用于 8s 后自动收: ts 比对当前时间 */
  ts: number;
}

function Pet() {
  const [status, setStatus] = React.useState<AgentStatus>("idle");
  // 5/6 鸿波: 桌宠 = 主动信息出口. 主窗 emit 'catfish:pet_bubble' 时桌宠头顶冒气泡.
  const [bubble, setBubble] = React.useState<BubbleState | null>(null);

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

  // 5/6 polling: agent_status (idle/thinking/running/done) 跟 bubble 一样走 Rust
  // polling buffer (Tauri 跨 webview event 不通). 500ms tick (状态切换不需要那么快).
  React.useEffect(() => {
    const tick = async () => {
      try {
        const s = await invoke<AgentStatus | null>("pet_pop_status");
        if (!s) return;
        if (["idle", "thinking", "running", "done"].includes(s)) {
          setStatus(s);
          if (s === "done") {
            // done 跳完 2s 自动回 idle
            setTimeout(() => setStatus("idle"), 2000);
          }
        }
      } catch {
        /* ignore */
      }
    };
    const t = window.setInterval(tick, 500);
    void tick();
    return () => window.clearInterval(t);
  }, []);

  // 5/6 polling: 桌宠气泡 — 每 300ms 拉一次 pet_pop_bubble(), ts 去重防重显.
  React.useEffect(() => {
    let lastTs = 0;
    const tick = async () => {
      try {
        const pb = await invoke<{
          text: string;
          agentName?: string;
          ts: number;
        } | null>("pet_pop_bubble");
        if (!pb || pb.ts === lastTs) return;
        lastTs = pb.ts;
        const text = (pb.text || "").trim();
        if (!text) return;
        const ts = Date.now();
        setBubble({ text, ts });
        void invoke("pet_log", {
          tag: "polled_bubble",
          msg: `text=${text.slice(0, 40)}`,
        }).catch(() => {});
        setTimeout(() => {
          setBubble((cur) => (cur && cur.ts === ts ? null : cur));
        }, 8000);
      } catch {
        // 静默 — pet_pop_bubble 不该挂, 真挂了下次 tick 还会试
      }
    };
    const t = window.setInterval(tick, 300);
    void tick(); // 立即跑一次
    return () => window.clearInterval(t);
  }, []);

  // 5/6 桌宠气泡通信只走 polling (上面那个 useEffect). 之前试过的 Tauri 跨 webview
  // event 路径 (app.emit_to / pet.emit / frontend listen) 实测都通不到 listener,
  // 死代码已清理.

  // 5/6 BL-E27.2: 气泡显示状态告诉 Rust hover tracker, 让气泡区域也接事件
  // (否则气泡会被穿透, 点不到).
  React.useEffect(() => {
    void invoke("pet_set_bubble_visible", { visible: bubble !== null }).catch(
      (err) => console.warn("pet_set_bubble_visible:", err),
    );
  }, [bubble]);

  /** 5/6 BL-E27.2 真拖拽 + 真穿透.
   *
   *  Rust 端 services/pet_hover.rs 后台 80ms 轮询鼠标位置, 切
   *  setIgnoreCursorEvents:
   *    - 鼠标在鲶鱼 96×114 区域 / 气泡区域 → false, webview 收事件
   *    - 在透明角 → true, 事件穿透到下面 app (鸿波"附近点击被拦"修)
   *  桌宠区域 NSPanel 现在能正常收事件了, startDragging 也能用了.
   *
   *  click vs drag 区分:
   *    - pointer down 记录起点
   *    - pointer move 移动 > 5px → 调 pet_start_drag (OS 接管)
   *    - pointer up 没拖过 + dt < 500ms → click 唤主窗
   *
   *  注: startDragging 后 OS 接管, webview 可能收不到 pointerup, 用 dragging
   *  flag 兜底.
   */
  const downRef = React.useRef<{
    t: number;
    x: number;
    y: number;
    dragging: boolean;
  } | null>(null);

  const onPointerDown = (e: React.PointerEvent) => {
    downRef.current = {
      t: Date.now(),
      x: e.clientX,
      y: e.clientY,
      dragging: false,
    };
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const d = downRef.current;
    if (!d || d.dragging) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (Math.hypot(dx, dy) > 5) {
      d.dragging = true;
      void invoke("pet_start_drag").catch((err) =>
        console.warn("pet_start_drag:", err),
      );
    }
  };

  const onPointerUp = async () => {
    const d = downRef.current;
    downRef.current = null;
    if (!d) return;
    if (d.dragging) return; // 已拖, 不当点击
    const dt = Date.now() - d.t;
    if (dt < 500) {
      await invoke("pet_clicked").catch((err) => console.warn("pet_clicked:", err));
    }
  };

  return (
    <div
      style={{
        width: 200,
        height: 200,
        position: "relative",
        // 整个 webview 默认 pointer-events 由 body 处理 (none 穿透);
        // 气泡 + 桌宠各自恢复 auto.
        pointerEvents: "none",
      }}
    >
      {/* 顶部气泡 (主动信息出口) — 5/6 鸿波: 桌宠是统一通知出口 */}
      {bubble && (
        <div
          onClick={() => void invoke("pet_clicked").catch(() => {})}
          style={{
            position: "absolute",
            top: 8,
            left: 8,
            right: 8,
            maxWidth: 184,
            padding: "8px 12px",
            background: "rgba(255, 248, 220, 0.97)", // 暖米底, 跟 brand 一致
            border: "1.5px solid #0E5F66",
            borderRadius: 12,
            fontSize: 12,
            lineHeight: 1.45,
            color: "#062E33",
            boxShadow: "0 4px 12px rgba(0, 0, 0, 0.18)",
            cursor: "pointer",
            pointerEvents: "auto",
            wordBreak: "break-word",
            // 入场动画
            animation: "pet-bubble-in 220ms cubic-bezier(0.34, 1.56, 0.64, 1)",
          }}
          title="点击跟我聊"
        >
          {bubble.text.length > 80 ? bubble.text.slice(0, 80) + "…" : bubble.text}
          {/* 三角小尾巴, 指向桌宠 */}
          <div
            style={{
              position: "absolute",
              bottom: -8,
              left: "50%",
              transform: "translateX(-50%)",
              width: 0,
              height: 0,
              borderLeft: "8px solid transparent",
              borderRight: "8px solid transparent",
              borderTop: "8px solid #0E5F66",
            }}
          />
          <div
            style={{
              position: "absolute",
              bottom: -6,
              left: "50%",
              transform: "translateX(-50%)",
              width: 0,
              height: 0,
              borderLeft: "7px solid transparent",
              borderRight: "7px solid transparent",
              borderTop: "7px solid rgba(255, 248, 220, 0.97)",
            }}
          />
        </div>
      )}

      {/* 桌宠 (居中下方) */}
      <div
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        style={{
          position: "absolute",
          bottom: 0,
          left: "50%",
          transform: "translateX(-50%)",
          width: 96,
          height: 114,
          pointerEvents: "auto",
          cursor: "pointer",
          transition: "transform 200ms ease",
        }}
        title="点击唤主窗 · ⌥⇧1/2/3/4 切 4 屏角"
        onMouseEnter={(e) => {
          e.currentTarget.style.transform = "translateX(-50%) scale(1.08)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.transform = "translateX(-50%) scale(1)";
        }}
      >
        <img
          src="/catfish-pet.svg"
          alt="鲶鱼"
          width={96}
          height={114}
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
      </div>
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
          @keyframes pet-bubble-in {
            0% { opacity: 0; transform: translateY(8px) scale(0.9); }
            100% { opacity: 1; transform: translateY(0) scale(1); }
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
