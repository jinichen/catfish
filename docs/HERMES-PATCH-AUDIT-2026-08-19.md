# catfish 的 hermes patch 冗余量化 (2026-08-19)

> 起因: 看完 NousResearch/Hermes-Bot-Mode —— 它做多 agent roster + 定时任务 +
> agent 间通信，用的是**一个 `plugin.js`**，README 原话「no core patches,
> no background daemons, no extra storage: everything is standard Hermes surface」。
>
> 而 catfish-xcatfish-user 是 27 个 `.py`、9329 行、37 个 patch 入口。
> 鸿波: 「先做量化」。
>
> 方法: AST 机械提取 37 个 patch 函数 → 逐个找它动的 hermes 目标 →
> 对着本机 hermes 0.20.0 查上游现状。**每一行都有文件+行号，没有"应该"。**

## ⚠ 修订 (同日, 鸿波问「你有仔细看过代码了吗」)

**没有。这一版的方法有缺陷，先写在最前面。**

第一版的 29/8 分类，是照着每个 patch 函数 **docstring 的第一行**分的，
**没读实现**。37 个里真正读过代码的只有 6 个 (P19 / P36 / P40 / P25 / P12 / P13)。

而这个仓库的 docstring 有一部分是坏的，例如:

```
_patch_p28_weixin_zh   wrap WeixinAdapter.send — : 真:** : 真:** : : : : : : : : : : :
```

拿这种东西当判据，正是这份文档想找的那类问题本身。

补读之后**改了两条结论**:

| patch | 第一版 | 修订后 | 依据 |
|---|---|---|---|
| **P26** cron REST | 部分冗余 | **仍需要** | 它挂的是 `_handle_cron_pause` / `_resume` / `_delete` (plugin_cron.py)，上游只有 `POST /api/cron/fire` —— **零重叠**，不是重复 |
| **contextvars** | 上游只在一处用 copy_context | **部分重叠** | `gateway/platforms/api_server.py:5963` 注释明写「run_in_executor threads, so the profile scope must be re-entered」—— 上游知道这个问题，只处理了 profile scope；catfish 那个是全局 wrap `run_in_executor` |

另外 **P16 的理由是错的**。它不是"签名比对"，而是性能补丁 ——
plugin_session.py 的注释: 「hermes 原生 session_search 的 _discover mode
**76-101s**，改走 catfish 340ms 快版」。而上游 `tools/session_search_tool.py`
现在已经大改过 (dedup-by-lineage、FTS 扫描行数上限、compaction-archived 判别)，
**到底还慢不慢读代码判不出来，必须实测**。

### 修订后的账

| 判定 | 个数 | |
|---|---|---|
| 证据确凿可退役 | **1** | P36 |
| 疑似可退役，需实测 | **1** | P25 |
| 仍需要 | **4** | P27 / P12 / P13 / **P26** |
| 待实测才能定 | **2** | P16 (性能) / contextvars (覆盖范围) |

### 这份 audit 还欠什么

**29 个 A 类里只抽查了 4 个**实现 (P20 / `_patch_toolsets` / `_patch_memory_prefetch` /
P4)，都站得住。但 4/29 不足以支撑"29 个全是产品功能"这个结论。

要把它做实，剩下 25 个得逐个读实现。判据是同一条: **它动的是 catfish 自己的
概念 (X-Catfish-User / picker / Companion / 审批 / 中文化)，还是在补 hermes 的缺。**

在补完之前，这份文档的"29"只能当**上界**看 —— 真实的产品功能数 ≤ 29，
冗余候选 ≥ 8。

## 结论先行

**「很多 patch 已经冗余」这个假设，量化之后不成立。**

| | 个数 | |
|---|---|---|
| A. catfish 产品功能，hermes 不可能有 | **29** | 身份透传 / picker 联动 / Companion 集成 / 中文化 / 审批链路 / 记忆闸 / 工具可见性 |
| B. 修 hermes 的 bug 或补 hermes 的缺 | **8** | 只有这批可能被上游修掉 |

B 类 8 个逐个查完:

| 判定 | 个数 |
|---|---|
| 证据确凿可退役 | **1** (P36) |
| 疑似可退役，需实测 | **1** (P25) |
| 部分冗余，需比对 | **1** (P26) |
| 仍需要 (上游确实没有) | **3** (P27 / P12 / P13) |
| 待比对 (本次没读完 catfish 侧) | **2** (P16 / contextvars) |

