/** 「用户」页顶部: 员工装 Companion 要的东西 —— 门户地址 + 接入码, 合成一条接入链接 (10/14)。
 *
 * 原来要管理员上服务器拿 certs/ca.pem 一台台发; 现在复制这一段发到群里:
 *   · 落地页链接 (http, 微信里点得开) → 页面上「打开鲶鱼 Companion」→ Companion 弹确认框, 一键接入
 *   · catfish:// 链接 (点不开时粘到 Companion「服务器配置 → 接入链接」)
 * Companion 用接入码核对自动取回的公司证书。HTTP 部署没有证书, 链接里只有地址。
 *
 * 11/08 (IP + 域名 / 公网映射): 配了多个对外地址时每个地址各一段 —— 内网 IP 那段跟以前一样;
 * 域名 / 公网映射那段的网关 / 认证都走门户的 https 地址 (外面只够得着这一个端口)。
 */
import { useEffect, useState } from "react";

import { Section } from "../../components/DataTable";
import { connectEntries } from "../../lib/connectLink";
import { allIssuers, publicHttpPort } from "../../lib/env";
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
  // 只有一个地址 (没配 PUBLIC_HOST) 时就是当前门户地址, 跟以前完全一样
  const addresses = https && allIssuers.length > 1 ? allIssuers : [origin];
  const entries = connectEntries(addresses, usable, publicHttpPort);
  const text = entries.length === 1
    ? [
        "鲶鱼 Companion 接入: 装好 Companion 后打开下面的链接, 点「打开鲶鱼 Companion」",
        entries[0].page,
        `(点不开的话, 把这串粘到 Companion「服务器配置 → 接入链接」: ${entries[0].app})`,
        `门户地址 ${origin}${usable ? `  接入码 ${usable}` : ""}`,
      ].join("\n")
    : [
        "鲶鱼 Companion 接入: 装好 Companion 后打开下面对应的链接, 点「打开鲶鱼 Companion」",
        ...entries.flatMap((e) => [
          "",
          e.internal ? `【在公司内网】门户地址 ${e.web}` : `【用域名 / 在公司外面】门户地址 ${e.web}`,
          e.page,
          `(点不开的话, 把这串粘到 Companion「服务器配置 → 接入链接」: ${e.app})`,
        ]),
        "",
        usable ? `接入码 ${usable}` : "",
      ].filter((l, i, a) => l !== "" || (i > 0 && a[i - 1] !== "")).join("\n").trim();

  return (
    <Section>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", fontSize: 13 }}>
        <span style={{ color: "var(--text-muted)" }}>员工接入 Companion:</span>
        <span>
          门户地址{" "}
          {addresses.map((a, i) => (
            <code key={a} style={{ marginRight: 6 }}>
              {a}
              {addresses.length > 1 && i === 0 ? " (内网 IP)" : ""}
            </code>
          ))}
        </span>
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
