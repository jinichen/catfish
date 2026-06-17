// P3.5.17.c.2 (6/17 鸿波) DEPRECATED — 砍, 真删用 `git rm`.
//
// 真因: 跟 ContextOverflowBanner 同款数学错 — 拿 useChatStore.lastPromptTokens
// (= hermes turn 内多 LLM API call 累加 prompt_tokens, cumulative cost) 跟
// catalog.context_window 比 %, 算出 "87% / 173%" 误报. 鸿波 6/17 audit 确认
// 真上游 single-call prompt 最大 135K << 256K ctx, 没 overflow.
//
// hermes 自带 ContextCompressor 自己处理 ctx, Companion 端不需要再算.
export {};
