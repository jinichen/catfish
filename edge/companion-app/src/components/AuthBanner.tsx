/** 鉴权状态横幅 — 顶部薄条.
 *
 * 显示规则:
 *   - auth_method='dev_token' → 黄色 warning "你在用开发模式 token, 不是真 SSO. 别在生产环境用."
 *   - auth_method='oidc' → 不显示 (正常状态, 不打扰员工)
 *   - 其他 → 不显示
 *
 * 决策 6 (docs/AUTH-DESIGN.md § 13): dev_token 在 prod 也保留作兜底, 但 UI 必须警告.
 */

import { useAuth } from "../hooks/useAuth";

export default function AuthBanner() {
  const { state } = useAuth();

  if (state.auth_method !== "dev_token") {
    return null;
  }

  return (
    <div
      style={{
        background: "rgba(251, 191, 36, 0.15)",
        borderBottom: "1px solid rgba(251, 191, 36, 0.4)",
        color: "#fbbf24",
        fontSize: 12,
        padding: "6px 16px",
        textAlign: "center",
      }}
    >
      ⚠ 你正在用 <strong>开发模式 token</strong>, 不是公司 SSO 登录.
      生产环境应该 unset CATFISH_DEV_TOKEN, 走真 SSO. 当前作为 IT 救急 / 演示用.
    </div>
  );
}
