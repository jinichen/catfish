# 专家包 —— 设计 (待审)

10/18 起草。「原子 skill → 工作流 skill → 专家」的第三层 (docs/WORKFLOW-SKILL-PLAN.md §7)。

一句话: **专家 = 一个有自己人设、只看得到自己那几样技能 / 工作流 / 能力、知道什么事该走哪条路、交付前会核对的小鲶分身**;
专家包 = 把它打成一个文件, 部门 (或员工自己) 做一次, 同事从资源市场装上就有一个「资质专员」「合同审核员」。

对标 WorkBuddy 专家包 (人设 + 团队成员 + 13 个 skill + quickPrompts), 但走我们的路:
不做一组 agent 互相传话的「团队」, 做**一个专家在若干工作流里选路** —— 内部流程多是固定路线, 一个会选路的专家比一群互相
传话的 agent 更稳、更好审计 (10/17 讨论)。

## 1. 现状 (10/18 读代码核实)

已经有的 —— 专家的「壳」其实就是现在的专家 Bot:

| 能力 | 位置 | 说明 |
|---|---|---|
| 专家 Bot = hermes profile | `commands/expert_bots.rs` | `~/.hermes/profiles/<id>/` 一整套独立 HERMES_HOME: 自己的 SOUL.md (人设)、config.yaml、skills/、会话和记忆 |
| 建 / 改 / 删 / 停用 | 同上 + `services/hermes_profile_sync.rs` | `hermes profile create --clone-from default`, 模型凭据从主 profile 同步; 每次改动走事务 + 重启 gateway, 失败回滚 |
| 注册表 / 模型策略 | `~/.catfish/companion.yaml` 的 `expert_bots` | 跟随 picker 或固定模型 |
| 按场景路由 | `expert_bot_route` + 前端 `resolveExpertBotRequest` | 请求打到 `/p/<profile>/v1/...`; **只有早安参谋、邮件起草两个场景** (`SUPPORTED_SCENARIOS`) |
| hermes 的分身隔离能力 | hermes 0.22.2 | 每个 profile 的 config.yaml 能单独设: `mcp_servers.<名>.tools.include/exclude` (只露哪些工具)、`skills.disabled` / `external_dirs` (看得到哪些 skill)、`agent.disabled_toolsets`; 微信能按发消息的人 / 群路由到某个 profile (`gateway.profile_routes`) |
| 工作流 / 积木 / 对外确认 | 10/17 工作流 skill | 专家「选好路之后」的执行就是跑工作流: 固定、可审计、对外步骤只能员工在界面上确认 |
| 分发 | skills hub (`/v1/hub/skills`) + 组件市场 | 单个 skill 可以发布 / 安装 (sha256 校验); 组件是平台大包 |

缺的:

1. **平时聊天找不到专家**: 专家 Bot 只服务两个写死的场景, 员工在工作台 / 微信里说话永远是主小鲶。
2. **专家看得到的东西跟主小鲶一模一样**: 每个 profile 都挂同一个 `~/.catfish/skills`、同一套 catfish 工具, 一个工具都没收;
   hermes 有收的开关, 我们一个都没写。专家装多了, 主小鲶的工具 / skill 列表会越挤越乱。
3. **没有「选路」和「交付前核对」的写法**: 现在只能把规则写进 SOUL 一大段话, 没有结构, 没法检查「规则里提到的工作流到底在不在」。
4. **没有「专家包」这种东西**: hub 只能发单个 skill, 组件是平台二进制包; 没有把 人设 + 若干 skill / 工作流 + 选路 + 能用的工具 打成一个可装可升级可卸的包。
5. **部门范围管不住**: hub 里 department 是所有部门共用的一个桶; identity 里存了 `allowed_skills`, 但没人执行。
6. **包里有可执行的东西但没签名**: 凝固 skill 的 script.py 会操作员工的浏览器, 现在只有 sha256 防传坏, 防不了「谁都能往市场里放」。

## 2. 专家包长什么样

一个文件 `<id>-<版本>.catfish-expert` (tar.gz), 里面:

