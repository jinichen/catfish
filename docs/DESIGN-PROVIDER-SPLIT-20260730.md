# 设计：拆分「供应商」与「模型」，key 加密存库

> 状态：**待鸿波确认，未动代码**
> 起因：鸿波 7/30 —— "是不是要单独分模型和 LLM 提供商的参数分别设置？模型参数写数据库，KEY 写入 .env，写入 KEY 后自动重启网关？"

---

## 一、先说三条结论

| 提议 | 结论 | 依据 |
|---|---|---|
| 拆分供应商 / 模型 | **做** | 见下方订正 |
| KEY 写入 `.env` | **做不到**，不是风险问题 | 见 §2 |
| 写入后自动重启网关 | **不做**，而且不必要 | 见 §3 |

真正的解法是第三条的反面：**让 key 不再走环境变量**，那么"填了就生效"是自然结果，根本不需要重启。

> **8/1 订正（第一条的依据）**：设计初稿写"7 个模型只有 5 组 upstream 配置"，
> **这个数是错的，实际是 6 组**——§6.1 那张表本身列的就是 6 行，是正文算错了，
> 而我在对话里还重复了几遍。今天真正重复的只有 Gemini 那一对。
>
> 所以拆分的收益**不在**"消除现有重复"（只省一行），而在四件后续的事：
> ① 加同一家的新模型不用重敲端点和 key 变量名；
> ② 客户现场**能加供应商**——现在做不到（§2）；
> ③ 换 key 是按供应商一次，不是按模型多次；
> ④ 它是把 key 移出环境变量的前提。
>
> 这四条仍然成立，但"重复是实打实的"这句站不住。
> 测试 `test_七个模型拆成六个供应商` 用真实数据把这个数钉住了。

---

## 二、为什么 "KEY 写入 .env" 在当前部署下做不到

三个层面，任何一个都足以否掉：

**1. 网关碰不到那个文件。** `.env` 在宿主机上，网关跑在容器里。容器内没有这个文件，也不该给容器写宿主机文件的权限。

**2. 就算能写，容器也看不见新变量。** `central/docker-compose.yml:136-139` 是**显式列名转发**：

```yaml
INTERNAL_LLM_KEY: ${INTERNAL_LLM_KEY:-}
INTERNAL_LLM_BASE_QWEN_MAIN: ${INTERNAL_LLM_BASE_QWEN_MAIN:-http://127.0.0.1:9998/v1}
INTERNAL_LLM_BASE_QWEN_VISION: ${INTERNAL_LLM_BASE_QWEN_VISION:-http://127.0.0.1:9998/v1}
INTERNAL_LLM_BASE_BGE_M3: ${INTERNAL_LLM_BASE_BGE_M3:-http://127.0.0.1:9998/v1}
```

客户在界面上加一家新供应商（比如 Moonshot），`.env` 里写进 `MOONSHOT_API_KEY`，**容器永远读不到** —— 除非同时改 `docker-compose.yml` 再重建。也就是说"界面上加供应商"这件事在现在的架构下压根不成立，改 UI 解决不了。

**3. 那之后也不是"重启网关"能生效的。** 环境变量在**容器创建时**固定。要 `docker compose up -d` 重建容器，不是重启进程。

---

## 三、为什么不做"自动重启网关"

**会掐断所有正在进行的对话。** 网关的 chat 走 SSE 流式，重启 = 每个正在对话的员工当场断流。管理员在后台点一下保存，全公司的对话同时断 —— 而他不会想到这两件事有关系。

**一个 web 请求重启自己的服务器是不可靠的。** 响应大概率回不来，界面上表现成"点了没反应"；重启失败的话更糟：服务没了，而管理员以为只是没反应，继续点。

**而且不必要。** 配置系统 7/30 已经做了热加载（`config.py` 的 TTL 3 秒 + `gateway_config_meta.revision`）。改模型参数从来不需要重启。**唯一需要重启的就是新环境变量** —— 而那恰恰是因为 key 走了 env。

把 key 从 env 里拿出来，重启这个需求本身就消失了。

---

## 四、数据模型

### 4.1 新表 `gateway_providers`

