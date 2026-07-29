# 技术债:换服务器 = 10 处散落状态的人肉巡检

> 2026-07-28 · 达华 POC 联调实录。当天从"本地原生栈"切到"远端 Docker 栈",
> 以下每一处都真实炸过一次,每次表象都不同,总计耗掉一个晚上。
> 这不是偶发,是架构问题:**没有单一事实源,没有派生物失效机制。**

## 一、散落点全录(全部当天实证)

| # | 位置 | 内容 | 谁写它 | 当天的故障表象 |
|---|---|---|---|---|
| 1 | `~/.catfish/companion.yaml` | `endpoints.gateway_url` / `endpoints.web_url` / `oidc.issuer` | UI 服务器配置(web_url 7/28 才补上) | 门户链接指向 127.0.0.1:5173 |
| 2 | `~/.catfish/memory_plugin.yaml` | `gateway.url` | UI 保存时同步 | — |
| 3 | `~/.hermes/.env` | `CATFISH_GATEWAY_URL` + `OPENAI_API_KEY`(30 天 service token) | UI 写 URL;Companion 启动 sync 写 token | **半新半旧**:URL 指远端、token 是本地 identity 签的 → 远端 401 invalid iss |
| 4 | `~/.hermes/config.yaml` | LLM provider 的 `model.base_url` | **没人同步(漏)** | hermes 调 localhost:8999 → Connection error,聊天无响应 |
| 5 | `~/.catfish/auth/token.json` | 员工登录凭证(绑定签发方 iss) | OAuth 登录 | 旧栈签的 token 打新栈 → /api/me 401 |
| 6 | `picker_state.json` | 上次选的模型名(绑定服务器 catalog) | 聊天界面选择器 | 残留 `deepseek-flash`,远端 catalog 没有 → 即使连上也 404 |
| 7 | 进程内存 | Companion `OnceLock`(endpoints/证书);hermes 启动读一次 .env/config | — | 文件改对了,跑着的进程仍用旧值;必须知道"要重启哪个" |
| 8 | `~/.hermes/.env` | **`OPENAI_BASE_URL`** | **没人同步(漏)** | 同一文件里 `CATFISH_GATEWAY_URL` 已指远端、`OPENAI_BASE_URL` 仍 `localhost:8999` → OpenAI SDK 读后者,**压过 config.yaml** → 改了 config.yaml 也没用,仍 Connection error |
| 9 | `~/.hermes/auth.json` | `credential_pool.openai-api[].base_url` | jwt-sync 只改 `api_key`,不改 `base_url` | 同上,第三处 URL 副本 |
| 10 | `~/.catfish/picker_model` | 上次选的模型名(纯文本一行) | Companion `set_picker_model` | **优先级最高**:每次 Companion 启动注入 store.model。删 `picker_state.json`(#6)会被它立刻灌回 → 旧模型名"删不掉",新网关 404 |

7/28 深夜追加的 #8 #9 #10 全部是当晚现场逐个撞出来的,每撞一个耗 20–40 分钟。
#8 尤其阴:**同一个 .env 文件里两个 URL,一个同步一个不同步**,现场看 `grep 199 .env` 有命中就以为改好了。

### 附:同类 UI bug(不是散落点,但同源)

`ChatModelPicker` 是 `<select value={current}>`。换服务器后 `current`(旧模型 ID)
不在新 catalog 的 options 里 —— **HTML select 在 value 失配时显示第一项、值不变**。
结果:UI 显示"通义千问 Plus",实际发出去的仍是 `deepseek-flash`。
且 `modelPickedByUser=true` 会让"跟随 catalog.default"的自动修正逻辑跳过,自己好不了。
修法(未做):失配时 fail-loud —— 显示"⚠ 已选模型 X 不在当前服务器",并强制回落 catalog.default。

外加两处相关默认值(不是散落,但放大了故障):
- identity `app.py _issuer_url()` 默认 `http://127.0.0.1:8998` → 本地栈签的 token iss 全是这个
- Companion `oauth.rs` 自动生成的默认 yaml 指向本地 8998

## 二、为什么是灾难

- 7 处没有任何一处知道其它 6 处的存在;
- 每处失效的**报错互不相同**(401 invalid_client / 401 iss mismatch / Connection error / 404 model / 门户离线),
  现场无法从表象反推是哪一处;
- token 类派生物(#3 #5)**不记录自己从哪个 issuer 来**,配置换了它们不会自我作废;
- 进程读一次缓存(#7)使得"文件已改对"与"实际生效"之间永远有一段无法观测的窗口。

500 人规模下,任何一次服务器迁移/换 IP,这 7 处 × 500 台 = 纯人肉灾难。

## 三、目标设计(三条原则)

1. **单一事实源**:服务器身份只存 `companion.yaml` 一处
   (`gateway_url` + `issuer` + `web_url`)。其余全部是派生物,禁止手工维护。
2. **派生物带指纹**:写入任何派生物(env token / auth token / picker_state /
   hermes config)时,附带 `server_fingerprint = hash(issuer + gateway_url)`。
   读取时指纹与当前事实源不符 → 视为不存在,自动丢弃并重建。
   (token 天然带 iss,校验 iss == 配置 issuer 即可,不需要额外字段。)
3. **启动自检可见**:Companion 启动时跑一遍派生物 × 事实源一致性检查,
   结果进日志 + 仪表盘;不一致的项标黄并说明会自动重建。
   "文件对不对"从需要人肉 grep 变成一眼可见。

## 四、分期(按杀伤面排序)

**P1(最小改动,杀掉最大一类):启动时 token 指纹校验。**
- `sync_service_token_to_env` 写 token 前、`ensure_fresh_access_token` 用 token 前:
  解 payload 比对 `iss` 与 `OidcConfig.issuer`,不一致 → 丢弃重取 / 强制重登。
- 覆盖 #3 #5 两处,当天最费时间的两个 401 都源于此。

**P2:UI 保存服务器配置时,一次写全所有派生点。**
- `write_server_config` 补写 `~/.hermes/config.yaml` 的 `model.base_url`(#4,当前漏)。
- **补写 `~/.hermes/.env` 的 `OPENAI_BASE_URL`(#8)与 `auth.json` 的
  `credential_pool[].base_url`(#9)** —— 7/28 实证:漏这两处时改 config.yaml 完全无效。
- 保存后删除 `picker_state.json`(#6)**和 `~/.catfish/picker_model`(#10)**
  —— 只删前者会被后者在下次启动灌回。
- 保存成功的提示里列出"已更新的 N 处 + 需要重启的进程清单"(#7 可见化)。

**P2.5:模型 ID 失配 fail-loud(见上文附)。** 一处 UI 改动,挡住"看着对、发出去错"
这一整类误判 —— 当晚在它身上单独耗掉近一小时。

**P3:收敛。**
- `memory_plugin.yaml` 的 `gateway.url`、`.env` 的 `CATFISH_GATEWAY_URL`
  改为启动时从 companion.yaml 生成,文件本身不再是配置源。
- hermes 的 LLM base_url 若可行,同样由 plugin 启动时从 env 推导
  (受"不改 hermes 源码"约束,方案待 hermes 侧确认)。

## 五、不做什么

- 不做配置中心/远程下发 —— POC 规模不需要,先把本机一致性做对。
- 不改 hermes 源码 —— 只动它的配置文件与我们的写入方。
