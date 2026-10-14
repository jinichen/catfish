import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";

import { config } from "../../lib/env";
import { pairServerCertificate, PAIRING_CODE_PLACEHOLDER } from "../../lib/serverPairing";

/** 门户报「证书不受信任」时的两条修法 (10/14):
 *   1. 接入码 (首选): Companion 自己取证书, 指纹跟 IT 给的接入码对上才保存 —— 证书虽然是从
 *      还没验证的连接上拿的, 但信不信由另一条路来的接入码决定, 不是静默信任。
 *   2. IT 发来的 ca.pem 文件 (老办法, 中央还没升级到支持接入码时用)。 */
export function ServerCertificateImport() {
  const [result, setResult] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  return <div>
    <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
      <span>输入 IT 给的接入码</span>
      <input value={code} onChange={(e) => setCode(e.target.value)} placeholder={PAIRING_CODE_PLACEHOLDER}
        style={{ fontFamily: "var(--font-mono, monospace)", minWidth: 220 }} />
      <button type="button" disabled={busy || !code.trim()} onClick={async () => {
        setBusy(true);
        try {
          const path = await pairServerCertificate(config.webUrl, code);
          setResult(`接入码核对通过, 公司证书已保存到 ${path}, 下一次检测自动生效。`);
        } catch (error) { setResult(`没有导入：${String(error)}`); }
        finally { setBusy(false); }
      }}>{busy ? "核对中…" : "导入"}</button>
    </div>
    <label style={{ display: "block", marginTop: 6 }}>
      或导入 IT 提供的 PEM 公共证书文件（将替换现有公司信任证书）
      <input type="file" accept=".pem,.crt,.cer" onChange={async (event) => {
        const file = event.currentTarget.files?.[0];
        if (!file) return;
        try {
          if (file.size > 65536) throw new Error("证书不得超过 64KB");
          const path = await invoke<string>("import_server_certificate", { pem: await file.text() });
          setResult(`已保存到 ${path}，下一次检测自动生效；证书仍需匹配访问地址。`);
        } catch (error) { setResult(`导入失败：${String(error)}`); }
      }} />
    </label>
    {result && <p role="status">{result}</p>}
  </div>;
}
