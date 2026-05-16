/** 仪表盘 · 4 服务概览卡片
 *
 * 复用 Console tab 的状态轮询 hook (useServiceStatus + services store), 但比
 * Console 的 ServiceCard 紧凑 —— 每行一个服务: 状态点 + 名称 + 端口/PID/状态文案。
 * 点击行跳到"控制台" tab 做启停操作 (这里只展示, 不操作)。
 *
 * 为啥 Dashboard 也要显示:
 *   员工默认进 Dashboard 想一眼看清楚整体健康度。 autostart 拉起的 tool-bridge /
 *   gateway 出问题时, 不该让员工跑去控制台才能发现。
 */

import { useServicesStore } from "../../store/services";
import { useServiceStatus } from "../../hooks/useServiceStatus";
import { useAgentStore } from "../../store/agent";
import StatusDot from "../../components/StatusDot";
import type { ServiceId, ServiceStatus } from "../../types/service";
import {
  gatewayStart,
  gatewayStop,
  chromeLaunch,
  chromeKill,
  localSearchStart,
  localSearchStop,
  toolBridgeStart,
  toolBridgeStop,
} from "../../lib/tauri";

// BL-CONSOLE-TAB-KILL (5/16): 启停 action 表, 替代砍掉的控制台 tab.
// 每个 ServiceId 知道自己怎么 start/stop. 重启 = stop + 800ms + start.
const SERVICE_ACTIONS: Record<
  ServiceId,
  { start: () => Promise<unknown>; stop: () => Promise<unknown> }
> = {
  gateway: { start: gatewayStart, stop: gatewayStop },
  chrome: { start: chromeLaunch, stop: chromeKill },
  local_search: { start: localSearchStart, stop: localSearchStop },
  tool_bridge: { start: toolBridgeStart, stop: toolBridgeStop },
};

async function restartService(id: ServiceId): Promise<void> {
  const a = SERVICE_ACTIONS[id];
  if (!a) return;
  try {
    await a.stop();
  } catch {
    /* stop 失败可能因为它已经挂, 不阻止重启 */
  }
  await new Promise((r) => setTimeout(r, 600));
  await a.start();
}

interface ServiceRow {
  id: ServiceId;
  name: string;
  why: string; // tooltip / 副标题: 为啥这个服务对员工重要
}

// BL-E11 后续: tool_bridge 的 why 提到 agent, 用员工自定义名拼出.
// 数量恒定 (4 行), useServiceStatus hook 顺序稳定 — 跟原静态 const 等价.
function buildServices(agentName: string): ServiceRow[] {
  return [
    {
      id: "gateway",
      name: "LLM Gateway",
      why: "所有 LLM 请求经它, 没起来 = 聊天用不了",
    },
    {
      id: "tool_bridge",
      name: "Tool Bridge",
      why: `暴露 60+ 工具给${agentName}, 没起来 = ${agentName}无工具瞎答`,
    },
    {
      id: "chrome",
      name: "Catfish Chrome",
      why: "浏览器自动化 (browser_navigate 等), 不需要可不起",
    },
    {
      id: "local_search",
      name: "Local Search",
      why: "本地文件全文搜索, 不需要可不起",
    },
  ];
}

// BL-CONSOLE-TAB-KILL (5/16): 卡内 batch + 单服务重启按钮的 style 常量.
const batchBtnStyle: React.CSSProperties = {
  fontSize: 11,
  color: "var(--catfish-text-muted)",
  background: "transparent",
  border: "1px solid var(--catfish-border)",
  borderRadius: "var(--radius-sm)",
  cursor: "pointer",
  padding: "2px 8px",
};

const rowBtnStyle: React.CSSProperties = {
  fontSize: 14,
  color: "var(--catfish-text-muted)",
  background: "transparent",
  border: "1px solid var(--catfish-border)",
  borderRadius: "var(--radius-sm)",
  cursor: "pointer",
  padding: "2px 8px",
  fontFamily: "var(--font-mono)",
  lineHeight: 1,
};

