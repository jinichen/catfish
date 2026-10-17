# 一起填表 (收集表) —— 计划

10/17 定。场景: 一个人发起一张表, 请同事各填各的那一行 (月度统计 / 安全检查 / 报名 / 资质待办 / 周报),
回收后在**发起人自己的鲶鱼**上汇总处理。**只做收集, 不做多人同时改一张表** —— 共编表 (排期之类)
用公司已有的在线文档 (WPS / 腾讯文档 / 飞书 / 企业微信), 鲶鱼只帮每个人起草自己那部分。

一句话流程: 发起人给表头 → 小鲶整理成字段 → 发给同事 → 同事的小鲶用他本机材料预填、本人改完点提交
→ 中央只转交 (取走即清空) → 发起人本机收齐、核对、催填、导出 Excel / 写汇总。

## 1. 现有积木 (复用, 不重造)

| 积木 | 位置 | 本功能用法 |
|---|---|---|
| 同事邮筒 (请同事帮忙 P49/P50) | gateway `room_link_router.py` / `room_link_db.py`, 表 `room_link_mailbox`; Companion `roomLinkInbox.ts` 每 15 秒取 | 邀请 / 回填 / 催填 / 截止 都走它, 加新的 kind |
| 邮筒的数据纪律 | payload 是 TEXT 不解析不记日志; 取走同一事务里 `payload = NULL`; 元数据留 30 天; `tests/test_central_log_no_content.py` 静态拦 | 原样继承, 表单内容不在中央落地 |
| Excel 读写 | tool-bridge `openpyxl`, `sales_pricebook._map_header` (表头映射), `sales_quote._write_custom` (按公司模板写) | 从 Excel 表头建表; 按原模板导出汇总 |
| 工具注册 | `catfish_tool_schemas.py` + `catfish_tools.dispatch_native` 前缀分派; 合并成一个工具多 action (工具数有上限) | 新工具 `catfish_form` (action: create / send / status / responses / remind / export / prefill) |
| Companion 调 tool-bridge | `tool_bridge_rpc.rs` + `commands/sales.rs` 这种按功能包一层的 `pim/*` 方法 | `commands/forms.rs` 包 `pim/form_*` |
| 提醒 | `sendNotification` (`commands/system.rs:11`) + `TabBar` 角标 (`pendingCount`) | 收到填表邀请: 角标 + 系统通知 (邮筒的「请同事帮忙」现在只有角标) |
| 用户 / 部门 | gateway `User` (`sub`=邮箱, `department`, `role`, `managed_departments`); 源头 identity-server | 选人; 但**普通员工现在没有能列同事的接口** (`/admin/users` 要 admin), 要新加, 见 §5 |

## 2. 发起 (发起人本机)

1. 在「协同」tab 点「发起填表」, 或直接跟小鲶说「把这张表发给安全部的人填, 周五前」。
2. 表从哪来, 两种:
   - **Excel 模板**: 读第一行做表头; 每列推断类型 (下拉列表 → 单选, 日期格式 → 日期, 数字格式 → 数字), 推不出就是文本。
   - **一句话描述**: 小鲶列字段, 员工确认。
3. 字段定义 (存本机 `~/.catfish/forms/<form_id>/form.json`):

```json
{
  "form_id": "f_20261017_ab12", "title": "10 月安全检查", "owner": "<发起人邮箱>",
  "deadline": "2026-10-24", "note": "每个项目部填一行",
  "fields": [
    {"key": "project", "label": "项目部", "type": "text", "required": true},
    {"key": "checks", "label": "检查次数", "type": "number", "required": true},
    {"key": "level", "label": "最高隐患等级", "type": "choice", "options": ["无", "一般", "较大", "重大"]},
    {"key": "date", "label": "最近检查日期", "type": "date"},
    {"key": "remark", "label": "说明", "type": "longtext", "hint": "有较大以上隐患的写整改情况"}
  ],
  "recipients": ["a@…", "b@…"],
  "template": "template.xlsx"
}
```

   类型只有 6 种: text / longtext / number / date / choice / multichoice。不做公式、不做跨行校验、不做附件 (§8)。
4. 选人: 填邮箱, 或从「本部门 / 我管的部门」名单里勾 (§5 的新接口)。
5. 发送 = 给每个收件人投一条 `form_invite` (字段定义 + 标题 + 截止 + 说明, 不含任何别人的回答)。

