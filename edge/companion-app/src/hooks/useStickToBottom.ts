/** 聊天消息流"贴底" (10/23 鸿波 catch "切到某个对话没到底 / 聊着聊着也常不到底")。
 *
 * 老做法是 messages 变了滚一次 —— 但 markdown / 工具块 / 图片 / 操作按钮是渲染后才长高的,
 * 滚完内容又长了一截, 下一次再算"离底 <60px"已经不成立, 就再也不跟了。
 * 改成记"贴底"意图: 员工自己往上翻 = 不贴, 翻回底 = 贴; 贴底时内容 / 窗口尺寸一变
 * (ResizeObserver) 就滚到底。切会话 (P3.5.79+ 7/22)、自己发消息 → 强制贴底。
 */
import { useCallback, useEffect, useRef, type RefObject } from "react";

const STICK_THRESHOLD = 40;

interface Last {
  role?: string;
}

export function useStickToBottom(
  scrollRef: RefObject<HTMLElement | null>,
  sessionId: string | null,
  messages: readonly Last[],
): () => void {
  const stickRef = useRef(true);
  const scrolledSessionRef = useRef<string | null>(null);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [scrollRef]);

  useEffect(() => {
    // 空会话不记, 等消息加载完再算"切进来"
    if (scrolledSessionRef.current !== sessionId && messages.length > 0) {
      scrolledSessionRef.current = sessionId;
      stickRef.current = true;
    } else if (messages[messages.length - 1]?.role === "user") {
      stickRef.current = true;
    }
    if (stickRef.current) scrollToBottom();
  }, [sessionId, messages, scrollToBottom]);

  useEffect(() => {
    const el = scrollRef.current;
    const content = el?.firstElementChild;
    if (!el || !content || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (stickRef.current) scrollToBottom();
    });
    ro.observe(content);
    ro.observe(el);
    return () => ro.disconnect();
  }, [scrollRef, scrollToBottom]);

  // 挂在滚动容器的 onScroll 上
  return useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_THRESHOLD;
  }, [scrollRef]);
}
