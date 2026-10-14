/** 「用户」页顶部: 员工装 Companion 要的两样东西 —— 门户地址 + 接入码 (10/14)。
 *
 * 原来要管理员上服务器拿 certs/ca.pem 一台台发; 现在把这一行复制到群里就行,
 * Companion 自己取证书并用接入码核对。HTTP 部署不需要证书, 只给地址。
 */
import { useEffect, useState } from "react";

import { Section } from "../../components/DataTable";
import { fetchPairingCode } from "../../lib/pairing";
import { copyText } from "./usersShared";

export function CompanionPairing() {
  const [code, setCode] = useState<string | null | undefined>(undefined);
  const [copied, setCopied] = useState(false);
  const origin = window.location.origin;

  useEffect(() => {
    fetchPairingCode().then(setCode).catch(() => setCode(null));
  }, []);

  const https = origin.startsWith("https://");
  const text = https && code
    ? `鲶鱼 Companion 接入 —— 门户地址: ${origin}  接入码: ${code}`
    : `鲶鱼 Companion 接入 —— 门户地址: ${origin}`;

  return (
    <Section>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", fontSize: 13 }}>
        <span style={{ color: "var(--text-muted)" }}>员工接入 Companion:</span>
        <span>门户地址 <code>{origin}</code></span>
        {https && (
          <span>
            接入码 <code>{code === undefined ? "读取中…" : code ?? "读不到 (中央版本太旧? 升级后再看)"}</code>
          </span>
        )}
        <button
          type="button"
          onClick={async () => {
            setCopied(await copyText(text));
            setTimeout(() => setCopied(false), 1500);
          }}
        >
          {copied ? "已复制" : "复制发给员工"}
        </button>
      </div>
    </Section>
  );
}
