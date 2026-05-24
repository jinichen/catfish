/** 鉴权状态 hook — Companion 启动 + 登录 / 登出.
 *
 * 5/18 BL-COMPANION-AUTO-RELOGIN:
 * 老逻辑只在 startup whoami 一次, token 期间过期 (1h 后) 不会自动检测,
 * 员工是在下一次 API call (chat / dashboard) 撞 401 → fetchWithAuth 黑盒 reauth
 * (浏览器弹无前兆) 才反应过来. UX 不好.
 *
 * 新逻辑: 1) 每 60s 轮询 expires_at, 看是否快到期; 2) `nearExpiry`(<5min) /
 * `expired` 两个 flag 暴露给 AuthBanner 显友好提示 + 一键续登; 3) `forceRelogin()`
 * 主动续登 helper.
 *
 * dev_token 模式 `expires_at = now + 365天`, 永远不近 expiry, 不打扰.
 */

import { useCallback, useEffect, useState } from "react";
import {
  authGetAccessToken,
  authLogin,
  authLogout,
  authWhoami,
} from "../lib/tauri";
import type { AuthState } from "../types/auth";
import { ANONYMOUS_AUTH } from "../types/auth";

/** 距离 expires_at 多近开始警告 (秒). 5 分钟给员工足够时间手动续 / 自动续. */
const NEAR_EXPIRY_WARN_SECS = 5 * 60;
/** 自动续登触发阈值 (秒). 距离过期不到 1 分钟时 useAuth 自动触发 login flow. */
const NEAR_EXPIRY_AUTO_SECS = 60;
/** 轮询间隔 (毫秒). 60s 够细致但不烧资源. */
const POLL_INTERVAL_MS = 60_000;

