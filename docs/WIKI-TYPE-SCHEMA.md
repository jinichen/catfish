# 知识库类型定义（属性 / 状态 / 类型上的行动）

2026-11-07 起。本体四块：**类型、属性、关系、行动**。原来鲶鱼只有类型（`entity_type` / `concept_type`）和关系（`related`），证书的编号、发证机构、有效期都写在正文一句话里，小鲶只能全文搜再读字；行动只能一页一页手工关联，执行时拿不到参数。

这一版先把「资质证书」（`cert`）做通，别的类型照同一套加。

## 定义在哪

- 内置：`edge/contracts/wiki_type_schema.json`，随安装包发（edge-runtime 里的 `contracts/`）。
- 本机补充：`~/.catfish/wiki-type-schema.json`，格式相同，只写要加 / 要改的部分：
  - 同名类型：属性按 `key` 合并，行动按 `id` 合并，其它字段覆盖；
  - 新类型：直接写进 `types`（key 用条目的 `entity_type` / `concept_type` 值，中文细分类型按受控词表归一后也认，例「资质」→ `cert`）。
- 读写全在 tool-bridge `wiki_types.py`；Companion（`commands/wiki_types.rs`）只转发，再给行动标上本机技能装没装。

## 一个类型里有什么

| 字段 | 意思 |
|---|---|
| `label` | 界面上的名字（资质证书） |
| `properties` | 属性：`key`、`label`、`type`（`text` / `date`）、可选 `hint`、`pattern`、`keywords`、`aliases` |
| `status` | 自动算的状态：`from` 哪个日期属性、`warn_days` 提前多少天算「即将到期」 |
| `relations` | 这一类常用的关系（目前是说明，还不拦截） |
| `actions` | 这一类都有的行动：`id`、`label`、`description`、可选 `skill_path`、`instruction`（`{属性key}` 会换成属性值） |

### 属性存在哪

扁平字段写进条目文件头，例 `cert_no: "CCRC-2019-ISV-SD-210"`。改属性只动这几行，正文一个字节都不动（`wiki_types.set_props_file` 直接写文件，不走会重排文件头的 `update_wiki_file`）。

### 从正文抽属性（「整理属性」）

知识体系 → 关系整理 → 搜索框旁「属性」：

1. 先按每个属性的 `pattern`（正则，第 1 组是值）抽，秒出、不花 token。
2. 句式抽不到、但正文出现 `keywords` 或属性名本身（「有效期」「发证机构」…）的条目，可以点「让小鲶读正文补」：每 8 条一批请模型摘值。模型给的值必须原样出现在正文里（日期允许换写法），否则丢掉。
3. 都过一道过滤：正文说「待核 / 疑似误录 / 未录入 / 占位」的不当值；日期必须是真日期（「无限期」不算）；`aliases` 把同一个值的不同写法归一（等级「无等级」→「无」）。
4. 只给建议：表里每个值都能改，勾上的才写；写之前每个文件整份备份到 `~/.catfish/wiki/.props-backups/<时间>/`。

11/07 在鸿波机器 80 条证书上实测：固定句式给出 69 条的建议（有效期 62、编号 56、发证机构 57…），小鲶再补认证范围等十来项，一次全量二十秒左右。

### 类型上的行动

知识页属性卡片下面列出这一类都有的行动，点「交给小鲶」：切到工作台开新对话，属性和「要做的事」（`instruction`，属性已填进去）一起带过去。要技能的（`skill_path`）本机没装就灰掉；不要技能的（例「到期提醒」用 `catfish_create_reminder`）总是可用。登录、提交、发送这类对外操作小鲶照常先问。

证书现有两个：

- **官网核查**：技能 `personal/renzheng-gongshi-website-verify`（鸿波本机自己的技能，别的员工机上没有会灰掉），核对编号、状态、有效期、等级、认证范围，不一致的先给员工看，同意后用 `catfish_wiki_update` 的 `props` 改属性。
- **到期提醒**：有效期至前 180 天提醒准备续期；有复审日期的也提前 180 天提醒；建之前先让员工确认。

单页手工关联的行动（`action_refs`，见 `CATFISH-WIKI-ACTIONS.md`）保留，当个别条目的补充。

## 小鲶怎么用

- `catfish_wiki_read`：有类型定义的条目返参多一个 `type_info`（属性、状态、这一类能做的事）。
- `catfish_wiki_query`：按类型 + 属性查，例 `{type: "cert", where: [{field: "valid_until", before: "2027-06-30"}], sort: "valid_until"}`、`{type: "cert", status: "即将到期"}`、`{type: "cert", where: [{field: "valid_until", empty: true}]}`（找没填的）。
- `catfish_wiki_update` 只给 `rel_path` + `props`：只改属性，不用整篇覆写。

## 下一步

- 证书用顺了，再按同一套给项目、部门、人、组织定义属性和行动。
- 蒸馏 / 新建证书条目时直接按定义填属性（现在新条目靠「整理属性」补）。
- `relations` 从说明变成约束（哪两类之间能连什么）。
