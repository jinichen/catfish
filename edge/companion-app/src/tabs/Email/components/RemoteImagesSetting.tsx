/** 「邮件里的外部图片」设置 (11/14): 邮箱设置里一张小卡。默认自动显示, 疑似钓鱼的照旧先拦 (lib/emailRemoteImages.ts)。 */
import { useEffect, useState } from "react";

import { getAutoLoadImages, onAutoLoadImagesChange, setAutoLoadImages } from "../../../lib/emailRemoteImages";

export function useAutoLoadImages(): boolean {
  const [on, setOn] = useState(getAutoLoadImages);
  useEffect(() => onAutoLoadImagesChange(() => setOn(getAutoLoadImages())), []);
  return on;
}

export default function RemoteImagesSetting() {
  const on = useAutoLoadImages();
  return (
    <div style={{ margin: "8px 12px", padding: "10px 12px", border: "1px solid var(--catfish-border)", borderRadius: 8,
                  background: "var(--catfish-bg)", fontSize: 12, lineHeight: 1.6, display: "flex", flexDirection: "column", gap: 4 }}>
      <strong>邮件里的外部图片</strong>
      <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, cursor: "pointer" }}>
        <input type="checkbox" checked={on} onChange={(e) => setAutoLoadImages(e.target.checked)} />
        打开邮件时自动显示
      </label>
      <span style={{ color: "var(--catfish-muted)", fontSize: 11 }}>
        外部图片一加载, 发件人就知道你打开了这封邮件。疑似钓鱼的邮件不会自动显示, 要看再点「显示图片」;
        关掉这项则每封都要自己点。
      </span>
    </div>
  );
}
