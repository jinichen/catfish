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

// BL-E27.4 (5/8): 桌宠状态颜色 indicator
type PetStatusColor = "default" | "running" | "completed" | "failed";
interface PetStatusSummary {
  color: PetStatusColor;
  unseen_completed: number;
  unseen_failed: number;
  last_event_ts: number;
  seen_ts: number;
}

function Pet() {
  const [status, setStatus] = React.useState<AgentStatus>("idle");
  // 5/6 鸿波: 桌宠 = 主动信息出口. 主窗 emit 'catfish:pet_bubble' 时桌宠头顶冒气泡.
  const [bubble, setBubble] = React.useState<BubbleState | null>(null);
  // 5/8 BL-E27.4: 桌宠状态颜色 (任务完成/失败 unseen)
  const [statusColor, setStatusColor] = React.useState<PetStatusColor>("default");
  const [unseenCount, setUnseenCount] = React.useState<number>(0);

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

  // 5/8 BL-E27.4: 桌宠状态颜色 5 秒 polling.
  // 数据源: ~/.catfish/pet_pending_bubbles.jsonl + seen_ts.json
  // 单击桌宠后, pet_clicked 自动调 mark_all_seen → 颜色回 default.
  React.useEffect(() => {
    const tick = async () => {
      try {
        const s = await invoke<PetStatusSummary>("pet_status_summary");
        setStatusColor(s.color);
        setUnseenCount(s.unseen_failed + s.unseen_completed);
      } catch {
        /* 静默 — Companion 还没起 / Rust 调用失败, 下次 tick 再试 */
      }
    };
    const t = window.setInterval(tick, 5000);
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
        title={
          statusColor === "failed"
            ? `${unseenCount} 个未看 (含失败) · 点击查看`
            : statusColor === "completed"
            ? `${unseenCount} 个未看 · 点击查看`
            : statusColor === "running"
            ? "后台任务运行中"
            : "点击唤主窗 · ⌥⇧1/2/3/4 切 4 屏角"
        }
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
        {/* 5/8 BL-E27.4: 桌宠头部状态颜色 indicator
            优先级: 红 (failed) > 绿 (completed) > 蓝 (running) > 默认 (隐藏)
            位置: 右上角 (跟头部齐), 大小 14x14 让员工 1m 外能看清 */}
        {statusColor !== "default" && (
          <div
            style={{
              position: "absolute",
              top: 4,
              right: 6,
              width: 14,
              height: 14,
              borderRadius: "50%",
              background:
                statusColor === "failed"
                  ? "#dc2626"   // 红: 任务失败未看
                  : statusColor === "completed"
                  ? "#10b981"   // 绿: 任务完成未看
                  : "#3b82f6",  // 蓝: 后台运行 (P2 现在不会触发)
              boxShadow:
                statusColor === "failed"
                  ? "0 0 0 2px rgba(220, 38, 38, 0.3), 0 0 6px rgba(220, 38, 38, 0.7)"
                  : statusColor === "completed"
                  ? "0 0 0 2px rgba(16, 185, 129, 0.3), 0 0 6px rgba(16, 185, 129, 0.7)"
                  : "0 0 0 2px rgba(59, 130, 246, 0.3)",
              animation:
                statusColor === "failed"
                  ? "pet-status-pulse-red 1.4s ease-in-out infinite"
                  : statusColor === "completed"
                  ? "pet-status-pulse-green 2.5s ease-in-out infinite"
                  : "pet-status-pulse-blue 2s ease-in-out infinite",
              zIndex: 2,
              pointerEvents: "none", // 不挡桌宠 click
            }}
            aria-label={`${unseenCount} 个未看`}
          />
        )}
        {/* 数字徽章 — 多于 1 个未看时显示数字 (≤ 9, 超出显示 9+) */}
        {statusColor !== "default" && unseenCount > 1 && (
          <div
            style={{
              position: "absolute",
              top: 0,
              right: 0,
              minWidth: 18,
              height: 18,
              padding: "0 4px",
              borderRadius: 9,
              background: "rgba(0, 0, 0, 0.85)",
              color: "white",
              fontSize: 10,
              fontWeight: 700,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              zIndex: 3,
              pointerEvents: "none",
              boxShadow: "0 1px 3px rgba(0,0,0,0.4)",
            }}
          >
            {unseenCount > 9 ? "9+" : unseenCount}
          </div>
        )}
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
          /* BL-E27.4 (5/8) — 桌宠头颜色 indicator 三档脉动 */
          @keyframes pet-status-pulse-red {
            0%, 100% { transform: scale(1); opacity: 1; }
            50% { transform: scale(1.25); opacity: 0.85; }
          }
          @keyframes pet-status-pulse-green {
            0%, 100% { transform: scale(1); opacity: 1; }
            50% { transform: scale(1.12); opacity: 0.92; }
          }
          @keyframes pet-status-pulse-blue {
            0%, 100% { transform: scale(1); opacity: 0.9; }
            50% { transform: scale(1.08); opacity: 1; }
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
