# Hermes v0.23.3 升级评估 (2026-11-20)

> 现钉: v0.23.2 (`ae2a81cc`)。候选: v0.23.3 (`caafe538`, 2026-11-19 发布)。
> 结论: **不能直接升 —— 主插件在 0.23.3 上装不上**。要改的地方很小 (2 处补丁), 改完风险低。

## 这个版本是什么

名义补丁版, 实际是 v0.23.2 以来的合集: 370 个 PR、1,657 个提交、3,948 个文件 (+222,379 / −96,154)。上游没写细节 (等 v0.24.0)。
点名的: 存储层拆到 `persistence/` 接口后面 (SQLite 是其中一个后端)、提示缓存「保活 + 空闲压缩」控制器、`prompt_caching.cache_ttl`
默认 `auto`、压缩时把丢掉的工具结果归档、冷缓存时主动裁剪工具结果默认打开、几个依赖安全补丁。

## 查了什么、结果

方法沿用 0.23.2 那套 (scratch 两个 tag 各建 venv, `HERMES_ROOT` 指过去跑)。

| 项 | 结果 |
|---|---|
| 插件引用的 Hermes 内部符号 (AST 抽 190 处, 两版真 import + hasattr) | **少了 1 处: `hermes_state.SessionDB.update_system_prompt`** (搬进 `persistence/sqlite/conversation/_store/sessions.py`)。其余 14 处两边都「缺」, 跟以前一样是插件自己加的 |
| 真实网关测试 (委派 / 定时任务 / 微信补发) | 0.23.2: 26/26。**0.23.3: 起不来 —— `delayed install fail: '_SessionDB' has no attribute 'update_system_prompt'`, `_INSTALLED` 仍 False**, 后面所有补丁都没装, pre_tool_call 钩子会抛错 (等于小鲶不能用工具)。在临时副本里把 P12 改成「找不到就跳过」后: **26/26 通过** |
| 插件全量单测 | xcatfish-user 两版都 782 过 (单测不走 install, 所以没抓到上面那个); memory 两版都 500 过 + 同样 1 项 (树外链接, 跟版本无关) |
| `audit_hermes_compat.sh` | 两版都 96 过 / 0 失败, 输出一致 |
| P40 (Companion 会话 user 消息去重, 补在 `SessionDB.append_message`) | **补丁还能装上, 但不再生效**: 0.23.3 里网关 / TUI / 关停回写全部改走 `persistence.messages.append(...)`, 不经过 `SessionDB.append_message` 了 |
| Python 依赖 (`uv.lock`) | 354 个包, 只有 13 个小版本 (oauthlib 4.0.1、opentelemetry 1.45.2 一族、tornado 6.6.1, 都是安全补丁)。wheelhouse 要重打, 无原生库变化 |
| 构建后端 | 不变 (`setuptools==84.1.0`, `wheel==0.48.0`) |
| 会话库 (Companion / tool-bridge 直接读 `state.db`) | 建表 / 加列 / 索引语句 195 条, 两版**完全一样**; 各库 SCHEMA_VERSION 不变 |
| Hermes API 路由 | 两版都是 41 个, 一致 |
| 网关启动参数 | `--external-supervisor` / `--force` / `--replace` 都在 |
| 配置版本 | 53 → 54: 只把安装模板写进去的 `prompt_caching.cache_ttl: "5m"` 删掉 (默认改成 auto), 其它不动 |
| 危险命令中文说明 (`test_p55`) | 通过, 没有新增要翻的 |

## 新行为, 对我们的影响

- **提示缓存 1h / 保活 / 空闲压缩**: 保活要知道缓存单价才会发, 我们走 `custom` → 本机 8999 中央网关, 价格未知, 判断为不发;
  空闲压缩只对 1h / 30m 缓存档生效。员工个人 Claude key (1.2.228) 若走 Anthropic 线路, 有人值守的对话会用 1h 缓存 (写入单价 2 倍)。
  升级后在本机看一天 agent.log 的 `cache_warm` / compaction 记录确认。
- **冷缓存主动裁剪**: 员工离开一阵再回来, 8000 字以上的旧工具结果会在 `state.db` 里**原地改写**成摘要 (原文存 `cache/pruned/`)。
  工具调用参数 (比如 execute_code 的代码) 不改, 所以「对话里跑通 → 固化技能」取代码不受影响;
  只有输出特别长的 execute_code 结果会变成摘要, `skill_from_code.session_runs` 判断成功与否会看不到 `status`。
  (我们配置里 `proactive_prune_tokens: 80000` 早就开着, 这次只是门槛变低。)
- **`hermes_state.SessionDB` 已标注废弃, v0.27.0 删除**: 插件里 2 处 (P12、P40) 要迁到 `persistence` 接口。

## 升级要做的

1. P12: 目标改成新位置 (或确认上游 0.23.3 已无该竞态后退役), 至少做到「找不到就跳过、不拖垮整个 install」。
2. P40: 改挂到 `persistence` 的 messages.append, 或用上游新的 `message_uid` 幂等; 用真网关验证 Companion 会话不再出现重复 user 消息。
3. 按老流程: 钉新提交、重打 Hermes 离线包 (mac 两个架构 + Windows)、离线安装 / 就地升级 e2e、Windows MSI。
4. 本机网关换新版跑一天, 看 `delayed install ✓`、出卡、会中小鲶、群聊 @小鲶、缓存保活日志。

工作量: 代码改动小 (两处补丁); 主要是重打包和 e2e, 约半天到一天。
