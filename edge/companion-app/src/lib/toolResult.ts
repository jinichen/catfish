/** tool 返回值里的**业务失败**识别 — BL-TOOLCALL-FAKE-OK (7/27 鸿波实盘).
 *
 * # 为什么需要这个
 *
 * useChat.ts 老代码 `ok = res.ok` 判的是**传输层**成功: tool 调到了、没抛异常。
 * 但 tool 完全可以"调用成功地告诉你它失败了":
 *
 *     browser_navigate → {"success": false, "error": "Blocked: URL targets a
 *                         private or internal address"}
 *
 * 传输层 res.ok === true → UI 标 ✓。鸿波截图里两个 browser tool 都是绿勾,
 * 页面却纹丝不动 —— 他和我都据此判断"调用成功但没生效",往 CDP / 页面渲染
 * 方向查了好几轮，真相其实写在 tool 返回值里，被 UI 吞了。
 *
 * # 两套约定都要认
 *
 * hermes builtin:  {"success": false, "error": "..."}
 * catfish native:  {"type": "error", "error": "..."}    (catfish_tools_browser.py 等)
 *
 * # 保守原则
 *
 * 宁可漏判也不错判 —— 把正常结果标成 ✗ 比标错 ✓ 更烦人。所以只在有**明确**
 * 失败信号时才判失败: success===false / type==="error" / 顶层 error 非空且没有
 * 任何成功信号。`error: null` `error: ""` 这类字段一律不算。
 */

/** tool 返回体里表示"成功"的信号 — 有这些就别判失败 */
const OK_TYPES = new Set(["ok", "result", "image", "text"]);

/**
 * 从 tool 的返回值里找业务失败信息。
 *
 * @param raw tool 返回的原始字符串 (useChat 里的 resultStr)
 * @returns 失败原因；没有明确失败信号则返回 undefined
 */
export function detectToolBusinessError(raw: unknown): string | undefined {
  if (raw === null || raw === undefined) return undefined;

  let obj: unknown = raw;
  if (typeof raw === "string") {
    const s = raw.trim();
    // 不像 JSON 就别猜 —— 纯文本结果里出现 "error" 字样不代表失败
    if (!s.startsWith("{") && !s.startsWith("[")) return undefined;
    try {
      obj = JSON.parse(s);
    } catch {
      return undefined;
    }
  }
  if (typeof obj !== "object" || obj === null || Array.isArray(obj)) {
    return undefined;
  }

  const o = obj as Record<string, unknown>;
  const errText = typeof o.error === "string" ? o.error.trim() : "";
  const type = typeof o.type === "string" ? o.type.toLowerCase() : "";

  // catfish native 约定: {"type": "error", "error": "..."}
  if (type === "error") {
    return errText || "工具返回 type=error";
  }
  // hermes builtin 约定: {"success": false, "error": "..."}
  if (o.success === false) {
    return errText || "工具返回 success=false";
  }
  // 有明确成功信号 → 不管 error 字段长什么样
  if (o.success === true || OK_TYPES.has(type)) {
    return undefined;
  }
  // 兜底: 只有一个非空 error 字符串, 没有任何成功信号
  if (errText) {
    return errText;
  }
  return undefined;
}
