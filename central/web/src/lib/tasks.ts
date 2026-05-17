/** BL-CENTRAL-WEB-PURGE-USERDATA (5/17 鸿波): 此模块已废.
 *
 * 原 fetchMyTasks() 调 gateway `/api/tasks/me` → 读员工本机
 * `~/.catfish/tasks.jsonl`. 违反 BL-CENTRAL-EDGE-BOUNDARY.
 *
 * Companion 自己有任务看板, 直接读本地文件, 不经中央 web. 中央 web 不暴露此
 * 数据.
 *
 * 此 stub 保留是为了 git diff 显示删除. 真删请在你本机 `git rm`.
 */
export {};
