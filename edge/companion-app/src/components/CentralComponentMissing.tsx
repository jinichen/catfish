/** 可选大组件 (向量模型包 / 会议组件包) 拿不到时说清楚是哪一种情况 (10/14 鸿波 Windows 截图)。
 *
 * 原来两种情况一个说法:「中央服务器还没有发布…请联系 IT」—— 但截图里其实是**连不上中央**
 * (读 https://127.0.0.1/components/manifest.json 时 error sending request)。员工照着找 IT,
 * IT 去中央一看包在那儿, 两头对不上。
 *   · 连不上: 说出连的是哪个地址和原因, 让员工核对「设置 → 服务器配置」的门户地址 / 中央是否在运行, 给「重试」
 *   · 连上了但没这个包: 才是「中央还没发布, 请联系 IT」
 * 手动放文件的命令是给 IT 的, 收进折叠里, 不糊在员工脸上。
 */
import type { ReactNode } from "react";

import { config } from "../lib/env";
import { Btn, ErrorLine } from "../tabs/Collab/roomLinkUi";

export default function CentralComponentMissing({
  what,
  listError,
  onRetry,
  manual,
}: {
  /** 「向量模型包」/「会议组件包」 */
  what: string;
  listError: string | null;
  onRetry: () => void;
  /** 给 IT 的手动安装说明 (可选) */
  manual?: ReactNode;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }}>
      {listError ? (
        <>
          <div>
            连不上中央服务器 (<code>{config.webUrl}</code>), 没法自动下载{what}。请确认中央服务器在运行,
            并到「设置 → 服务器配置」核对门户地址, 然后点重试。
          </div>
          <ErrorLine>{listError}</ErrorLine>
          <div>
            <Btn kind="primary" onClick={onRetry}>重试</Btn>
          </div>
        </>
      ) : (
        <div>中央服务器还没有发布本机平台的{what}, 请联系 IT 在中央「组件市场」上传。</div>
      )}
      {manual && (
        <details>
          <summary style={{ cursor: "pointer", color: "var(--catfish-text-muted)" }}>给 IT 的手动安装说明</summary>
          {manual}
        </details>
      )}
    </div>
  );
}
