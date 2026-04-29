/** Companion 端鉴权状态 — 跟 commands/auth.rs AuthState 对齐. */

export interface AuthState {
  authenticated: boolean;
  email: string;
  name: string;
  department: string;
  tier: string;
  /** 'oidc' / 'dev_token' / ''. UI 用来决定要不要显 dev_token warning banner. */
  auth_method: string;
  expires_at: number;
}

export const ANONYMOUS_AUTH: AuthState = {
  authenticated: false,
  email: "",
  name: "",
  department: "",
  tier: "",
  auth_method: "",
  expires_at: 0,
};
