/** BL-CENTRAL-WEB-PURGE-USERDATA (5/17 鸿波): 此页已废.
 *
 * 原 /kanban 读 gateway `/api/tasks/me` → 读员工本机 `~/.catfish/tasks.jsonl`
 * → 展示任务标题/状态. 违反 BL-CENTRAL-EDGE-BOUNDARY (中央端不碰用户数据).
 *
 * Companion 桌面 app 自己有任务看板. 中央 web 不该有这页.
 *
 * App.tsx 路由把 `/kanban` 直接 redirect 到首页. 此 stub 文件保留是为了 git
 * diff 显示删除而非空 stub. 真删请在你本机 `git rm`.
 */
export {};