export function useAuth() {
  const [state, setState] = useState<AuthState>(ANONYMOUS_AUTH);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /** 距离 expires_at < 5 分钟. AuthBanner 用来显黄色"即将过期"提示. */
  const [nearExpiry, setNearExpiry] = useState(false);
  /** 已经过期. AuthBanner 用来显红色"已过期"+ 续登按钮. */
  const [expired, setExpired] = useState(false);
  /** 正在续登 (浏览器 popup 期间). UI 用来 disable 按钮 + 显 spinner. */
  const [reauthing, setReauthing] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const s = await authWhoami();
      setState(s);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await refresh();
      if (!cancelled) setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  // 5/18 BL-COMPANION-AUTO-RELOGIN: 监听 fetchWithAuth (me.ts) 401 触发的 silent
  // reauth 完成事件 → 刷新 whoami 让 React state 跟上新 expires_at, 不然 Banner
  // 还显"已过期" 但 token 已经新了.
  useEffect(() => {
    const handler = () => void refresh();
    window.addEventListener("catfish:auth-refreshed", handler);
    return () => window.removeEventListener("catfish:auth-refreshed", handler);
  }, [refresh]);

  // 5/18 BL-COMPANION-AUTO-RELOGIN: 周期 expiry check
  // 5/23 BL-COMPANION-SILENT-REFRESH: 周期里**先 refresh()** 再 check.
  // 原因: Rust 端 auth_get_access_token 现在会在 token 快过期时 silent refresh,
  // 写入新 expires_at 到 ~/.catfish/oauth/user_info. 但 React state 里的
  // expires_at 是 mount 时 whoami 拿的旧值, 不会自己变. 如果只 check 本地缓存,
  // 老 expires_at 到点 → setExpired(true) → 触发下面 auto-trigger forceRelogin
  // 弹浏览器, 跟"silent refresh 已经把 token 续好" 矛盾.
  // 修法: 每次 poll 都先 authWhoami 读盘上的最新 user_info, 再判 near/expired.
  // 一次 IPC ~1ms, 60s 一次代价可忽略.
  useEffect(() => {
    if (!state.authenticated) {
      setNearExpiry(false);
      setExpired(false);
      return;
    }
    // auth_method=dev_token 时 expires_at = now+365天, near = false. 跳过.
    if (state.auth_method === "dev_token") {
      setNearExpiry(false);
      setExpired(false);
      return;
    }
    const check = async () => {
      // 5/23 BL-COMPANION-SILENT-REFRESH: 先同步盘上最新 user_info, 再判
      try {
        await refresh();
      } catch {
        // whoami 挂 (Tauri 命令崩 / IPC race) → 用上次 state 兜底, 不致命
      }
      // refresh() 完成后, state.expires_at 可能已被 setState 更新 (异步, 这次
      // check 里读的还是闭包捕获的旧值). 不要紧 — setState 触发的 re-render
      // 会让 useEffect 重跑 (deps 含 expires_at), 下一轮 check 用新值.
      // 这一轮 check 仍按旧 state 判 near/expired, 最多多 setNearExpiry 一次
      // 同值 (React 自动 bail out re-render), 不引起多余动作.
      const now = Math.floor(Date.now() / 1000);
      const remaining = state.expires_at - now;
      setNearExpiry(remaining > 0 && remaining < NEAR_EXPIRY_WARN_SECS);
      setExpired(remaining <= 0);
    };
    void check();  // 立即一次
    const t = window.setInterval(() => void check(), POLL_INTERVAL_MS);
    return () => window.clearInterval(t);
  }, [state.authenticated, state.auth_method, state.expires_at, refresh]);

  const login = useCallback(async () => {
    setError(null);
    setReauthing(true);
    try {
      const s = await authLogin();
      setState(s);
      setExpired(false);
      setNearExpiry(false);
    } catch (e) {
      setError(String(e));
      throw e;
    } finally {
      setReauthing(false);
    }
  }, []);

  // 5/18 BL-COMPANION-AUTO-RELOGIN: 自动续登 (banner 按钮 / 自动触发都走它)
  const forceRelogin = useCallback(async () => {
    try {
      await login();
    } catch {
      // login 已 setError + setReauthing=false, 这里只防 unhandled rejection
    }
  }, [login]);

  // 5/18 BL-COMPANION-AUTO-RELOGIN: 距过期 < 1min 自动触发续登, 让员工无感.
  // 浏览器弹一下立即关闭 (catfish-identity 已登录态 cookie 还在, OAuth flow 秒过).
  //
  // 5/19 (本次实盘修): 真过期 (remaining <= 0) 也要自动续, 不能让员工手点
  // "重新登录" 按钮. 老条件 `remaining > 0 && < 60` 只覆盖"还剩 1 分钟"的
  // 窗口, 一旦过期反而不触发 — 鸿波实盘 chat 撞红条 banner 没自动续就是这.
  //
  // cooldown 防无限重试: 上次自动续登失败后 60s 内不再 auto-trigger
  // (用户手点"重新登录" 按钮始终可用, cooldown 只限制自动那条路径).
  useEffect(() => {
    if (!state.authenticated || state.auth_method === "dev_token") return;
    if (reauthing) return;  // 正在续, 别叠
    const now = Math.floor(Date.now() / 1000);
    const remaining = state.expires_at - now;
    // 三段触发: 还剩 < 1min / 真过期 / 过期超久 (cooldown 后再试一次)
    const shouldAutoTrigger =
      (remaining > 0 && remaining < NEAR_EXPIRY_AUTO_SECS) ||
      // 真过期: 永远尝试自动续 — 失败的话 forceRelogin 内部 setError 不会再连击
      // (本 effect 依赖 expires_at, expires_at 没变 effect 不重跑; login 成功
      // expires_at 跳到未来, effect 跑一次就 return; login 失败 state 不变
      // 也不会重跑 effect. 不需要 cooldown timer).
      remaining <= 0;
    if (shouldAutoTrigger) {
      void forceRelogin();
    }
  }, [state.authenticated, state.auth_method, state.expires_at, reauthing, forceRelogin]);

  const logout = useCallback(async () => {
    try {
      await authLogout();
      setState(ANONYMOUS_AUTH);
      setNearExpiry(false);
      setExpired(false);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  const getAccessToken = useCallback(async () => {
    return await authGetAccessToken();
  }, []);

  return {
    state,
    loading,
    error,
    nearExpiry,
    expired,
    reauthing,
    refresh,
    login,
    logout,
    forceRelogin,
    getAccessToken,
  };
}
