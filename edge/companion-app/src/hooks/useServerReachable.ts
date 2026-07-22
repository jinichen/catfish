/** BL-SERVER-REACHABILITY-CHECK (7/16): Companion 启动检测中央服务器连通性.
 *
 * 用户 pushback: 员工首启 Companion, 若中央服务器不通, 点登录浏览器会挂 (URL 错 /
 * 端点不通 / 员工没配公司 URL). 死循环. Fix: 启动时先 ping identity + gateway,
 * 不通 → 弹配置卡让员工填公司 URL, 通了才显示"登录".
 *
 * 检测:
 *   - identity: GET {identity_url}/.well-known/openid-configuration (200 = 通)
 *   - gateway:  GET {gateway_url}/healthz (200 = 通)
 * 双通 = reachable. 任一不通 = 显示配置卡.
 *
 * 触发时机:
 *   - Companion 启动 (App.tsx useEffect)
 *   - 员工在配置卡里改 URL + 保存 → retest() 手动触发
 *   - config 变化 (write_server_config 成功) → auto retest
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

export interface ServerReachableState {
  checking: boolean;
  reachable: boolean;
  identityReachable: boolean;
  gatewayReachable: boolean;
  identityUrl: string;
  gatewayUrl: string;
  lastError: string | null;
  lastCheckedAt: number | null;
}

const initial: ServerReachableState = {
  checking: true,   // 首次启动默认 checking, 避免闪一下"不通"
  reachable: false,
  identityReachable: false,
  gatewayReachable: false,
  identityUrl: "",
  gatewayUrl: "",
  lastError: null,
  lastCheckedAt: null,
};

const PING_TIMEOUT_MS = 3000;   // 每个 endpoint 3s 超时, 不然启动久等
const PING_DEBOUNCE_MS = 500;    // 员工连续改 URL 时 debounce 500ms 再 retest

/** 手工 fetch with timeout · AbortController Web API.
 *
 * BL-CSP-PROXY (7/18 鸿波): 走 fetchViaProxy (Rust reqwest), 不直接 fetch — 员工输
 * 远端 IP 时 build 版被 CSP connect-src 拦 (dev 用 vite HMR self origin 不拦 · 有陷阱).
 */
async function fetchWithTimeout(url: string, ms: number): Promise<Response> {
  const { fetchViaProxy } = await import("../lib/http_proxy");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  try {
    return await fetchViaProxy(url, {
      signal: controller.signal,
      // 关: 不允许 credentials · 简单 CORS · 只查 status
      credentials: "omit",
      // 关: 不需要 cache · 每次 fresh
      cache: "no-store",
    });
  } finally {
    clearTimeout(timer);
  }
}

export function useServerReachable() {
  const [state, setState] = useState<ServerReachableState>(initial);
  const debounceRef = useRef<number | null>(null);

  const check = useCallback(async () => {
    setState((s) => ({ ...s, checking: true }));

    // 1. 拿当前 config
    let identityUrl = "";
    let gatewayUrl = "";
    try {
      const cfg = await invoke<{
        gateway_url: string;
        identity_url: string;
      }>("read_server_config");
      identityUrl = cfg.identity_url || "";
      gatewayUrl = cfg.gateway_url || "";
    } catch (e) {
      setState({
        ...initial,
        checking: false,
        lastError: `读 config 失败: ${e}`,
        lastCheckedAt: Date.now(),
      });
      return;
    }

    // 2. 并行 ping identity + gateway
    const [identityRes, gatewayRes] = await Promise.allSettled([
      identityUrl
        ? fetchWithTimeout(
            `${identityUrl.replace(/\/$/, "")}/.well-known/openid-configuration`,
            PING_TIMEOUT_MS,
          )
        : Promise.reject(new Error("identity_url 未配")),
      gatewayUrl
        ? fetchWithTimeout(`${gatewayUrl.replace(/\/$/, "")}/healthz`, PING_TIMEOUT_MS)
        : Promise.reject(new Error("gateway_url 未配")),
    ]);

    const identityReachable =
      identityRes.status === "fulfilled" && identityRes.value.ok;
    const gatewayReachable =
      gatewayRes.status === "fulfilled" && gatewayRes.value.ok;

    // 3. 收集 error 信息 (给员工看提示用)
    const errors: string[] = [];
    if (!identityReachable) {
      const reason =
        identityRes.status === "rejected"
          ? (identityRes.reason as Error).message
          : `HTTP ${(identityRes as PromiseFulfilledResult<Response>).value.status}`;
      errors.push(`认证服务不通 (${identityUrl || "URL 未配"}): ${reason}`);
    }
    if (!gatewayReachable) {
      const reason =
        gatewayRes.status === "rejected"
          ? (gatewayRes.reason as Error).message
          : `HTTP ${(gatewayRes as PromiseFulfilledResult<Response>).value.status}`;
      errors.push(`网关不通 (${gatewayUrl || "URL 未配"}): ${reason}`);
    }

    setState({
      checking: false,
      reachable: identityReachable && gatewayReachable,
      identityReachable,
      gatewayReachable,
      identityUrl,
      gatewayUrl,
      lastError: errors.length ? errors.join("; ") : null,
      lastCheckedAt: Date.now(),
    });
  }, []);

  /** 员工在配置卡改 URL 时用 · debounce 500ms 再 retest */
  const retest = useCallback(() => {
    if (debounceRef.current) {
      window.clearTimeout(debounceRef.current);
    }
    debounceRef.current = window.setTimeout(() => {
      void check();
    }, PING_DEBOUNCE_MS);
  }, [check]);

  // Companion 启动跑一次
  useEffect(() => {
    void check();
    // 清 debounce (unmount 保护)
    return () => {
      if (debounceRef.current) {
        window.clearTimeout(debounceRef.current);
      }
    };
  }, [check]);

  return { ...state, retest, checkNow: check };
}
