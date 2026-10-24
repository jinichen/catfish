/** 聊天消息流"贴底" (10/23 鸿波 catch "切到某个对话没到底 / 聊着聊着也常不到底")。
 *
 * 老做法是 messages 变了滚一次 —— 但 markdown / 工具块 / 图片 / 操作按钮是渲染后才长高的,
 * 滚完内容又长了一截, 下一次再算"离底 <60px"已经不成立, 就再也不跟了。
 * 改成记"贴底"意图: 员工自己往上翻 = 不贴, 翻回底 = 贴; 贴底时内容 / 窗口尺寸一变
 * (ResizeObserver) 就滚到底。切会话 (P3.5.79+ 7/22)、自己发消息 → 强制贴底。
 *
 * 10/24 鸿波「对话的指令运行期间, 不会保持在会话的末尾」: 原来每个 scroll 事件都按「离底 <40px」
 * 重算贴不贴底。跑指令时工具结果块一下长出几百 px —— 我们刚滚到底、那次滚动的事件还没派发,
 * 内容先长了一截, 事件到了一算离底 600px, 当成员工往上翻了, 再也不跟。
 * 改成: 只有 scrollTop 真往上走了才算员工翻; 内容在下面长高 (scrollTop 没动) 不算。
 * 鸿波追加「不是用按钮, 直接到末尾」: 跑指令 (isStreaming) 期间一律跟到底, 员工往上翻也拉回;
 * 跑完那一下再滚到底一次。没在跑时照旧: 翻上去不拉回。
 *
 * 10/24 开发版诊断框实测: 内容高度没变 (6220)、我们也没滚, scrollTop 自己从 5711 掉到 4193 ——
 * 最新那条回复在消息 id 从临时换成正式时被整块卸掉重挂, 中间页面排了一次版, 内容一下短 1518px,
 * 浏览器把 scrollTop 夹到新的底; 再挂回来高度复原, scrollTop 却留在被夹的位置。只看 scrollTop
 * 判断「员工往上翻」不可靠 —— 改看员工真的动了没有: 滚轮 / 触控板、按住滚动条拖、PageUp / Home /
 * 上箭头。没有这些动作 scrollTop 却变了 = 页面自己动的, 贴着底就立刻滚回去。
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
  isStreaming = false,
): () => void {
  const stickRef = useRef(true);
  const scrolledSessionRef = useRef<string | null>(null);
  const lastTopRef = useRef(0);
  const streamingRef = useRef(isStreaming);
  streamingRef.current = isStreaming;
  // 员工最近一次「自己滚」的时间 (滚轮 / 键盘); 按住滚动条拖的时候一直算
  const userIntentUntilRef = useRef(0);
  const draggingRef = useRef(false);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const mark = () => { userIntentUntilRef.current = Date.now() + 600; };
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      if (["PageUp", "PageDown", "Home", "End", "ArrowUp", "ArrowDown", " "].includes(e.key)) mark();
    };
    const down = () => { draggingRef.current = true; };
    const up = () => { if (draggingRef.current) mark(); draggingRef.current = false; };
    el.addEventListener("wheel", mark, { passive: true });
    el.addEventListener("touchmove", mark, { passive: true });
    el.addEventListener("pointerdown", down);
    window.addEventListener("pointerup", up);
    window.addEventListener("keydown", onKey);
    return () => {
      el.removeEventListener("wheel", mark);
      el.removeEventListener("touchmove", mark);
      el.removeEventListener("pointerdown", down);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("keydown", onKey);
    };
  }, [scrollRef]);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    lastTopRef.current = el.scrollTop;
  }, [scrollRef]);

  useEffect(() => {
    // 空会话不记, 等消息加载完再算"切进来"
    if (scrolledSessionRef.current !== sessionId && messages.length > 0) {
      scrolledSessionRef.current = sessionId;
      stickRef.current = true;
    } else if (messages[messages.length - 1]?.role === "user") {
      stickRef.current = true;
    }
    if (stickRef.current || isStreaming) scrollToBottom();
  }, [sessionId, messages, isStreaming, scrollToBottom]);

  // 跑完 (isStreaming true → false): 最后的操作按钮 / 收尾段落这时才出来, 再贴一次底
  const wasStreamingRef = useRef(isStreaming);
  useEffect(() => {
    if (wasStreamingRef.current && !isStreaming) {
      stickRef.current = true;
      scrollToBottom();
    }
    wasStreamingRef.current = isStreaming;
  }, [isStreaming, scrollToBottom]);

  useEffect(() => {
    const el = scrollRef.current;
    const content = el?.firstElementChild;
    if (!el || !content || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      if (stickRef.current || streamingRef.current) scrollToBottom();
    });
    ro.observe(content);
    ro.observe(el);
    return () => ro.disconnect();
  }, [scrollRef, scrollToBottom]);

  // 挂在滚动容器的 onScroll 上
  return useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const top = el.scrollTop;
    const userMoved = draggingRef.current || Date.now() < userIntentUntilRef.current;
    if (el.scrollHeight - top - el.clientHeight < STICK_THRESHOLD) {
      stickRef.current = true;
    } else if (userMoved) {
      if (top < lastTopRef.current - 1) stickRef.current = false; // 员工真往上翻了
    } else if (stickRef.current || streamingRef.current) {
      // 员工没动, scrollTop 却离了底: 页面自己动的 (整块重挂被夹) —— 滚回去
      scrollToBottom();
      return;
    }
    lastTopRef.current = top;
  }, [scrollRef, scrollToBottom]);
}