```
expert.yaml          清单 (见下)
SOUL.md              人设: 我是谁、口吻、边界 (不碰什么)
skills/<名字>/        自带的技能 (凝固 skill: SKILL.md + script.py; 过程型 skill: SKILL.md)
workflows/<名字>/     自带的工作流 (SKILL.md + workflow.yaml)
avatar.png           可选, 512×512
```

`expert.yaml` (示意):

```yaml
kind: catfish-expert
id: qual-specialist            # 小写字母数字横线, 全公司唯一
name: 资质专员
version: 1.2.0
description: 帮项目部处理资质申请: 查台账、核对水印、走审批、到期提醒
scope: department              # personal (只自己) / department (本部门) / company
departments: [安全部]           # scope=department 时: 哪些部门能装
model: inherit                 # inherit (跟员工当前选的) / 固定某个模型
quick_prompts:                 # 员工打开专家时的几个快捷问法
  - 帮我看看这周有哪些资质申请待审批
  - 哪些资质证书三个月内到期
routes:                        # 选路: 什么事走哪条路, 缺什么先问
  - when: 处理资质申请待办 / 审批资质
    use: workflow:qual-approve
    ask: [要处理哪个系统的待办 (默认 EIS)]
  - when: 查证书到期 / 续期提醒
    use: workflow:cert-expiry
  - when: 资质标准、申报条件这类问答
    use: knowledge                       # 不跑流程, 查知识库回答
  - when: 其它
    use: decline                         # 不是我管的, 请员工回主小鲶
checks:                        # 交付前核对 (每条对应一个工作流的 expect 或一句要检查的话)
  - after: workflow:qual-approve
    verify: 每一条都写明了「通过 / 退回 / 作废」和原因
tools:                         # 除了 routes 里工作流用到的, 还要哪些能力 (积木目录里的名字)
  - wiki_search
  - wiki_read
requires:                      # 依赖但不随包带的 (例: 部门已经发布的 skill)
  skills: [department/eis-login@^1]
```

几条约束:

- **routes 里引用的工作流 / skill 必须在包里或 requires 里**, 打包和安装时都校验; `use: knowledge / decline` 是两个固定选项。
- **工具不用员工手填**: 安装器把 routes 里工作流用到的积木、skill 需要的浏览器工具、`tools` 里列的加起来, 就是这个专家能看到的全部
  (§3.2)。清单里写不出「给我全部工具」。
- **routes / checks 不是代码**: 安装时编进 SOUL.md 末尾一段结构化的「选路规则 / 交付前核对」, 专家照着选; 真正执行的是工作流
  (固定步骤 + expect + 对外确认), 所以「选错路」最坏是跑了一个不对的工作流, 而对外的那一步仍然要员工点头。
- **包不能依赖员工的个人 skill** (10/18 定): routes / requires 只能指向包里自带的或部门 / 公司已发布的;
  只给自己用的本机专家 (scope=personal, 不打包) 可以用自己的个人 skill。
- 包里**不能有员工数据**: 打包时跟发布 skill 一样扫凭据 / 身份证号手机号 / 内网地址, 撞到就拒; 专家 profile 里的会话、记忆、运行记录不进包。

## 3. 装上以后

### 3.1 落在哪

- **专家本身**: 一个 hermes profile (`~/.hermes/profiles/expert-<id>/`), 复用专家 Bot 现有的建 / 同步模型凭据 / 事务回滚;
  标记 `.catfish-managed-profile` 写 `expert-pack-v1` + 包 id 和版本, 卸载 / 升级只动这种 profile。
- **包里的技能 / 工作流**: 落 `~/.catfish/skills/expert/<id>/<名字>/` (新的 `expert` 命名空间)。
  run_skill / 工作流运行器按现有搜索规则就找得到; 主小鲶和别的专家**看不到** (§3.2)。
- **requires 里的部门 skill**: 照常装在 `department/` 下, 缺了安装器先从 hub 装 (同样 sha256 校验), 装不上就不装这个专家。

### 3.2 专家只看得到自己的东西 (写 profile 的 config.yaml)

- `mcp_servers.catfish-tools.tools.include` = 算出来的工具清单 + 固定几个 (`catfish_run_skill`、`catfish_workflow`
  的看运行 / 接着跑、`catfish_wiki_search` 这类查询); 其余 catfish 工具专家根本看不到。
