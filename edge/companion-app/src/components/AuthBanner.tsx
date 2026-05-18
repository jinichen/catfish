/** 鉴权状态横幅 — 顶部薄条.
 *
 * 显示规则 (按优先级, 上面的胜出):
 *   - reauthing → 蓝色"正在续登..." (5/18 BL-COMPANION-AUTO-RELOGIN)
 *   - expired   → 红色"登录已过期 [重新登录]" 按钮 (5/18)
 *   - nearExpiry→ 黄色"登录即将过期, [续登]" 按钮 (5/18)
 *   - auth_method='dev_token' → 黄色 warning "你在用开发模式 token..."
 *   - auth_method='oidc'      → 不显示 (正常状态, 不打扰员工)
 *
 * 决策 6 (docs/AUTH-DESIGN.md § 13): dev_token 在 prod 也保留作兜底, 但 UI 必须警告.
 */

import { useAuth } from "../hooks/useAuth";

const BANNER_BASE: React.CSSProperties = {
  borderBottom: "1px solid",
  fontSize: 12,
  padding: "6px 16px",
  textAlign: "center" as const,
};

const BTN_BASE: React.CSSProperties = {
  marginLeft: 12,
  padding: "2px 10px",
  fontSize: 11,
  fontWeight: 600,
  borderRadius: 4,
  border: "1px solid currentColor",
  background: "transparent",
  color: "inherit",
  cursor: "pointer",
};

export default function AuthBanner() {
  const { state, nearExpiry, expired, reauthing, forceRelogin } = useAuth();

  // 5/18 BL-COMPANION-AUTO-RELOGIN: reauth 进行中 → 蓝色提示
  if (reauthing) {
    return (
      <div
        style={{
          ...BANNER_BASE,
          background: "rgba(56, 189, 248, 0.15)",
          borderBottomColor: "rgba(56, 189, 248, 0.4)",
          color: "#38bdf8",
        }}
      >
        🔄 正在续登... (浏览器会自动打开 SSO 页面, 已登录的话秒过)
      </div>
    );
  }

  // 5/18 BL-COMPANION-AUTO-RELOGIN: 已过期 → 红色 + 一键续登
  if (expired) {
    return (
      <div
        style={{
          ...BANNER_BASE,
          background: "rgba(239, 68, 68, 0.15)",
          borderBottomColor: "rgba(239, 68, 68, 0.4)",
          color: "#ef4444",
        }}
      >
        🔒 登录已过期, 部分功能不可用.
        <button onClick={() => void forceRelogin()} style={BTN_BASE}>
          重新登录
        </button>
      </div>
    );
  }

  // 5/18 BL-COMPANION-AUTO-RELOGIN: 快过期 → 黄色提醒 (不阻塞, 但提示)
  if (nearExpiry) {
    return (
      <div
        style={{
          ...BANNER_BASE,
          background: "rgba(251, 191, 36, 0.15)",
          borderBottomColor: "rgba(251, 191, 36, 0.4)",
          color: "#fbbf24",
        }}
      >
        ⏰ 登录即将过期 (5 分钟内).
        <button onClick={() => void forceRelogin()} style={BTN_BASE}>
          现在续登
        </button>
        <span style={{ marginLeft: 12, opacity: 0.7 }}>
          (不点也行, 过期前 1 分钟会自动续)
        </span>
      </div>
    );
  }

  // dev_token 警告 (原行为, 不变)
  if (state.auth_method !== "dev_token") {
    return null;
  }

  return (
    <div
      style={{
        ...BANNER_BASE,
        background: "rgba(251, 191, 36, 0.15)",
        borderBottomColor: "rgba(251, 191, 36, 0.4)",
        color: "#fbbf24",
      }}
    >
      ⚠ 你正在用 <strong>开发模式 token</strong>, 不是公司 SSO 登录.
      生产环境应该 unset CATFISH_DEV_TOKEN, 走真 SSO. 当前作为 IT 救急 / 演示用.
    </div>
  );
}
