import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";

/** Never accept a certificate fetched from the unverified connection itself. */
export function ServerCertificateImport() {
  const [result, setResult] = useState("");
  return <div>
    <label>
      导入 IT 提供的 PEM 公共证书（将替换现有公司信任证书）
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
