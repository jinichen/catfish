/** 微信聊天导出文件分析授权。
 *
 * 模型没有第二套配置：读取结果回到当前聊天，分析沿用 useChatStore.model。
 */
import * as React from "react";
import { invoke } from "@tauri-apps/api/core";

import { useChatStore } from "../../store/chat";

interface ArchiveStatus {
  supported: boolean;
  helperPath: string;
  helperInstalled: boolean;
  sourceType: string | null;
  sourcePath: string | null;
  sourceReady: boolean;
  enabled: boolean;
  authorized: boolean;
  currentPickerModel: string | null;
  consentedPickerModel: string | null;
  requiresReauthorization: boolean;
  message: string;
}

export default function WeChatArchiveCard() {
  const pickerModel = useChatStore((state) => state.model);
  const [status, setStatus] = React.useState<ArchiveStatus | null>(null);
  const [acknowledged, setAcknowledged] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const reload = React.useCallback(async () => {
    try {
      setStatus(await invoke<ArchiveStatus>("wechat_archive_status"));
      setError(null);
    } catch (err) {
      setError(String(err));
    }
  }, []);

  React.useEffect(() => {
    void reload();
  }, [reload, pickerModel]);

  const enable = async () => {
    setBusy(true);
    setError(null);
    try {
      const next = await invoke<ArchiveStatus>("wechat_archive_enable", {
        acknowledged,
      });
      setStatus(next);
      setAcknowledged(false);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const disable = async () => {
    setBusy(true);
    setError(null);
    try {
      setStatus(await invoke<ArchiveStatus>("wechat_archive_disable"));
      setAcknowledged(false);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const pickExport = async () => {
    setBusy(true);
    setError(null);
    try {
      setStatus(await invoke<ArchiveStatus>("wechat_archive_pick_export"));
      setAcknowledged(false);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const clearSource = async () => {
    setBusy(true);
    setError(null);
    try {
      setStatus(await invoke<ArchiveStatus>("wechat_archive_clear_source"));
      setAcknowledged(false);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  const sourceLabel = status?.sourcePath?.split(/[\\/]/).pop();

  return (
    <section
      style={{
        border: "1px solid var(--catfish-border)",
        borderRadius: "var(--radius-md)",
        background: "var(--catfish-bg-elevated)",
        padding: "var(--space-4)",
        minWidth: 0,
      }}
    >
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <h3 style={{ margin: 0 }}>导出聊天记录分析</h3>
        {status?.authorized && (
          <span style={{ color: "var(--status-ok, #2a8b3f)", fontSize: 12 }}>已授权</span>
        )}
        {status?.requiresReauthorization && (
          <span style={{ color: "var(--status-warn, #c98b00)", fontSize: 12 }}>
            Picker 已变化
          </span>
        )}
      </div>

      <p style={{ margin: "12px 0", fontSize: 14, lineHeight: 1.65 }}>
        无需绑定微信账号。选择 JSON、JSONL 或 CSV 导出文件后，即可授权分析；不会自动读取微信。
      </p>
      {status?.sourceReady && (
        <p style={{ margin: "12px 0", fontSize: 14, overflowWrap: "anywhere" }}>
          分析模型：<code>{pickerModel || status.currentPickerModel || "尚未选择"}</code>
        </p>
      )}

      {status && !status.supported && (
        <p style={{ margin: "6px 0", fontSize: 12, color: "var(--catfish-text-muted)" }}>
          当前系统暂不支持聊天导出文件分析。
        </p>
      )}

      {status?.supported && (
        <div style={{ margin: "12px 0", display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <button type="button" onClick={() => void pickExport()} disabled={busy} style={button(true)}>
            {status.sourceType === "export_file" && status.sourceReady ? "更换导出文件" : "选择导出文件"}
          </button>
          {status.sourceType === "export_file" && status.sourceReady && sourceLabel && (
            <>
              <span style={{ fontSize: 14, overflowWrap: "anywhere", minWidth: 0 }} title={status.sourcePath || undefined}>✓ {sourceLabel}</span>
              <button type="button" onClick={() => void clearSource()} disabled={busy} style={button(false)}>
                移除
              </button>
            </>
          )}
        </div>
      )}

      {status?.supported && !status.helperInstalled && (
        <div
          style={{
            padding: 8,
            borderRadius: 4,
            background: "rgba(201, 139, 0, 0.06)",
            fontSize: 14,
            lineHeight: 1.6,
          }}
        >
          <div>聊天导出读取器未随当前安装就绪。</div>
          <code style={{ overflowWrap: "anywhere" }}>{status.helperPath}</code>
          <div style={{ color: "var(--catfish-text-muted)" }}>
            重新运行 Companion 安装准备即可补装；本模式不会抓取密钥或修改微信客户端。
          </div>
        </div>
      )}

      {status?.supported && status.helperInstalled && status.sourceReady && !status.authorized && (
        <label
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: 6,
            fontSize: 12,
            lineHeight: 1.55,
            cursor: "pointer",
          }}
        >
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(event) => setAcknowledged(event.target.checked)}
          />
          <span>
            我确认：记录只在本机读取，但查询结果会交给当前 Picker 模型处理。
            如果切换 Picker，本授权自动失效并要求重新确认。
          </span>
        </label>
      )}

      {status && (
        <div style={{ marginTop: 8, fontSize: 11, color: "var(--catfish-text-muted)" }}>
          {status.message}
          {status.enabled && status.consentedPickerModel && (
            <> · 上次授权模型 <code>{status.consentedPickerModel}</code></>
          )}
        </div>
      )}

      {error && (
        <div style={{ marginTop: 8, color: "var(--status-err, #c93a3a)", fontSize: 12 }}>
          {error}
        </div>
      )}

      {status?.supported && status.helperInstalled && status.sourceReady && (
        <div style={{ marginTop: 10 }}>
          {status.authorized ? (
            <button type="button" onClick={() => void disable()} disabled={busy} style={button(false)}>
              {busy ? "处理中…" : "关闭历史分析"}
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void enable()}
              disabled={busy || !acknowledged || !status.currentPickerModel}
              style={{ ...button(true), opacity: busy || !acknowledged || !status.currentPickerModel ? 0.5 : 1, cursor: busy || !acknowledged || !status.currentPickerModel ? "not-allowed" : "pointer" }}
            >
              {busy ? "安全检查中…" : "确认并启用"}
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function button(primary: boolean): React.CSSProperties {
  return {
    border: `1px solid ${primary ? "var(--catfish-accent, #2a7fbb)" : "var(--catfish-border)"}`,
    background: primary ? "var(--catfish-accent, #2a7fbb)" : "transparent",
    color: primary ? "#fff" : "inherit",
    borderRadius: 4,
    padding: "8px 16px",
    minHeight: 40,
    cursor: "pointer",
    fontSize: 14,
  };
}
