# llm-gateway config-overlay · 客户定制层

打包时两步合并，得到客户实际拿到的 `config/`：

1. `central/llm-gateway/config/` → `delivery/catfish-poc/llm-gateway/config/`（通用 baseline）
2. `delivery/catfish-poc/llm-gateway/config-overlay/` → 同上 `config/`（覆盖）

overlay 里只放**跟通用 baseline 不一样的文件**，git track 的是差异。打 tar 时 overlay 被 exclude，客户只看到合并后的 `config/`。

## 默认是空的

三个文件（`models.yaml` / `roles.yaml` / `quotas.yaml`）现在都是带说明的空模板，**空是正常状态**——客户直接用通用 baseline 就能跑。只有这家客户确实要改，才往对应文件里写那几行。

9/22 之前这里躺着某一家客户的真实配置：单 model、他们的上游、他们的部门表和额度。每打一个包都会把那家的选择原样带给下一家，而且没人会注意到——因为合并之后看起来就像是"默认就这样"。

## 什么该进 overlay，什么该进 baseline

判据只有一句：**下一家客户是不是也需要这条？**

是 → 写进 `central/llm-gateway/config/`（baseline），所有客户都拿到。
只有这家要 → 才写在 overlay 里。

9/22 按这条判据搬了一样东西回 baseline：`mcp_registry` / `skills_hub` / `wiki_hub` 三段 `upstream_url`。它们是任何 docker 部署都必需的（容器里 `127.0.0.1` 指向 gateway 自己，不走 compose 服务名就是 502），却一直只存在于某一家的 overlay 里。换一家客户就会漏，而漏掉的症状只出现在「系统管理」页面上显示"不可达"——登录、聊天、门户全都正常，没人会第一时间点开那一页。7/29 现场就是这么发现的。

## 改完怎么验

`delivery/build-package.sh` 打包时会校验合并后的 `config/models.yaml`，确认那三个内部服务的 `upstream_url` 指向 compose 服务名。写错会当场红，不会打出坏包。

模型和额度这两类改动它验不了——那得拿真账号跑一次 chat，和看一眼额度有没有真的卡住。