export default function ServicesCard() {
  // 注: 不在这里 for 循环调 useServiceStatus —— React hooks 规则不允许。
  // 每行组件 ServiceRowItem 自己调 hook, 数量恒定 (buildServices 永远返 4 行)。
  const agentName = useAgentStore((s) => s.name);
  const SERVICES = buildServices(agentName);

  return (
    <section
      style={{
        background: "var(--catfish-bg-elevated)",
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        padding: "var(--space-4)",
        boxShadow: "var(--shadow-sm)",
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: "var(--space-3)",
        }}
      >
        <strong>本地服务</strong>
        {/* BL-CONSOLE-TAB-KILL (5/16): 一键 batch 操作, 替代砍掉的控制台 tab.
            真要看 log 走 ~/Library/Logs/Catfish/*.log 文件. */}
        <div style={{ display: "flex", gap: 4 }}>
          <button
            onClick={() => {
              void Promise.allSettled([
                gatewayStart(),
                chromeLaunch(),
                localSearchStart(),
                toolBridgeStart(),
              ]);
            }}
            title="一键启动 4 个本地服务"
            style={batchBtnStyle}
          >
            全启
          </button>
          <button
            onClick={() => {
              void Promise.allSettled([
                gatewayStop(),
                chromeKill(),
                localSearchStop(),
                toolBridgeStop(),
              ]);
            }}
            title="一键停止 4 个本地服务"
            style={batchBtnStyle}
          >
            全停
          </button>
          <button
            onClick={async () => {
              await Promise.allSettled([
                gatewayStop(),
                chromeKill(),
                localSearchStop(),
                toolBridgeStop(),
              ]);
              await new Promise((r) => setTimeout(r, 800));
              await Promise.allSettled([
                gatewayStart(),
                chromeLaunch(),
                localSearchStart(),
                toolBridgeStart(),
              ]);
            }}
            title="一键全部重启"
            style={batchBtnStyle}
          >
            全重启
          </button>
        </div>
      </header>

      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {SERVICES.map((s) => (
          <ServiceRowItem key={s.id} row={s} />
        ))}
      </div>
    </section>
  );
}

function ServiceRowItem({ row }: { row: ServiceRow }) {
  // 触发该服务的轮询 (3s 一次), 同时订阅 store
  useServiceStatus(row.id);
  const status = useServicesStore((s) => s.statuses[row.id]);
  const dotKind = dotKindFor(status);
  // BL-FIX18 (5/8): 不用 HTML title tooltip — native tooltip 浏览器自己定位
  // (跟随鼠标), 不受卡片边界控制, 横穿到右边 '本月配额' 卡片. 改成 inline
  // 副标题展示, 鸿波反馈"宽度出界了". 4 个服务垂直空间够, 不需要悬浮.
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        padding: "6px 0",
        borderBottom: "1px solid var(--catfish-border)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <StatusDot status={dotKind} />
        <span style={{ flex: 1, fontSize: 13, fontWeight: 500 }}>{row.name}</span>
        <span
          style={{
            fontSize: 11,
            color: "var(--catfish-text-muted)",
            fontFamily: "var(--font-mono)",
            textOverflow: "ellipsis",
            overflow: "hidden",
            whiteSpace: "nowrap",
            maxWidth: 140,
          }}
        >
          {compactStatusText(status)}
        </span>
        {/* BL-CONSOLE-TAB-KILL (5/16): 单服务重启按钮, 替代控制台单卡操作.
            员工 90% 撞 bug 时想做的就是"重启这个服务", 直接前置. */}
        <button
          onClick={() => void restartService(row.id)}
          title={`重启 ${row.name}`}
          style={rowBtnStyle}
        >
          ↻
        </button>
      </div>
      {/* BL-FIX18: why 改 inline 副标题, 不再 native tooltip 出界 */}
      <div
        style={{
          fontSize: 10,
          color: "var(--catfish-text-muted)",
          paddingLeft: 18, // 跟 StatusDot 宽 (8px) + gap (10px) 对齐
          lineHeight: 1.4,
        }}
      >
        {row.why}
      </div>
    </div>
  );
}

function dotKindFor(status: ServiceStatus | undefined): "ok" | "warn" | "err" | "idle" {
  if (!status) return "idle";
  if (!status.running) return "err";
  if (status.healthy) return "ok";
  return "warn";
}

function compactStatusText(status: ServiceStatus | undefined): string {
  if (!status) return "探测中…";
  if (!status.running) return "未启动";
  if (!status.healthy) return status.message ?? "进程在但未就绪";
  // healthy: 显示端口或 PID
  if (status.port) return `:${status.port}`;
  if (status.pid != null) return `PID ${status.pid}`;
  return "OK";
}
