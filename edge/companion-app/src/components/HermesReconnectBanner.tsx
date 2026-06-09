/** P3.3.5 (6/9 鸿波) — hermes reconnect banner.
 *
 * 监听 `catfish:hermes-reconnecting` / `catfish:hermes-reconnect-end` 自定义事件,
 * chat.ts 在 fetch connection error (hermes 不可达) 时 dispatch:
 *
 *   catfish:hermes-reconnecting  → chat.ts 自动重试 1 次, sleep 5s, banner 显
 *   catfish:hermes-reconnect-end → 重试结果 (成功 / 最终失败) 出来, banner 关
 *
 * UX: 顶部固定一条灰色 banner "hermes 重连中…", 倒计时 5s. 不打断现有 chat
 * stream, 不弹 modal. 比之前红框报错 + 用户手动重发好得多.
 *
 * 真根因: hermes 内置 lark/weixin platform 在 DNS 失败时会触发上游 sys.exit,
 * launchd KeepAlive 自动重启. 这段 ~5s 窗口 Companion fetch 必失败. 现在
 * 自动重试 + banner, 用户体感 "卡 5-10s 后通了" 而不是红框报错.
 */

import { useEffect, useState } from "react";

export default function HermesReconnectBanner() {
  const [active, setActive] = useState(false);
  const [secondsLeft, setSecondsLeft] = useState(5);

  useEffect(() => {
    const onStart = () => {
      setActive(true);
      setSecondsLeft(5);
    };
    const onEnd = () => {
      setActive(false);
      setSecondsLeft(0);
    };
    window.addEventListener("catfish:hermes-reconnecting", onStart);
    window.addEventListener("catfish:hermes-reconnect-end", onEnd);
    return () => {
      window.removeEventListener("catfish:hermes-reconnecting", onStart);
      window.removeEventListener("catfish:hermes-reconnect-end", onEnd);
    };
  }, []);

  // 倒计时 (active 时每秒 -1, 走到 0 不消失, 等 onEnd 事件清掉)
  useEffect(() => {
    if (!active) return;
    if (secondsLeft <= 0) return;
    const t = setTimeout(() => setSecondsLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearTimeout(t);
  }, [active, secondsLeft]);

  if (!active) return null;

  return (
    <div className="hermes-reconnect-banner" role="status" aria-live="polite">
      <span className="hermes-reconnect-banner__spinner" aria-hidden="true">
        ⟳
      </span>
      <span className="hermes-reconnect-banner__text">
        hermes 重启中, 自动重试…
        {secondsLeft > 0 && (
          <span className="hermes-reconnect-banner__countdown">
            {" "}({secondsLeft}s)
          </span>
        )}
      </span>
    </div>
  );
}
