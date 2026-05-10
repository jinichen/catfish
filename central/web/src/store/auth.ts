/** 全局 auth store (BL-ARCH1 5/10).
 *
 * 启动时调 fetchMe() 拉真员工信息 (chenhongbo / engineering / admin).
 * 没登录 → null, App.tsx 判断后跳 IdP.
 */

import { create } from "zustand";

import type { MeInfo } from "../lib/me";

interface AuthState {
  me: MeInfo | null;
  loading: boolean;
  error: string | null;
  setMe: (me: MeInfo | null) => void;
  setLoading: (v: boolean) => void;
  setError: (e: string | null) => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  me: null,
  loading: true,
  error: null,
  setMe: (me) => set({ me, loading: false, error: null }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error, loading: false }),
}));