- `skills.external_dirs` = 只有 `~/.catfish/skills/expert/<id>/` (+ requires 里用到的部门 skill); 不挂员工个人 skill 全集。
- 终端 / 执行代码这类工具集默认关 (`agent.disabled_toolsets`), 包里明确声明需要才开, 安装时单独提示员工。
- **主小鲶这边反过来**: 主 profile 的 `external_dirs` 不再挂整个 `~/.catfish/skills`, 改成挂 personal / department / external 三个子目录,
  这样装多少个专家, 主小鲶的 skill 列表都不变挤。(实现时先核实 hermes external_dirs 的扫描深度, 不行就用 `skills.disabled` 把 expert/ 下的名字排掉。)

### 3.3 怎么找到专家

- **工作台**: 对话框顶上加「找谁」—— 小鲶 / 资质专员 / …; 选了专家, 这个会话的请求走 `/p/<profile>/`
  (把现在只给早安 / 邮件用的路由扩到普通对话), 打开时显示 quick_prompts。会话列表里标出是跟哪个专家的。
- **主小鲶会推荐**: 主小鲶看到已装专家的一句话简介; 员工的事明显归某个专家 (「帮我审一下资质待办」), 它回一句
  「这件事资质专员更熟, 要切过去吗」并给一个按钮 —— **只推荐, 不偷偷转交** (转交意味着在员工不知道的 profile 里跑工作流、要审批)。
- **微信**: 第一版不做按人自动路由。员工在微信里说「找资质专员」→ 主小鲶回「去鲶鱼里找它」。(hermes 支持按发消息的人 / 群路由,
  适合「某个群固定交给某个专家」, 放到后面。)
- **定时任务**: 专家自己的定时任务 (每周一查到期证书) 挂在专家 profile 上跑; 对外步骤照旧停下等确认, 确认卡写明是哪个专家。

## 4. 怎么做出一个专家包

### 4.1 在 Companion 里搭 (主路)

设置 → 技能 → 新 tab「🧑‍💼 专家」→「新建专家」:

1. 名字、一句话简介、人设 (小鲶可以按简介先起草一版 SOUL, 员工改)。
2. 从「我的工作流 / 我的技能」里勾这个专家要用的。
3. 写选路: 每条一句「什么事」+ 选用哪个工作流 / 查知识库 / 不接; 「缺什么先问」可选。
4. 写交付前核对 (可选)。
5. 「试一下」: 用这个专家开一个对话, 真跑。满意了:
   - 自己用: 保存 (scope=personal);
   - 给部门: 「打包发布」→ 打包时校验 (引用都在、扫凭据和员工数据、skill 脚本过 `skill_verify` 静态检查) → 发到中央资源市场 (§5)。

也可以跟小鲶说「把我这几个资质相关的工作流做成一个资质专员」, 小鲶出草稿, 员工在上面这个界面里看、改、试、保存 (同工作流草稿)。

### 4.2 导出 / 导入文件

没有中央的单机、或者想发给别的公司: 「导出 .catfish-expert」/「从文件安装」。装文件跟从市场装走同一个安装器、同样的检查和权限提示。

## 5. 资源市场

- skills hub 加一种东西: **专家** (`kind=expert`), 存包文件 + 清单摘要 + sha256, 版本不能覆盖 (跟 skill 一样)。
- **谁能发 (10/18 定)**: 任何员工都能**提交**; 提交后进「待审核」, 由目标部门的 manager (company 范围由 admin) 审核 ——
  审核页看得到清单、权限摘要、选路、每个工作流的步骤和 skill 脚本里会操作的网站; 通过才上架, 驳回写原因回给提交人。
  manager / admin 自己发的同样走一遍审核记录 (可以自己审自己, 但留痕)。personal 不上市场 (导出文件给人)。
  中央只存包和审核记录 (谁提交、谁审、结论、时间), 跟 skill 发布同一条「员工主动 push」例外。
- **谁能看到 / 装**: 中央按 token 里的部门过滤: department 包只对 `departments` 里的人可见、可下载 —— 不是只在界面上藏,
  下载接口也拦。
- 门户的资源市场页和 Companion 的「专家」tab 都能看到列表: 名字、简介、版本、谁发的、**会用到什么** (§6 那份权限摘要)。

## 6. 安全