也就是说: **9329 行里，能立刻拿掉的是 1 个 patch (约 27 行)**。

这跟我看完 Bot Mode 之后的第一反应相反 —— 当时我说「catfish 在 hermes 已经有
能力的地方又叠了一层」，那句话对 8/13 的 `catfish-autocompress`、对昨天的 tool
cap 是成立的，但**推广到整个插件不成立**。29/37 是真的产品代码。

Bot Mode 的可比性也要打折: 它做的是 UI 层聚合 (roster / 头像 / 路由到既有原语)，
catfish 这个插件做的是**协议层改造** (身份透传、审批、picker 联动) —— 那些东西
在 hermes 的公开面上没有对应入口，除了 monkeypatch 没有别的做法。

## B 类逐条

### P36 `_patch_p36_terminal_cwd_home` — ✅ 可退役

| | |
|---|---|
| 病因 | launchd 起 hermes 时 `os.getcwd()="/"`，LLM 相对路径全撞死 |
| 做法 | `os.environ["TERMINAL_CWD"] = $HOME` (plugin_misc.py:665-691) |
| 上游现状 | `hermes_cli/config_defaults.py:4264-4265` 原话:<br>`# NOTE: MESSAGING_CWD was removed here — use terminal.cwd in config.yaml`<br>`# instead. The gateway reads TERMINAL_CWD (bridged from terminal.cwd).` |
| 判定 | **上游把答案写在注释里了**: 配 `terminal.cwd`，gateway 自己桥到 TERMINAL_CWD |

**动作**: `~/.hermes/config.yaml` 加 `terminal: {cwd: "~"}`，删掉 P36。
config.yaml 是**升级保留**的 (upgrade-hermes-v020.sh 只备份不替换)，
比 monkeypatch 更稳。

⚠ 退役前确认一件事: P36 的语义是「**没设过才兜底**」(plugin_misc.py:671-677
尊重员工/装机脚本已有的 export)。换成 config.yaml 之后这个"尊重已有值"的行为
由谁保证 —— 要读 `_resolve_local_initial_cwd` 的优先级链。

### P25 `_patch_p25_cron_env_isolation` — 疑似可退役

| | |
|---|---|
| 病因 | cron job 往 `os.environ["HERMES_CRON_SESSION"]` 写，污染整个 daemon 进程 |
| 上游现状 | 新增 `gateway/session_context.py`；`tools/approval.py:238` 已改成<br>`get_session_env("HERMES_CRON_SESSION", "")` —— **session 作用域**<br>`tools/environments/base.py:471` 有清洗正则盖住这一族 var<br>全树搜 `environ[...]=` 写这个 var: **0 处** (只剩 session_context.py:240 的一句文档) |
| 判定 | 病因看着已经被架构改掉了 |

**实测确认方式** (不要凭 grep 下结论):
```bash
# 1. 临时禁用 P25 (注释掉 plugin.py 里那个 _try_patch)，重启 hermes
# 2. 跑一个 cron job
# 3. 看 daemon 进程的 env 有没有被污染:
ps eww $(pgrep -f "hermes.*gateway" | head -1) | tr ' ' '\n' | grep HERMES_CRON_SESSION
# 空 = 上游确实修好了，P25 可退役
```

### P26 `_patch_p26_cron_rest_endpoints` — 部分冗余

上游 `gateway/platforms/api_server.py:1982` 现在有 `POST /api/cron/fire`，
注释里还写着「The REST cron endpoints are authenticated」(:1269)。

P26 提供了哪几条、跟上游那条重不重 —— **本次没逐条比对**。
下一步: 列出 P26 注册的路由 vs 上游的，重的删掉。

### P27 `_patch_p27_cron_auto_retry` — 仍需要

`cron/*.py` 里搜不到任何 retry / backoff / max_retries。上游没有失败重试。

### P12 `_patch_p12_update_system_prompt_safe` — 仍需要

上游 `hermes_state.py:4208-4220` 确实重写过了 (改成 hash 存储 +
`_delete_unreferenced_system_prompts`)，但 P12 要防的那个 race 保护
**仍然不存在**:

```
sed -n 4205,4222p hermes_state.py | grep -cE "INSERT OR IGNORE|_insert_session_row"
→ 0
```

也就是 session row 还没 insert 上时，那个 UPDATE 照样 affect 0 rows。
P12 (wrap 前先 `_insert_session_row`) 仍然有效。