## 3. 填写 (收件人本机)

1. 邮筒取到邀请 → 「协同」tab 的待办里出一张卡「张三请你填《10 月安全检查》· 5 项 · 10/24 截止」+ 系统通知。
2. 点开是一张表单。上面一个按钮「让小鲶先填」: 小鲶用**收件人自己本机**的邮件 / 文件 / 任务 / 知识库起草每一项,
   每项旁边标来源 (哪封邮件 / 哪个文件); 起草不出的空着, 不编。
3. 本人改完点「提交」才发出 (投一条 `form_response` 给发起人)。也可以点「不填」并写一句原因 (同样回给发起人)。
4. 截止前可以改了再提交, **后提交的覆盖先前的**。截止后照样能交, 发起人那边标「逾期」。
5. 小鲶预填时读到的原始材料**不随回答发出**, 只发表单里的值。

## 4. 回收与处理 (发起人本机)

1. 回答进 `~/.catfish/forms/<form_id>/responses/<填写人邮箱>.json` (后到覆盖)。
2. 进度表: 每个收件人一行 —— 已提交 / 未提交 / 不填 (原因) / 逾期; 「催一下」给没交的人投 `form_remind`;
   「结束收集」投 `form_close`, 收件人那边的卡变成「已结束」。
3. 导出 Excel: 有模板就按原模板的列顺序写, 后面加「填写人 / 提交时间 / 状态」三列; 没模板用内置样式。
   落到 `~/.catfish/outputs/<日期>/<表名>-汇总.xlsx`, 跟报价单同一个地方。
4. 交给小鲶处理: `catfish_form` 的 responses / status 让它读到结果, 员工可以说
   「谁还没交」「把较大隐患的几个项目列出来」「跟上个月比」「写一段汇总发给领导」—— 都在本机做。

## 5. 新加的中央接口: 同事名单

现在 `/admin/users` 只给 admin, `/api/dev/users` 在生产环境返回空, 员工只能手填邮箱。新加:

- `GET /api/directory?q=` → `{people: [{email, name, department}], total}`, 全公司可搜 (10/17 定)。
- 只出通讯录元数据 (邮箱 / 姓名 / 部门), 不碰任何员工数据, 不违反边界文档。
- gateway 代理 identity-server, 跟 `admin_proxy.py` 同一个写法。

## 6. 数据边界

跟「请同事帮忙」同一条规矩, 不开新口子:

- 中央只做**转交**: 表单定义和回答都是邮筒里的不透明 payload, 不解析、不打日志; 收件人 / 发起人**取走即清空**。
- 中央留下的只有元数据 (谁 → 谁、哪种 kind、什么时候投 / 取), 30 天后删, 和现在一样。
- 每条回答都是员工自己点「提交」发出的 (员工主动 push 的例外, 同销售摘要), 没有任何自动上传。
- 汇总、核对、导出、小鲶分析**全在发起人本机**; 中央 web 不出任何表单内容页。

要改的一处: 邮筒现在每条只留 10 分钟没人取就作废 (为「请同事帮忙」的在线握手定的)。
填表要跨好几天、对方可能关机, 所以**表单类 kind 单独定期限**: 留到截止日后 7 天, 最长 30 天;
`request` / `grant` 保持 10 分钟不变。期限一到 payload 清空, 跟取走一样。

## 7. 要改的地方

| 位置 | 改动 |
|---|---|
| gateway `room_link_db.py` / `room_link_router.py` | `MAILBOX_KINDS` 加 `form_invite / form_response / form_remind / form_close`; 按 kind 定期限 (表单类可带 `expires_at`, 上限 30 天); 现有测试 + 新测试 (过期清空、取走清空、日志不含内容) |
| gateway 新 `directory` 路由 + identity-server 一个只读查询 | §5 |
| tool-bridge 新 `forms.py` (+ `forms_excel.py`) | 表单定义 / 本机账本 / 从 Excel 建表 / 导出; `catfish_tool_schemas_form.py` 一个 `catfish_form` 工具; `server.py` 加 `pim/form_*` |
| hermes 插件 `plugin_core_tools.py` | `catfish_form` 加进 `_PROMOTE` (不然藏在 tool_search 后面) |
| Companion Rust `commands/forms.rs` | 包 `pim/form_*` |
| Companion 前端 `tabs/Collab/` | 发起填表 / 收到的表 (表单 + 「让小鲶先填」) / 我发起的表 (进度 + 催 + 导出); `roomLinkInbox.ts` 认新 kind 分给表单 store; 收到邀请发系统通知 |
| 文档 | 本文件; `CENTRAL-EDGE-DATA-BOUNDARY.md` 补一句「邮筒表单类 kind」说明 |

