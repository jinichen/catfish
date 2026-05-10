/** TTS 小喇叭按钮 — BL-VOICE2 (5/10).
 *
 * 给 AI 回复消息加一个 "听" 按钮. 点击 → piper 合成 → 播放.
 * 状态:
 *   idle      🔊 灰色, hover 高亮
 *   loading   ⏳ 转圈, disabled
 *   playing   ⏸ 高亮 (再点停止)
 *   error     🔇 红色 + tooltip 错误信息
 *
 * 全局只有一个 "正在播放", 点别的消息会自动停旧的 (lib/tts.ts 里管).
 */

import { useEffect, useState } from "react";

import { speak, stopSpeaking, isSpeaking } from "../lib/tts";

interface Props {
  text: string;
  /** 可选: 替默认 voice (zh_CN-huayan-medium) */
  voice?: string;
  /** 16-20px 给 ChatMessage / 24px 给桌宠 */
  size?: number;
}

type State = "idle" | "loading" | "playing" | "error";

export default function TTSButton({ text, voice, size = 16 }: Props) {
  const [state, setState] = useState<State>("idle");
  const [errMsg, setErrMsg] = useState<string>("");

  // 卸载 / 切到别的消息时, 如果正是我们在播 → 不主动停 (lib 全局管),
  // 但要把本组件状态归位.
  useEffect(() => {
    const tick = setInterval(() => {
      if (state === "playing" && !isSpeaking()) {
        // 全局 audio 已经停了 (要么 ended 要么被 stopSpeaking)
        setState("idle");
      }
    }, 200);
    return () => clearInterval(tick);
  }, [state]);

  const handleClick = async () => {
    if (state === "playing") {
      stopSpeaking();
      setState("idle");
      return;
    }
    if (state === "loading") return;

    setState("loading");
    setErrMsg("");
    try {
      const promise = speak(text, voice);
      // 进 playing 状态. promise resolve 时 ended → setState idle (effect 会处理).
      // 但合成完到真开始播之间有一点空隙, 直接进 playing 体验更顺滑.
      setState("playing");
      await promise;
      setState("idle");
    } catch (e) {
      const m = e instanceof Error ? e.message : String(e);
      if (m === "aborted") {
        setState("idle");
        return;
      }
      setState("error");
      setErrMsg(m);
      // eslint-disable-next-line no-console
      console.warn("[TTSButton] speak 失败:", m);
      // 3 秒后回 idle, 给员工再点的机会
      setTimeout(() => setState("idle"), 3000);
    }
  };

  const icon =
    state === "loading"
      ? "⋯"
      : state === "playing"
        ? "⏸"
        : state === "error"
          ? "🔇"
          : "🔊";

  const color =
    state === "playing"
      ? "var(--catfish-accent)"
      : state === "error"
        ? "var(--catfish-red, #c43f3f)"
        : "var(--catfish-text-muted)";

  const title =
    state === "loading"
      ? "正在合成…"
      : state === "playing"
        ? "暂停"
        : state === "error"
          ? `TTS 失败: ${errMsg}`
          : "听一下 (Piper 本地合成, 数据不出电脑)";

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={state === "loading"}
      title={title}
      aria-label={title}
      style={{
        background: "transparent",
        border: "none",
        cursor: state === "loading" ? "wait" : "pointer",
        color,
        fontSize: size,
        padding: "2px 4px",
        opacity: state === "loading" ? 0.6 : 1,
        transition: "color 0.15s",
        lineHeight: 1,
      }}
    >
      {icon}
    </button>
  );
}