装一个专家 = 在员工电脑上装了会操作他浏览器、会建任务 / 发收集表的东西。

- **装之前给员工看权限摘要**, 他点「安装」才装:
  - 会用到的能力, 按「查 / 写本机 / 对外」分组; 对外的标出「跑到时会停下等你确认」;
  - 会操作哪些网站 (从 skill 脚本里的 goto 地址提取);
  - 需不需要终端 / 执行代码 (默认不给);
  - 依赖哪些部门 skill、会不会顺带装。
- **只从本公司的中央装** (或员工自己选的文件); 下载校验 sha256; skill 脚本安装前再过一遍 `skill_verify` (只许调白名单里的工具)。
- **签名放后面**: 现在 hub 只有 sha256 (防传坏, 不防冒充)。专家包是第一个「别人做的、会动员工浏览器」的分发物,
  第二阶段加发布者签名 (中央签, Companion 验), 第一阶段靠「只有 manager / admin 能发 + 部门可见范围」兜。
- 专家跑工作流时的对外确认、删除类每次问 (P59)、审计只记名字 —— 全部沿用, 不给专家开后门。
- 部门可见范围顺手补上 identity 里 `allowed_skills` 的执行: 专家包算一类 skill (`expert/<id>`), 不在允许范围内的装不上、跑不了。

## 7. 升级 / 停用 / 卸载

- **升级**: 市场上有新版 → 专家 tab 里提示「资质专员 1.3.0 可更新」+ 这版改了什么 + 权限摘要有没有变 (多了对外能力要重新点头)。
  升级替换 SOUL / 技能 / 工作流 / config 里的工具清单, **保留**专家 profile 里的会话和记忆。
- **停用**: 专家不出现在「找谁」里, gateway 不托管它 (沿用专家 Bot 的停用 / parked 机制)。
- **卸载**: 删专家 profile (只删 `expert-pack-v1` 标记的) + `skills/expert/<id>/`; 运行记录留着 (本机审计), 依赖装的部门 skill 不删。

## 8. 要改的地方

| 位置 | 改动 |
|---|---|
| tool-bridge 新 `expert_pack.py` | 清单校验、打包 (含扫描)、解包、安装 (落 skills/expert/<id>/、装依赖)、卸载、权限摘要 |
| `skill_workflow` / run_skill | `expert` 命名空间 (正则 + 搜索根) |
| Companion Rust `expert_bots*` | 专家包安装 = 建 managed profile + 写 SOUL (人设 + 编进去的选路 / 核对) + 写 config.yaml 的工具 / skill 范围; 升级 / 卸载 |
| hermes 主 profile 配置 | external_dirs 改挂三个子目录 (或 disabled 排掉 expert/) |
| 前端 工作台 | 「找谁」选择器、专家会话走 `/p/<profile>/`、quick_prompts、会话标记 |
| 前端 设置 → 技能 →「专家」 | 新建 / 编辑 / 试用 / 导出 / 从文件装 / 市场列表 / 权限摘要 / 升级 |
| hermes 插件 | 主小鲶知道已装专家 (一句话简介), 推荐切换 (只推荐) |
| 中央 skills-hub | `kind=expert`: 提交 / 待审核 / 审核通过或驳回 / 上架列表 / 下载按部门过滤 / 版本 |
| 中央 gateway | hub 代理放行专家包; identity `allowed_skills` 对 `expert/*` 生效 |
| 中央 web 资源市场页 | 专家列表 + 权限摘要; manager 的「待审核」页 (看清单 / 步骤 / 会操作的网站, 通过 / 驳回) |
| 文档 | 本文; 专家搭建 SOP |

## 9. 分阶段

1. **本机专家**: 在 Companion 里用自己的工作流 / 技能搭专家、工作台「找谁」切换、工具和 skill 范围收住、主小鲶推荐。
   (不需要中央改动, 先把「专家好不好用」验出来。)
2. **专家包 + 市场**: 打包 / 导出 / 从文件装 / 发布到中央 / 部门范围 / 权限摘要 / 升级卸载。
3. **签名 + 微信路由**: 发布者签名; 微信按人 / 群路由到专家。

## 9.1 第一阶段实现备注 (10/18)

