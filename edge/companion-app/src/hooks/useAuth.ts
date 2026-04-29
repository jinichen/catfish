/** 鉴权状态 hook — Companion 启动 + 登录 / 登出. */

import { useCallback, useEffect, useState } from "react";
import {
  authGetAccessToken,
  authLogin,
  authLogout,
  authWhoami,
} from "../lib/tauri";
import type { AuthState } from "../types/auth";
import { ANONYMOUS_AUTH } from "../types/auth";

export function useAuth() {
  const [state, setState] = useState<AuthState>(ANONYMOUS_AUTH);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  const login = useCallback(async () => {
    setError(null);
    try {
      const s = await authLogin();
      setState(s);
    } catch (e) {
      setError(String(e));
      throw e;
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await authLogout();
      setState(ANONYMOUS_AUTH);
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
    refresh,
    login,
    logout,
    getAccessToken,
  };
}
