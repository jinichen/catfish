/** BL-CENTRAL-WEB-PURGE-MEPAGE (5/17 鸿波): 此页已废.
 *
 * 原 /me 三张卡:
 *   1. 身份 (OIDC claims) — auth metadata, 合规
 *   2. 今日配额 — quota_events (audit metadata, 合规)
 *   3. 详细数据 (在桌面 Companion 看) — **列了员工本机数据具体概念名**
 *      (writing_style / personality / employee_journal / session_facts ...),
 *      违 BL-CENTRAL-EDGE-BOUNDARY spirit (中央 web 不该知道员工本机有啥个性化
 *      数据). 哪怕只是 UI 文案, 也等于"中央在跟员工说我知道你本机有 X Y Z".
 *
 * 整页删. 员工自查身份 / 配额 / 画像 / 印象 / skill / 对话 → 桌面 Companion app.
 *
 * App.tsx 路由把 `/me` redirect 到首页. 此 stub 保留是为了 git diff 显示删除而非
 * 空 stub. 真删请在你本机 `git rm`.
 */
export {};
