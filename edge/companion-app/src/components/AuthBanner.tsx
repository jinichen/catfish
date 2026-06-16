/** 鉴权状态横幅 — 顶部薄条.
 *
 * 显示规则 (按优先级, 上面的胜出):
 *   - reauthing → 蓝色"正在续登..." (5/18 BL-COMPANION-AUTO-RELOGIN)
 *   - auth_method='dev_token' → 黄色 warning "你在用开发模式 token..."
 *   - auth_method='oidc'      → 不显示 (正常状态, 不打扰员工)
 *
 * 决策 6 (docs/AUTH-DESIGN.md § 13): dev_token 在 prod 也保留作兜底, 但 UI 必须警告.
 *
 * 5/22 鸿波: **expired 红条删掉**. 5/19 已经做了"真过期永远自动续登"
 * (useAuth.ts:131-147), 红条 = stale noise — UI 显"已过期" 而 fetchWithAuth 401
 * silent reauth 已经把 token 续上, 员工看见红条但 chat 能用, 反而误导.
 *
 * P3.5.14 (6/16 鸿波): **nearExpiry 黄条也删掉**, 跟 expired 红条同款逻辑.
 * useAuth `NEAR_EXPIRY_AUTO_SECS=60` 触发自动续登, 距过期 < 1min 时 forceRelogin
 * 自动跑, 浏览器秒过员工无感. 老 banner 在 < 5min 显 "登录即将过期" + 自带
 * 文案 "(不点也行, 过期前 1 分钟会自动续)" — 它自己都承认会自动续, 提前 4 分钟
 * 占顶部 4 分钟纯噪声. 鸿波反馈"都自动续登, 为什么还要提示?". 砍.
 *
 * expired / nearExpiry state 和 forceRelogin 在 useAuth 里保留 (内部自动续登要用),
 * 只是 UI 不展示. AuthBanner 现只显 reauthing (实际续登中, 浏览器弹) + dev_token
 * warning. 平时 OIDC 正常登录 → AuthBanner 静默, 不打扰员工.
 */

import { useAuth } from "../hooks/useAuth";

const BANNER_BASE: React.CSSProperties = {
  borderBottom: "1px solid",
  fontSize: 12,
  padding: "6px 16px",
  textAlign: "center" as const,
};
// P3.5.14: BTN_BASE 砍 — 老 nearExpiry banner "现在续登" 按钮唯一用法, 整段已删.

export default function AuthBanner() {
  const { state, reauthing } = useAuth();

  // 5/18 BL-COMPANION-AUTO-RELOGIN: reauth 进行中 → 蓝色提示
  // (浏览器 SSO popup 期间真显, 不是 stale noise)
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

  // 5/22 expired 红条 + P3.5.14 nearExpiry 黄条都删 — 自动续登覆盖了, banner
  // 是 stale noise. 详见文件顶部注释. state 在 useAuth 内仍计算, 只是 UI 不显.

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