```sql
CREATE TABLE gateway_providers (
    id           text PRIMARY KEY,          -- 'dashscope' / 'internal-vllm'
    display_name text NOT NULL,             -- '阿里云百炼' / '内网 vLLM'
    api_base     text,                      -- NULL = 用 SDK 默认端点 (Gemini 就是)
    -- key 的两条来源, 互斥。二选一, 不能都空。
    api_key_env  text,                      -- 老路: 读环境变量 (存变量名)
    api_key_enc  bytea,                     -- 新路: 加密后的密文
    timeout      int  NOT NULL DEFAULT 60,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   text
);
```

`gateway_models.payload` 里的 `upstream` 由

```jsonc
{ "model": "openai/qwen_v3_6_35b_a3b", "api_base": "...", "api_key_env": "...", "timeout": 180 }
```

变成

```jsonc
{ "model": "openai/qwen_v3_6_35b_a3b", "provider": "internal-vllm", "timeout": 180 }
```

`timeout` 留在模型这一层 —— 它是模型属性不是供应商属性（同一个内网 vLLM 上，视觉模型 180s、embedding 30s）。供应商那份是默认值。

### 4.2 所有调用点零改动

这是这次改动能安全做的关键。全仓 **30 处**读 upstream，全部走这五个成员：

```
m.upstream.model         6 处   m.upstream.api_key       3 处
m.upstream.api_base      4 处   m.upstream.timeout       2 处
m.upstream.is_available  9 处   m.upstream.api_key_env   2 处 (只用于报错文案)
```

**`UpstreamConfig` 的对外接口一个字不改**，只改它的值从哪来：组装配置时把 provider 行合并进去。`network.py` / `app.py` / `facts_pipeline.py` / `fallback.py` / `catalog.py` 全部不动。

```python
# config.py 里, 组装时合并 (伪码)
def _merge_provider(row: dict, providers: dict[str, dict]) -> dict:
    up = dict(row["upstream"])
    p = providers.get(up.pop("provider", ""), {})
    return {**row, "upstream": {
        "model": up["model"],
        "api_base": p.get("api_base"),
        "api_key_env": p.get("api_key_env") or "",
        "_secret": p.get("api_key_plain"),      # 解密后, 只在内存
        "timeout": up.get("timeout") or p.get("timeout") or 60,
    }}
```

`UpstreamConfig.api_key` 改成：有 `_secret` 就用它，否则退回 `os.environ.get(api_key_env)`。`is_available` 同理。**两条路并存**，这也是迁移期的兼容基础。

---

## 五、key 加密

### 5.1 算法

`cryptography` 已在环境里（48.0.0），用 **Fernet**（AES-128-CBC + HMAC-SHA256，带时间戳，标准库级封装，不需要自己拼 nonce）。

主密钥来自 `CATFISH_SECRET_KEY` 环境变量，一次配好不再动：

```bash
# 生成 (部署时跑一次, 存进 .env 和密码管理器)
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 5.2 主密钥没配时的行为

**不能静默降级。** 如果 `CATFISH_SECRET_KEY` 没设：

- 库里已有密文 → 那些 provider 标记为不可用，`is_available` 返回 False，管理页显示"主密钥没配，这些供应商的 key 解不开"。**不抛异常掀翻整份配置**（同 7/30 那条教训）。
- 界面上新增/修改 key → **拒绝保存**，明确说要先配主密钥。不能让人以为存进去了。

### 5.3 key 永不回传

`GET /api/admin/providers` 返回 `has_key: bool` 和 `key_source: "env" | "stored"`，**永远不返回密文也不返回明文**。

编辑时 key 输入框留空 = 不改；填了 = 覆盖。这是密码字段的标准做法，也避免了"界面往返一次把 key 明文送了一圈"。

### 5.4 轮换

换 key = 在界面上重填一次，立即生效（配置热加载）。
换**主密钥**需要停机脚本：用老主密钥全部解密、用新的全部加密。这个脚本要跟设计一起写，不能等到真要换的时候现想。

---

## 六、迁移

### 6.1 从现有 7 个模型推导 provider

现状（实查 `models.yaml`）：

| provider id | api_base | api_key_env | 用它的模型 |
|---|---|---|---|
| `internal-qwen-main` | `${INTERNAL_LLM_BASE_QWEN_MAIN}` | `INTERNAL_LLM_KEY` | private-main |
| `internal-qwen-vision` | `${INTERNAL_LLM_BASE_QWEN_VISION}` | `INTERNAL_LLM_KEY` | private-vision |
| `internal-bge-m3` | `${INTERNAL_LLM_BASE_BGE_M3}` | `INTERNAL_LLM_KEY` | private-embed |
| `dashscope` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `DASHSCOPE_API_KEY` | public-qwen-flash |
| `deepseek` | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` | public-deepseek-flash |
| `gemini` | *(空)* | `GEMINI_API_KEY` | public-gemini-pro, public-gemini-flash |

