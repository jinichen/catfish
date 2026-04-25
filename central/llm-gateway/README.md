# catfish-gateway · LLM 网关

> 鲶鱼平台的"数据主权支点"。所有员工 agent 的 LLM 请求都经它。

---

## 能做什么

- OpenAI 兼容接口（Hermes 无需改代码即可接入）
- 支持公司私有 LLM（vLLM / SGLang / 任何 OpenAI 兼容端点）
- 模型目录（`/v1/catalog`）让员工看到可用模型
- 元数据审计（token 数、延迟、错误），**永不记录对话内容**
- Dev 模式 SSO 绕过（P0），后续接真 OIDC

---

## 环境要求

- Python **3.12**（推荐，最稳定）
- 能访问公司内部 LLM 平台（本项目默认 `10.10.40.102:32730`）

---

## 快速开始

```bash
# 1. 安装
python3.12 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"

# 2. 配置
cp .env.example .env
# 编辑 .env，填入 INTERNAL_LLM_KEY（从公司 LLM 平台申请）

# 3. 启动
python -m catfish_gateway.app

# 另一个终端跑测试
./scripts/test_chat.sh
```

访问：
- `http://localhost:8000/docs` — FastAPI 自动文档
- `http://localhost:8000/health` — 健康检查

---

## 配置模型

编辑 `config/models.yaml`，现成包含三个模型：

| 模型名 | 类型 | 说明 |
|---|---|---|
| `catfish-private-main` | chat | Qwen3.5 122B (A10B MoE) · 主力 · 25K 窗口 |
| `catfish-private-vision` | chat | Qwen3-VL 30B (A3B MoE) · 多模态 |
| `catfish-private-embed` | embedding | BGE-M3 · 给 Hermes memory 用 |

---

## Docker 方式

```bash
cp .env.example .env
# 填入 INTERNAL_LLM_KEY
docker compose up -d
docker compose logs -f
```

---

## 给 Hermes 接入

在员工侧的 `~/.hermes/config.yaml` 写：

```yaml
llm:
  provider: openai
  api_base: http://<gateway-host>:8000/v1
  api_key: <dev-token-or-sso-token>
  model: catfish-private-main
```

或者用环境变量：

```bash
export OPENAI_API_BASE=http://<gateway-host>:8000/v1
export OPENAI_API_KEY=<dev-token>
export OPENAI_MODEL_NAME=catfish-private-main
```

---

## 项目结构

```
llm-gateway/
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── config/
│   └── models.yaml              # 模型定义
├── src/
│   └── catfish_gateway/
│       ├── __init__.py
│       ├── app.py               # FastAPI 入口
│       ├── config.py            # 配置加载
│       ├── auth.py              # 鉴权（Dev/SSO）
│       ├── catalog.py           # 模型目录端点
│       └── metrics.py           # 元数据审计
├── scripts/
│   └── test_chat.sh             # 冒烟测试
└── tests/
    └── test_smoke.py
```

---

## 安全承诺（写进代码的约束）

1. ✅ **对话正文永不入库**（`metrics.py` 只记元数据）
2. ✅ **API Key 只在服务器上**（员工端只看到网关）
3. ✅ **LiteLLM verbose 模式关闭**（避免意外日志泄漏）
4. ✅ **错误消息截断 200 字符**（避免上游泄漏 prompt 片段）

可通过 `scripts/audit_logs.sh` 审计确认日志中没有对话内容。

---

## P0 限制 → P1 将补齐

| 当前 P0 | P1 补齐 |
|---|---|
| Dev 静态 token | 真实 SSO OIDC JWT 验证 |
| 内存 dict 记元数据 | Postgres 持久化 + 用量报表 |
| 单实例 | 多副本 + 健康检查 + fallback |
| 日志到 stdout | 结构化日志到 Loki/ELK |
| 无限流 | 按员工/部门限流 |