- **定义在本机**: `~/.catfish/experts/<id>/expert.yaml` (tool-bridge `expert_local.py` 校验 / 编译); 设置 → 技能 →「🧑‍💼 专家」里搭。
- **落 profile** (Companion `commands/experts_local.rs`, 复用专家 Bot 的建 profile / 同步凭据 / 事务回滚):
  profile id = `expert-<id>`, 注册成 `chat: true` —— 不绑场景也被网关托管、不受早安 / 邮件那个总开关管。
  - SOUL.md = 人设 + 编进去的选路 (每条写清走哪个工作流、缺什么先问) + 交付前核对 + 规矩 (不归我管就请回小鲶)。
  - profile config.yaml: `mcp_servers.catfish-tools.tools.include` = 算出来的工具 (固定有 catfish_run_skill / catfish_workflow,
    加上勾的能力组; 有「查知识库」这条路自动带知识库能力) —— 已核实 hermes 按原始工具名过滤;
    `skills.external_dirs` 只挂 `~/.catfish/experts/<id>/skills/` (里面是指向选中工作流 / 技能的软链; 已核实 hermes 扫描跟软链);
    profile 克隆来的 skill 全进 `skills.disabled`; hermes 自带的终端 / 写文件 / 自带浏览器 / 上网等工具集默认关, 勾了才开。
  - 已经在跑的专家改了定义, 「保存并生效」后重启一次网关让它读到新的。
- **工作台「找谁」**: 选了专家, 请求走 `/p/<profile>/v1/chat/completions` (只走 hermes 通道; 连不上就明说, 不悄悄换小鲶答);
  换人 = 新开一个会话; 会话跟谁聊记在本机 localStorage, 从侧栏点回来接着跟同一个专家聊; 专家固定了模型就用它的, 否则跟 picker。
  跟专家的新会话先显示它的快捷问法。
- **主小鲶推荐**: catfish-memory 插件在主小鲶的系统提示里列出已装专家 (专家 profile 里不出);
  新工具 `catfish_expert` (list / suggest), suggest 在对话里画成「切到 X」按钮, 员工点了才切 —— 专家自己看不到这个工具。
  核心工具上限 47 → 48。主小鲶的配置一行不动。
- 本机专家可以用自己的个人 skill; 打包给别人 (第二阶段) 才禁止。

## 10. 不做 (这一版)

- 多个专家互相传话的「团队」(WorkBuddy 那种主理人 + 成员)。
- 主小鲶自动转交给专家后台去办 (只推荐切换)。
- 专家之间共享记忆。
- 第三方 (公司外) 市场。

## 11. 验收

在虚构数据上 (演示站 / 演示邮箱, 不碰真 EIS):

1. 用两个现成工作流搭一个「演示专员」, 写三条选路 (一个工作流 / 查知识库 / 不接); 试用时三种请求各走对路, 不归它管的会说「回主小鲶」。
2. 专家只看得到算出来的工具: 让它「帮我发个收集表」(没给这个能力) —— 它说做不了, 而不是绕路去做。
3. 主小鲶的 skill / 工具列表不因为装了专家而变长。
4. 主小鲶遇到归专家管的事, 推荐切换, 不自己转交。
5. 打包: 引用不存在的工作流 → 拒; 包里混进手机号 → 拒; skill 脚本调白名单外的工具 → 拒。
6. 另一台机器 (另一个部门的账号) 在市场里看不到 department 包, 直接调下载接口也拿不到。
6b. 普通员工提交 → 目标部门 manager 的待审核里出现 → 驳回带原因回到提交人 / 通过后才在市场里看得到。
7. 安装前的权限摘要跟包里实际用到的一致; 升级多了对外能力要重新确认。
8. 卸载只删这个专家的 profile 和 skills/expert/<id>/, 依赖的部门 skill 和运行记录还在。

## 12. 已确认 (10/18)

1. **先只做第一阶段 (本机专家)**, 验好用了再做专家包和市场。
2. **发布**: 任何员工都能提交, 目标部门的 manager 审核通过才上架 (company 范围由 admin 审); 驳回写原因 (§5)。
3. **主小鲶只推荐切换**, 不直接转交 (§3.3)。
4. **专家包不能依赖个人 skill**; 只给自己用的本机专家可以用自己的个人 skill (§2)。