按 `(api_base, api_key_env)` 去重自动生成，id 用可读名。三个内网的 base 不同所以是三条 —— 这本身也说明"内网 vLLM"其实是三个端点，之前被"共用一个 key"掩盖了。

### 6.2 迁移分两步，中间可停

**第一步（本次）**：建表、生成 provider、模型改成引用、`api_key_env` **原样保留在 provider 上**。此时行为跟现在完全一致，key 仍走 env。**这一步不碰任何 key，风险只在数据模型。**

**第二步（确认第一步稳定后）**：界面上支持"把 key 存进库"，逐个 provider 迁。`api_key_env` 和 `api_key_enc` 并存，迁一个少一个。全部迁完之后 `.env` 里只剩 `CATFISH_SECRET_KEY` 和数据库口令。

### 6.3 回滚

第一步的 alembic `downgrade()` 要能把 provider 字段合并回 `gateway_models.payload`，而不是只 `DROP TABLE` —— 否则回滚等于把模型配置删了。这条要写测试。

---

## 七、界面

侧栏「接入」组下新增一项：

```
接入
  供应商    ← 新增
  模型
  配额
  部门权限
```

**供应商页**：列表（名称 / 端点 / key 状态 / 用它的模型数），点进去编辑。key 那格是 password 类型，留空=不改。

**模型页**：「上游接入」那一组从 4 格变 2 格 ——

```
供应商  [内网 vLLM (视觉)  ▾]  ← 下拉, 旁边"管理供应商 →"
上游模型 [openai/qwen_v3_6_35b_a3b]
超时     [180]
```

`api_base` / `api_key_env` 两格从模型页消失。这也顺带解决了 7/30 那个问题：内网地址不再出现在模型编辑页上。

**删除保护**：还有模型在用的 provider 不许删，错误信息里点名是哪几个（同现在 fallback 链的做法）。

---

## 八、风险清单

| 风险 | 处理 |
|---|---|
| 主密钥丢失 → 所有存库的 key 解不开 | 部署 SOP 里写明：生成后立刻存进密码管理器；`达华服务端一键部署SOP.md` 加一节 |
| 拿到「库 + 主密钥」= 拿到所有 key | 现状拿到宿主机 `.env` 同样拿到全部，**安全性不降低**；而 `pg_dump` 里从明文变密文，是净提升 |
| 迁移写坏 → 模型全部不可用 | 分两步；第一步不碰 key；`downgrade()` 要能真回滚并有测试 |
| provider 下拉选错 → 模型指向错端点 | 保存前展示"这个供应商的端点是 X，key 状态 Y"；错了当场看得见 |
| 新增 `cryptography` 显式依赖 | 已在环境里（48.0.0），显式声明进 `pyproject.toml`，锁上界防 2.x |

---

## 九、验证计划

沿用这两天的做法 —— **每条测试都注入故障确认会红**，不确认的测试不算数。

- provider 合并后 `UpstreamConfig` 的五个成员跟改造前逐字段相等（用现有 7 个模型做黄金对照）
- 加密/解密往返；主密钥没配时**不抛异常**、标记不可用、界面能看到原因
- `GET /api/admin/providers` **永远不含** key 的任何字节（正查密文、明文、前缀）
- 删除仍被引用的 provider 要拒绝，且发生在真删之前
- alembic upgrade → downgrade → upgrade，模型配置逐字段不变
- 迁移期两条路并存：一半 provider 走 env、一半走库，都能正常调用

---

## 十、工作量与顺序

1. alembic 008 + `provider_store.py` + 迁移脚本
2. `config.py` 合并逻辑（**这一步做完，30 个调用点应该一个都不用改** —— 如果需要改，说明合并的位置选错了）
3. 加密模块 + 主密钥缺失的降级路径
4. `/api/admin/providers` CRUD
5. 供应商页 + 模型页「上游接入」改 2 格
6. 部署 SOP 补主密钥生成与备份

第 2 步是这次改动的成败点：**如果它没让调用点归零改动，方案本身要重想。**
