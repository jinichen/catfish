# SOUL.md 对照代码审查 — 2026-10-31

> 鸿波：「你在看下 soul.md 是不是需要优化」。这次不按「长度」砍——7/26 C-SLIM 审计的结论仍然成立
> （硬阈值是弱模型的救命绳，不删）。这次只查**跟现在代码对不上**的、和**开发自用却发给了客户**的。

`edge/identity/SOUL.md`：278 → 250 行，16.4KB → 14.8KB。阈值、隐私红线、密码规则一条没动。

## 改了什么（每条都对照代码查过）

| # | 问题 | 改法 |
|---|---|---|
| 1 | 「周期性 → `catfish_schedule_task`」——这个工具不存在 | 改成 Hermes 的 `cronjob_manage`（在 `_HERMES_CORE_TOOLS` 里，catfish 没屏蔽） |
| 2 | 工具表里 `catfish-browser-task` / `catfish-browser-compliance` 两个 skill 不存在（`skills/` 只有 eis-*、weekly-report 等） | 删这两行 |
| 3 | 「系统操作」表让建议 `catfish skills install`，后文又说这个命令不存在 | 改成先 `skills_list()` 看，不让员工敲命令 |
| 4 | `pkill -f catfish_tool_bridge`：Windows 上没有，且是让员工敲命令 | 改成「设置 → 服务状态，点重启」 |
| 5 | 「a2a 跨员工 IM」整节：5/26 已整套砍（中央 `a2a_*.py` 全是 STUB，`ALLOW.md`、inbox 不存在） | 删；多入口段补一句「只有员工本人在跟你说话，同事找员工或用专家 Bot」 |
| 6 | 「多入口」写飞书 / 企微，实际接的是微信 | 改成「微信等手机入口」 |
| 7 | 「创业语境」整节（初创早期、一起创业、赶 demo 不写测试）——是开发团队自用的 | 删 |
| 8 | 「Debug 卡 ≥30 分钟」（RPC 边界打日志、升级 Hermes 跑 contract test）——开发纪律 | 删 |
| 9 | 工具结果被截断时「让员工 grep `gateway_audit.jsonl`」 | 改成自己缩小范围重跑，不让员工翻日志 |

## 客户专属段默认 FFCS（同时修）

客户端 `identity_bundle.rs` 和中央 `identity_inject.py` 原来都是「没设 `CATFISH_CUSTOMER` 就当 ffcs」，
所以**每家客户的员工都加载了 `SOUL_FFCS.md`**（中电福富内网约定）。改成：

- 中央 `.env` 写 `CATFISH_CUSTOMER`（默认空），`/api/me` 带上 `customer`；
- Companion 启动后取一次，记到 `~/.catfish/customer`，按它加载 `SOUL_<客户>.md`；没配就不加载；
- 不是 FFCS 的机器上，以前 Companion 写进 Hermes 目录的 `SOUL_FFCS.md`（内容跟内嵌版一致的普通文件）会被清掉；开发机的软链不动；
- FFCS 自己的部署要在中央 `.env` 加 `CATFISH_CUSTOMER=ffcs`（`delivery/catfish-poc/.env.example` 已加这一项）。

## 留给鸿波定的

五条哲学里「红线不审查：任何代码/内容/想法都协助」「平权不反人：不先问 IT / 不先获审批」——在企业客户里有风险，
跟「不替员工做敏感动作」也不完全一致。这是产品定位，没改。