版本号按惯例 5 处一起改; 中央改动后跑 `central_version.sh --write`。

## 7.1 实现备注 (10/17 落地时的取舍)

- **10/17 定的两条**: 中央最多留 30 天 (截止后 7 天); 同事名单**全公司**可搜 (按需要填表的人, 不限本部门)。
- **老版 Companion 不会吞掉填表邮件**: 取件改成按 kind 取, 不带 `?kinds=` 只给请求 / 授权。不然老版会把
  认不出的邀请取走、解析失败丢掉, 中央又已清空 —— 邀请就凭空没了。
- 回答在中央至少留 3 天 (截止早过了也一样), 发起人出差关机几天也不丢。
- 通讯录接口 `/api/directory` (gateway) → identity `/admin/directory`: 任何登录员工可查, 只出 邮箱 / 姓名 / 部门,
  不列已删除 / 已锁定 / sysadmin / 自己。挂在 `/api` 而不是 `/api/admin`, 它不是管理功能。
- 网络都在 tool-bridge (`forms_net.py`), Companion 前端只调 `form_call` (Rust 只放行 `pim/form_*`);
  前端每 20 秒 `form_sync` 一次 (导航常驻订阅, 不进协同页也收得到, 新邀请 / 被催发系统通知)。
- 「让小鲶先填 / 让小鲶汇总」走工作台的一次性请求 (`startEmailChat`, 名字是历史原因), 跟员工自己打字一样。
  小鲶的 `catfish_form` 只能存草稿, **没有提交 / 不填动作**; 发 / 催 / 结束要先念给员工、带 confirmed 才发。
- 回答只收本表收件人发来的; 同一编号别人发的邀请不覆盖原表 (防冒名)。
- 核心工具上限 45 → 46 (gateway `DEFAULT_MAX_TOOLS`), `catfish_form` 跟 `catfish_customer` 一样直达, 不藏在 tool_search 后。
- 端到端实测 (临时 gateway + identity + 一次性 PG, 两个虚构账号): 发 → 老客户端取不到 → 新客户端取到、中央 payload 变 NULL
  → 提交 → 发起人收到 → 汇总表 / Excel 对; 通讯录不含自己和 sysadmin。

## 8. 不做 (这一版)

- 多人同时改同一张表 / 实时看到别人填的内容 (用在线文档)。
- 附件 / 图片字段 (邮筒单条 64KB 上限, 文件另走; 真需要再议)。
- 公式、跨行校验、条件显示字段。
- 周期性自动发 (每月自动发一次) —— 先看手动发用得多不多。
- 中央 web 上看表单汇总 (那就等于内容落中央)。
- 端到端加密: 现在跟「请同事帮忙」一样靠「不透明 + 取走即清空 + 不记日志」; 如果客户要求中央连密文都看不懂, 再加。

## 9. 验收

1. 发起人用一张真实格式的 Excel (虚构数据) 建表, 字段类型推断对; 发给 3 个测试账号。
2. 收件人关机 2 天再开, 邀请还在; 「让小鲶先填」每项带来源, 起草不出的为空; 改完提交, 发起人 15 秒内收到。
3. 重复提交覆盖; 截止后提交标逾期; 「不填」带原因回到发起人。
4. 催填 / 结束收集 收件人那边都看得到。
5. 导出 Excel 按原模板列顺序, 多三列; 小鲶能回答「谁还没交」「哪几个是较大隐患」。
6. 中央库: 取走后 payload 为 NULL; 过期后 payload 为 NULL; 日志里搜不到任何字段值。
7. Mac + Windows 各走一遍。

## 10. 已确认 (10/17)

1. 表单类 payload 在中央最多留 30 天 (截止后 7 天) —— 按建议。
2. 同事名单: 按需要填表的人, 全公司可搜。
3. 第一个样例用虚构的「安全生产月 · 10 月安全检查」表 (tests/test_forms.py)。
