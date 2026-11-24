/** 只有对话窗口才有的规矩 (随每条消息一起发, 跟 FACT_EVIDENCE_RULES 一样放在最后)。
 *
 * 11/24 鸿波「打开ITSS 简介.docx」→ 小鲶用 execute_code 跑 `open`, Word 弹出来盖住了对话;
 * 而对话页右边本来就能预览 —— 回答里写出完整路径, Companion 自己在右边打开 (useArtifactAutoOpen /
 * lastAnswerOutputs)。微信那边没有右栏, 所以这条只在对话窗口发。 */
export const CHAT_UI_RULES = `## 对话窗口规则
- 员工说「打开 / 看看 / 预览」某个文件时，不要用 open、subprocess、osascript 之类在电脑上启动 Word / Excel / 预览等程序。找到文件后，在回答里写出它的完整路径（用反引号包住），对话窗口会在右侧直接预览。
- 只有员工明确说「用 Word 打开」「用默认程序打开」「在 Finder 里显示」这类话时，才调用本机程序。`;
