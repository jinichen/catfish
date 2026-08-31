/** Tools list cache — 抽自 useChat.ts (5/20 拆分).
 *
 * 60s TTL + KEY_TOOLS missing detection + 显式 clear. tool_bridge 启动早 + skill
 * 装新 工具会被自动 invalidate. 失败不缓存, 下次 send 重试.
 */

import { toolBridgeListTools } from "../../lib/tauri";
import type { OpenAITool } from "../../lib/chat";

const KEY_TOOLS = ["catfish_run_skill"];
let _cachedTools: OpenAITool[] | null = null;
let _cachedAt = 0;
const _TOOLS_CACHE_TTL_MS = 60_000;

function _cacheHasAllKeyTools(cached: OpenAITool[]): boolean {
  const names = new Set(cached.map((t) => t.function.name));
  return KEY_TOOLS.every((kt) => names.has(kt));
}

export async function ensureTools(): Promise<OpenAITool[]> {
  const now = Date.now();
  if (
    _cachedTools !== null
    && now - _cachedAt < _TOOLS_CACHE_TTL_MS
    && _cacheHasAllKeyTools(_cachedTools)
  ) {
    return _cachedTools;
  }
  if (_cachedTools !== null && !_cacheHasAllKeyTools(_cachedTools)) {
    console.info("[catfish chat] cache 里缺关键工具, 强制重拉 tool_bridge");
  }
  try {
    const list = await toolBridgeListTools();
    const usable = list.filter((t) => t.available);
    const wire: OpenAITool[] = usable.map((t) => ({
      type: "function" as const,
      function: {
        name: t.name,
        description: t.description,
        parameters: (t.input_schema as Record<string, unknown>) ?? {
          type: "object",
          properties: {},
        },
      },
    }));
    _cachedTools = wire;
    _cachedAt = Date.now();
    console.info(
      `[catfish chat] 加载 ${wire.length}/${list.length} 个工具(${list.length - wire.length} 个当前终端不可用, TTL 60s)`,
    );
    return wire;
  } catch (e) {
    console.warn("[catfish chat] tool_bridge 不可达, 跳过 tools(下次重试):", e);
    return [];
  }
}

/** 切账号 / 重启 tool_bridge / 装新 skill 后调一次清缓存让 ensureTools 重拉 */
export function _clearToolsCache(): void {
  _cachedTools = null;
  _cachedAt = 0;
}
