/** Dev 多角色测试账号切换器 — 五一 sprint 5/2 RBAC.
 *
 * 仅 dev 模式可用 (gateway /api/dev/users 在 prod 返 404, 切换器自动隐藏).
 *
 * 行为:
 *   - 启动时拉 dev_users.yaml 列表
 *   - localStorage 记当前选的 token, 跟 useMe / lib/chat / lib/quota 共用
 *   - 切换 → 立即写 localStorage + reload 页面 (让所有 hook 重 fetch)
 *
 * 显示: 顶部 banner 横条, 类似 AuthBanner. 不在 prod 显示.
 */

import { useEffect, useState } from "react";

import {
  fetchDevUsers,
  getOverrideToken,
  setOverrideToken,
  type DevUser,
} from "../lib/me";

export default function DevUserSwitcher() {
  const [users, setUsers] = useState<DevUser[] | null>(null);
  const [currentToken, setCurrentToken] = useState<string | null>(getOverrideToken());

  useEffect(() => {
    void (async () => {
      const list = await fetchDevUsers();
      setUsers(list);
    })();
  }, []);

  // 没拉到 (prod / gateway 没起 / 配置 yaml 不存在) → 整个 banner 不显示
  if (!users || users.length === 0) return null;

  const current = users.find((u) => u.token === currentToken);
  const handleSwitch = (token: string) => {
    if (token === currentToken) return;
    setOverrideToken(token || null);
    setCurrentToken(token || null);
    // reload 让所有 hook (useMe / useAudit / useQuota) 重 fetch 新 token 的数据
    window.location.reload();
  };
  const handleReset = () => {
    setOverrideToken(null);
    setCurrentToken(null);
    window.location.reload();
  };

  return (
    <div
      style={{
        background: "var(--catfish-bg-elevated)",
        borderBottom: "1px solid var(--catfish-warning, #d70)",
        padding: "var(--space-2) var(--space-4)",
        display: "flex",
        alignItems: "center",
        gap: "var(--space-3)",
        fontSize: 12,
      }}
      title="dev 模式多账号切换器, 生产模式自动隐藏"
    >
      <span style={{ fontFamily: "var(--font-mono)", color: "var(--catfish-warning, #d70)" }}>
        🧪 DEV
      </span>
      <span style={{ color: "var(--catfish-text-muted)" }}>测试账号:</span>
      <select
        value={currentToken ?? ""}
        onChange={(e) => handleSwitch(e.target.value)}
        style={{
          fontSize: 12,
          padding: "2px 6px",
          background: "var(--catfish-bg)",
          color: "var(--catfish-text)",
          border: "1px solid var(--catfish-border)",
          borderRadius: 3,
          fontFamily: "var(--font-mono)",
        }}
      >
        <option value="">— .env 默认 —</option>
        {users.map((u) => (
          <option key={u.token} value={u.token}>
            {u.role.padEnd(8, " ")} · {u.name || u.email} ({u.department || "—"})
          </option>
        ))}
      </select>
      {current && (
        <span style={{ color: "var(--catfish-text-muted)" }}>
          当前: <strong>{current.role}</strong>
          {current.managed_departments.length > 0 && (
            <> · 管 {current.managed_departments.join("/")}</>
          )}
        </span>
      )}
      {currentToken && (
        <button
          onClick={handleReset}
          style={{
            marginLeft: "auto",
            fontSize: 11,
            padding: "2px 8px",
            background: "transparent",
            color: "var(--catfish-text-muted)",
            border: "1px solid var(--catfish-border)",
            borderRadius: 3,
            cursor: "pointer",
          }}
        >
          重置 (回 .env)
        </button>
      )}
    </div>
  );
}
