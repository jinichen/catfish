/** 「用户」页顶部: 员工装 Companion 要的东西 —— 门户地址 + 接入码, 合成一条接入链接 (10/14)。
 *
 * 原来要管理员上服务器拿 certs/ca.pem 一台台发; 现在复制这一段发到群里:
 *   · 落地页链接 (http, 微信里点得开) → 页面上「打开鲶鱼 Companion」→ Companion 弹确认框, 一键接入
 *   · catfish:// 链接 (点不开时粘到 Companion「服务器配置 → 接入链接」)
 * Companion 用接入码核对自动取回的公司证书。HTTP 部署没有证书, 链接里只有地址。
 */
import { useEffect, useState } from "react";

import { Section } from "../../components/DataTable";
import { catfishConnectLink, landingPageLink } from "../../lib/connectLink";
import { publicHttpPort } from "../../lib/env";
import { fetchPairingCode } from "../../lib/pairing";
import { copyText } from "./usersShared";

export function CompanionPairing() {
  const [code, setCode] = useState<string | null | undefined>(undefined);
  const [copied, setCopied] = useState(false);
  const origin = window.location.origin;
  const https = origin.startsWith("https://");

  useEffect(() => {
    if (!https) { setCode(null); return; }
    fetchPairingCode().then(setCode).catch(() => setCode(null));
  }, [https]);

  const usable = https ? code ?? null : null;
  const page = landingPageLink(origin, usable, publicHttpPort);
  const app = catfishConnectLink(origin, usable);
  const text = [
    "鲶鱼 Companion 接入: 装好 Companion 后打开下面的链接, 点「打开鲶鱼 Companion」",
    page,
    `(点不开的话, 把这串粘到 Companion「服务器配置 → 接入链接」: ${app})`,
    `门户地址 ${origin}${usable ? `  接入码 ${usable}` : ""}`,
  ].join("\n");

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
          disabled={code === undefined}
          title={text}
          onClick={async () => {
            setCopied(await copyText(text));
            setTimeout(() => setCopied(false), 1500);
          }}
        >
          {copied ? "已复制" : "复制接入链接发给员工"}
        </button>
      </div>
    </Section>
  );
}
