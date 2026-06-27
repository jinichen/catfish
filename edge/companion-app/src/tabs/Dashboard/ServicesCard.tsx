/** 仪表盘 · 服务概览卡片
 *
 * 状态:
 *   - **Tool Bridge / Chrome / Local Search** (3 个真本地服务) — 边缘端 Companion
 *     可启停 + 重启. 它们是单 Mac 单员工的进程, 跟 Companion 进程绑定.
 *   - **LLM Gateway** (1 个中央端服务) — 只读监控. 状态点 + 端口, 没启停按钮.
 *
 * BL-COMPANION-SERVICES-DEMOTE (5/17 鸿波): 老逻辑允许 Companion 启停 Gateway,
 * 违 BL-CENTRAL-EDGE-BOUNDARY 操作面 — Gateway 是中央服务 (未来 SaaS 化跑客户
 * 机房 / 云端), 边缘 Companion 不该有启停权. SaaS 化后启停按钮点了也没用,
 * 多员工共享时一员工停 → 全公司断. 现在就撤掉, 改只读监控 (健康检查 only).
 *
 * 为啥 Dashboard 还要显示 Gateway:
 *   员工不能控制 != 不能看. Gateway 挂的话员工聊天用不了, 要能 spotting 到
 *   红点 → 联系 ops 处理. 跟"信号塔在不在线"同性质.
 */

import { useServicesStore } from "../../store/services";
import { useServiceStatus } from "../../hooks/useServiceStatus";
import { useAgentStore } from "../../store/agent";
import StatusDot from "../../components/StatusDot";
import type { ServiceId, ServiceStatus } from "../../types/service";
import {
  chromeLaunch,
  chromeKill,
  localSearchStart,
  localSearchStop,
  toolBridgeStart,
  toolBridgeStop,
} from "../../lib/tauri";

// BL-COMPANION-SERVICES-DEMOTE (5/17): gateway 不在 SERVICE_ACTIONS 里了 —
// 边缘 Companion 无权启停中央服务. 表里只剩 3 个真本地服务.
// P3.5.125 (6/26): hermes 也排除 — 由 launchd 管, Companion 只 hermes_kill 触发拉.
// 类型 Pick 防新增 ServiceId 时漏改这个表.
type LocalServiceId = Exclude<ServiceId, "gateway" | "hermes">;
const SERVICE_ACTIONS: Record<
  LocalServiceId,
  { start: () => Promise<unknown>; stop: () => Promise<unknown> }
> = {
  chrome: { start: chromeLaunch, stop: chromeKill },
  local_search: { start: localSearchStart, stop: localSearchStop },
  tool_bridge: { start: toolBridgeStart, stop: toolBridgeStop },
};

async function restartService(id: ServiceId): Promise<void> {
  // BL-COMPANION-SERVICES-DEMOTE (5/17): gateway 不可重启, 这里直接 return.
  // P3.5.125 (6/26): hermes 也跳过 — 由 watchdog 自动 kill (走 hermesKill 触发 launchd)
  if (id === "gateway" || id === "hermes") return;
  const a = SERVICE_ACTIONS[id as LocalServiceId];
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
      // BL-COMPANION-SERVICES-DEMOTE (5/17): 文案改成"中央服务"明确身份, 提示
      // 员工挂了找 IT/ops, 不要自己尝试重启 (按钮也没了).
      why: "中央服务 (你 mac 上是过渡, 未来云端). 挂了联系 IT, 没起来 = 聊天用不了",
    },
    // P3.5.125 (6/26 鸿波 catch "hermes 没监控"): hermes API server (8642),
    // 真**: 真**:** chat / 早安 / cron / wechat 真**都依赖**. hang 时连续 3 次
    // /healthz 失败 → 自动 kill -9 触发 launchd 拉.
    {
      id: "hermes",
      name: "Hermes Agent",
      why: `${agentName}核心 runtime (chat / 早安 / cron / 微信). hang 自动重启, 不用管`,
    },
    {
      id: "tool_bridge",
      name: "Tool Bridge",
      why: `暴露 60+ 工具给${agentName}, 没起来 = ${agentName}无工具瞎答`,
    },
    {
      id: "chrome",
      name: "Catfish Chrome",
      why: "浏览器自动化 (browser_navigate 等), 不需要可不起. page-level hang 自动重启",
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
        <strong>服务状态</strong>
        {/* BL-COMPANION-SERVICES-DEMOTE (5/17): batch 按钮**只动 3 个本地服务**,
            不再控制 Gateway (中央端). 按钮文案改 "本地全启 / 本地全停 / 本地全重启"
            明确范围. Gateway 行单独显示状态点, 没按钮.
            真要看 log 走 ~/Library/Logs/Catfish/*.log 文件. */}
        <div style={{ display: "flex", gap: 4 }}>
          <button
            onClick={() => {
              void Promise.allSettled([
                chromeLaunch(),
                localSearchStart(),
                toolBridgeStart(),
              ]);
            }}
            title="一键启动 3 个本地服务 (Tool Bridge / Chrome / Local Search)"
            style={batchBtnStyle}
          >
            本地全启
          </button>
          <button
            onClick={() => {
              void Promise.allSettled([
                chromeKill(),
                localSearchStop(),
                toolBridgeStop(),
              ]);
            }}
            title="一键停止 3 个本地服务"
            style={batchBtnStyle}
          >
            本地全停
          </button>
          <button
            onClick={async () => {
              await Promise.allSettled([
                chromeKill(),
                localSearchStop(),
                toolBridgeStop(),
              ]);
              await new Promise((r) => setTimeout(r, 800));
              await Promise.allSettled([
                chromeLaunch(),
                localSearchStart(),
                toolBridgeStart(),
              ]);
            }}
            title="一键重启 3 个本地服务"
            style={batchBtnStyle}
          >
            本地全重启
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
            员工 90% 撞 bug 时想做的就是"重启这个服务", 直接前置.
            BL-COMPANION-SERVICES-DEMOTE (5/17): Gateway 不显示重启按钮 —
            中央服务边缘不该有启停权. 留个占位灰字"只读"提示员工知道. */}
        {row.id === "gateway" ? (
          <span
            style={{
              fontSize: 10,
              color: "var(--catfish-text-muted)",
              padding: "2px 8px",
              border: "1px dashed var(--catfish-border)",
              borderRadius: "var(--radius-sm)",
            }}
            title="中央服务由 IT / ops 管理, Companion 只读监控状态"
          >
            只读
          </span>
        ) : (
          <button
            onClick={() => void restartService(row.id)}
            title={`重启 ${row.name}`}
            style={rowBtnStyle}
          >
            ↻
          </button>
        )}
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