### P13 `_patch_p13_dump_naming_type_tag` — 仍需要

上游 `agent/agent_runtime_helpers.py:1887` 仍是
`f"request_dump_{safe_sid}_{timestamp}.json"` —— **没有 type 前缀**。
chat / bg-review / cron 的 dump 仍然混在一起排序。

### P16 `_patch_p16_session_search` — 待比对

上游 `tools/session_search_tool.py:848` 的 `session_search` 签名已经很丰富
(含 `around_message_id` 的 scroll 形态)。catfish 是**整个函数体替换** ——
本次没读 catfish 版加了什么，无法判定。

### `_patch_asyncio_executor_for_contextvars` — 待比对

上游 `agent/auxiliary_client.py:436` 用了 `contextvars.copy_context()`，
但只在那一处。catfish 的是全局 wrap `loop.run_in_executor`。
需要确认: 除了 auxiliary_client，还有没有别的路径丢 context。

## A 类 29 个 (不冗余，仅登记)

身份透传 7: p1 / p2 / p3 / p4 / p5_p6_p11 / p10 / p17
Companion 集成 3: p7 / p8_p9 / p24
picker 联动 2: p21 / p23
中文化 2: p14 / p28
审批链路 3: p15 / p15_2 / p20
记忆与工具闸 4: p42 / p44 / _patch_toolsets / _patch_memory_prefetch
工具可见性 3: p43 / p45×2
其它 5: p29 (/learn) / p30 (wechat qr) / p39 (codex auth) / p19 (lifecycle→SSE) /
p40 (Companion 双写去重)

其中两个一开始被我误分进 B 类，读完实现后归回 A:

- **P19** 不是"补 hermes 的缺"。上游 `agent_init.py:477/488` 确实同时有
  `tool_progress_callback` 和 `status_callback`，但 P19 桥的是
  lifecycle/warn → SSE 并带 `tool="catfish-lifecycle"` 标记给 Companion UI
  分发。产品集成，上游不会有。
- **P40** 解的是 Companion 特有的双写 (同一 session id 被请求入口和 turn flush
  各写一次)，判据是 `session.source == companion` + 30 秒窗。上游
  `hermes_state.py:7317` 的 `_is_duplicate_replayed_user_message` 是
  include_ancestors 的 replay 场景，不是同一件事。

## 真正该拿走的东西

不是「砍 patch」，是 Bot Mode 那个**默认姿势**:

> 能用公开面 (config / cron / CLI / 既有原语 + 命名约定) 就别 patch。

按这个尺子，这次量化找到的其实是**另一类问题** —— 不是"上游已经有了"，
而是"**本来就有更稳的做法，当时没用**":

1. **P36** 该是 config.yaml 一行，不是 monkeypatch (上游注释直接指路)
2. **两套调度并存**: hermes cron 2 个 job (AI五巨头日报 / 每周一待办生成)
   vs Companion Rust 侧 5 个自起线程 (distill / email / pet_hover / watchdog /
   autostart)。Bot Mode 的 Routines 就是普通 cron job 加 `[bot:<name>]` 前缀，
   所以 `hermes cron list` 和核心 Cron 页面都看得见。catfish 那 5 个线程
   在 hermes 侧完全不可见。
3. **`~/.hermes/profiles/` 一个都没用**。hermes 0.20 有这个原语
   (`hermes_constants.py:173/785`、CLI `profile` 子命令、
   `skill_manager_tool.py:775` 扫 `profiles/*/skills`)，
   而 catfish 用「网关一张 `SOURCE_TOOL_PROFILES` 手工白名单」模拟多身份。
   —— 这条只是**方向**，两者语义对不对得上没查过，别照着改。

## 方法备注

- 37 个 patch 由 AST 提取 (`ast.FunctionDef` 名字前缀 `_patch_` / `_install_`)，
  不是 grep 计数。之前 `grep -c "_try_patch("` 报 23，那个数把函数定义也算了，
  而且漏掉不走 `_try_patch` 的 17 个。
- 每条"上游现状"都给了文件+行号。凡是只有 grep 信号、没读实现的，一律标
  「疑似 / 待比对」，不写成结论。
- `~/Library/Logs/catfish/gateway.log` 类的运行时证据本次没用上 —— B 类判定
  全部来自静态代码比对。P25 那条需要跑一次才能定。
