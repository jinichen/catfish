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
    const check = () => {
      const now = Math.floor(Date.now() / 1000);
      const remaining = state.expires_at - now;
      setNearExpiry(remaining > 0 && remaining < NEAR_EXPIRY_WARN_SECS);
      setExpired(remaining <= 0);
    };
    check();  // 立即一次
    const t = window.setInterval(check, POLL_INTERVAL_MS);
    return () => window.clearInterval(t);
  }, [state.authenticated, state.auth_method, state.expires_at]);

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
  useEffect(() => {
    if (!state.authenticated || state.auth_method === "dev_token") return;
    const now = Math.floor(Date.now() / 1000);
    const remaining = state.expires_at - now;
    if (remaining > 0 && remaining < NEAR_EXPIRY_AUTO_SECS && !reauthing) {
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
