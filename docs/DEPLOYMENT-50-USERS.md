# 鲶鱼 Catfish · 系统部署清单

> **规模**：初期 50 名员工 / 并发峰值 15 人
> **模型**：DeepSeek V4 Flash（主力）+ DeepSeek V4 Pro（推理增强）双 SKU 私部
> **覆盖**：两套部署形态 —— 客户公有云（A）+ 客户内网完全私部（B）
> **更新日期**：2026-05-25 (5/24 → 5/25 修正 — 见底部 changelog)
>
> **5/25 修正摘要 v1.2 (鸿波 review HF + 提醒 "客户用 H20")**:
> - **关键澄清**: 客户实际部署 **H20** (国产合规版, 96GB, **不支持原生 FP4**), 不是 H100. V4 Flash 必走 FP8 fallback, weights **284 GB** (不是 H100 mixed 178 GB)
> - 50 人 / 并发 15 **H20 主推** = **8× H20 单机 (768 GB)** — 4 卡 H20 (384 GB) 装下但余量 63 GB 太紧, 长 context 撑不住
> - H100/H200 (有 FP4) 仅作 alternative 列出, 客户实际不用
> - V4 Hybrid Attention (CSA+HCA) → KV cache **仅 V3.2 的 10%** (跟卡无关, 模型层优化, H20 部署同样受益)
> - H20 compute ~15% of H100 但 memory bandwidth 4.0 TB/s (H100 是 3.35 TB/s) — 反超! MoE active params 路由对 bandwidth 敏感, TTFT 仅慢 1.5-2x 可接受
> - 加 4.7 V4 unique 特性 (3 种 reasoning mode / 无 Jinja chat template / HF 无 Inference Provider / Pre-train 32T tokens)
> - 加 4.8 部署 trigger checklist (vLLM 0.7+ / CUDA 版本 / chat template 适配 / 真测 1M)

---

## 0 · 一页摘要

| 维度 | 方案 A · 公有云中央 + 内网 LLM | 方案 B · 全内网私部 |
|---|---|---|
| **catfish 中央服务位置** | 客户购买的公有云 ECS（阿里云 / 腾讯云 / 华为云） | 客户机房物理机 / 虚拟机 |
| **DeepSeek 推理位置** | 客户机房 GPU 集群（专线回流） | 客户机房 GPU 集群（同网段） |
| **数据出门？** | OIDC 票据 + 调用元数据可能经公有云；prompt/response 可走专线全程内网 | 0 字节出门 |
| **网络条件** | 公有云 ECS ↔ 客户内网 GPU 需 IPsec/SD-WAN/专线，最低 100Mbps，<30ms | 全部同机房，可走 万兆/RDMA |
| **运维责任** | 公有云厂商负责硬件，catfish 团队负责软件，客户 IT 负责 GPU 推理 | catfish 团队 + 客户 IT 共同负责 |
| **首期成本量级**（不含人力） | 中央 ≤ ¥2k/月 + GPU 一次性 ¥1.8M+（仅 Flash）/ ¥6M+（含 Pro）| 同左 GPU + ¥10-30k 通用服务器一次性 |
| **典型适用** | 客户已有公有云资源池 / IT 弱、想云托管 | 央国企 / 金融 / 涉密 / 强合规 |

> 两套都 **共用** 同一套 catfish docker stack（postgres + identity + gateway + skills-hub + nginx）+ 同一套 DeepSeek 推理栈（vLLM / SGLang），只是机器在不同地方。

---

## 1 · 容量假设与流量模型

### 1.1 用户行为基线（保守估算）

| 参数 | 单人每日 | 50 人每日 | 50 人每月 (22 工作日) |
|---|---|---|---|
| 对话轮次 | 30 turn | 1,500 turn | 33,000 turn |
| 平均 prompt 大小（含 system + journal + tools）| 4,000 token | 6.0M | 132M |
| 平均 completion 大小 | 600 token | 0.9M | 19.8M |
| 工具调用次数（每轮约 0.5 次）| 15 | 750 | 16,500 |
| Companion 长连接（SSE）持续时长 | 上班 8h | — | — |

**月度 token 总量**（input + output）≈ **152M token/月**

### 1.2 并发模型

- 「并发 15」= 同一时刻 15 人正在让模型推理（按一个 token 在流出的瞬间算）
- 业内经验：员工活跃时段 9:30–11:30 / 14:00–17:00 是峰值，其它时段并发 ≤ 5
- 容量按峰值 **20 并发** 设计（15 + 25% 冗余）

### 1.3 LLM 推理压力换算

| 模型 | 单请求 TTFT 目标 | 单请求总 latency | 峰值需要的 throughput |
|---|---|---|---|
| DeepSeek V4 Flash（**284B 总 / 13B 激活**, FP4+FP8 mixed） | < 1.5s | 5–15s | ≥ 800 token/s 总吞吐 |
| DeepSeek V4 Pro（**1.6T 总 / 49B 激活**, FP4+FP8 mixed） | < 3s | 10–40s | ≥ 600 token/s 总吞吐 |

> MoE 模型的"内存"由 **总参数量** 主导（所有 expert 必须 load 进 VRAM 才能路由），"算力 / 单 token 速度"由 **激活参数量** 主导。Flash 13B active 推理跟 Llama 13B 同档（很快），但 284B 总权重决定了**起步就要数百 GB VRAM**。Pro 1.6T 总权重直接进入"集群级"门槛。

---

## 2 · 方案 A · 公有云中央 + 客户内网 DeepSeek

### 2.1 拓扑图

```
                ┌─────────────────────────────────┐
                │ 公有云 VPC (阿里云华东2 / 腾讯云北京) │
                │                                 │
   员工电脑──▶│  ECS-1 (中央)                    │
   Companion   │   ├─ nginx (443)                │
   (50 台)     │   ├─ catfish-gateway (8999)     │
                │   ├─ catfish-identity (8998)    │
                │   ├─ skills-hub (8997)          │
                │   └─ postgres (5432, 本地)      │
                │                                 │
                │  RDS PostgreSQL (可选, 替代本地) │
                └───────┬─────────────────────────┘
                        │ IPsec VPN / 云联网 / 专线
                        │ (≥100Mbps, ≤30ms RTT)
                        ▼
                ┌─────────────────────────────────┐
                │ 客户机房 (10.10.40.0/24)         │
                │                                 │
                │  GPU 推理节点 ×N                │
                │   ├─ DeepSeek V4 Flash (vLLM)   │
                │   └─ DeepSeek V4 Pro (vLLM/SGLang)│
                │                                 │
                │  客户 IdP (SSO, 可选)           │
                └─────────────────────────────────┘
```

### 2.2 公有云资源清单（中央侧）

| 资源 | 规格建议 | 月费参考 (阿里云华东2) | 说明 |
|---|---|---|---|
| **ECS-1**（catfish 中央全栈）| 4 vCPU / 8 GB RAM / 100 GB ESSD PL1 | ¥350–500 | 装 docker-compose 全部 5 个服务；轻量足够 |
| **公网带宽** | 5 Mbps 按固定带宽 | ¥120 | 仅 OIDC 跳转 + 静态资源；SSE 走专线回客户 |
| **域名 + ICP**（可选） | `catfish.客户.com` | 一次性 ¥55/年 | 走 nginx 反代 + Let's Encrypt 免费证书 |
| **RDS PostgreSQL**（可选替代本地 PG） | 通用型 2c4g, 100 GB SSD | ¥400 | 客户合规要求托管 DB 时用；不然 docker pg 即可 |
| **OSS**（可选） | 标准存储 100 GB | ¥12 | audit log / 跨员工 skill artifact 长期归档 |
| **专线/云联网到客户机房** | 100 Mbps IPsec 或物理专线 | ¥800–3,000（视运营商） | **关键项**，下文 2.4 详述 |
| **小计（不含专线）** | | **¥870–1,000/月** | |

> 50 人规模 ECS 完全用不满；后期到 200 人也只需要升 8 vCPU / 16 GB（¥800/月量级）。

### 2.3 容器服务清单

| 容器 | 镜像 | 端口 | RAM 占用峰值 | 磁盘 | 说明 |
|---|---|---|---|---|---|
| nginx | `nginx:1.27-alpine` | 80, 443 | 50 MB | < 1 GB | SSL 卸载 + 路由 |
| catfish-gateway | `catfish-gateway:0.1.x` | 8999 | 800 MB | 5 GB（audit jsonl + quota.db） | 主入口，包含 LiteLLM SDK |
| catfish-identity | `catfish-identity:0.1.x` | 8998 | 300 MB | < 100 MB | OIDC 颁发 + refresh_token store |
| skills-hub | `catfish-skills-hub:0.1.x` | 8997 | 200 MB | 20 GB（skill 包） | 跨员工 skill 分发 |
| postgres | `postgres:16-alpine` | 5432 | 1 GB | 50 GB（sessions + facts + quota）| 主数据库 |
| **总计** | | | **≈ 2.4 GB** | **≈ 76 GB** | 4c8g 100GB ECS 富余 |

### 2.4 公有云 ECS → 客户内网 GPU 的网络互通（关键设计点）

**必须打通**：catfish-gateway 调 DeepSeek inference endpoint。

| 方案 | 月费量级 | 时延 | 适用 |
|---|---|---|---|
| **A1 · IPsec VPN over Internet** | ¥0（仅带宽费） | 20-50ms，抖动较大 | PoC / 预算紧；适合 Flash，对 Pro 不友好 |
| **A2 · 云厂商「云联网/CloudConnect」** | ¥800-1,500 | 10-20ms，稳定 | **推荐**首选 |
| **A3 · 运营商物理专线**（MPLS / SD-WAN） | ¥3,000-8,000 | <10ms，最稳 | 客户已有专线复用 |
| **A4 · 反向代理 + 客户 DMZ 暴露 HTTPS** | ¥0 | 互联网级 | 不推荐，安全审计难过 |

> 公有云 ECS → 客户机房单次调用平均 prompt 6KB + completion 1.2KB ≈ 8KB；20 并发 × 8KB / 5s = 32 KB/s 平均。**带宽不是瓶颈，时延和稳定性是**。

### 2.5 员工端（Companion 客户端）

- macOS 10.15+ / Windows 10+ / Ubuntu 20.04+
- 单机内存占用 ≈ 350-500 MB
- 启动后建立到 `https://catfish.客户.com/v1/chat/completions` 的 SSE 长连接
- 通过 `~/.catfish/companion.yaml` 配 OIDC issuer

---

## 3 · 方案 B · 全内网私部

### 3.1 拓扑图

```
                ┌─────────────────────────────────────────────┐
                │  客户机房 (统一 VLAN)                          │
                │                                             │
   员工电脑──▶│  Server-1 (catfish 中央)                    │
   Companion   │   ├─ nginx (443)                            │
   (50 台,     │   ├─ catfish-gateway (8999)                 │
    内网/VPN)  │   ├─ catfish-identity (8998)                │
                │   ├─ skills-hub (8997)                      │
                │   └─ postgres (5432)                        │
                │                                             │
                │  Server-2 (GPU 推理)                         │
                │   ├─ DeepSeek V4 Flash (vLLM)               │
                │   └─ DeepSeek V4 Pro (vLLM/SGLang)          │
                │                                             │
                │  客户 IdP (LDAP / OAuth / 钉钉)             │
                └─────────────────────────────────────────────┘
```

### 3.2 中央服务硬件清单

| 资源 | 规格建议 | 备注 |
|---|---|---|
| **Server-1** 物理机或 VM | Intel Xeon 8 vCPU / 16 GB RAM / 200 GB SSD | 装 catfish docker-compose 全栈 |
| 操作系统 | Ubuntu Server 22.04 LTS / 麒麟 V10 / 统信 UOS（信创） | Docker 24+ + docker-compose v2 |
| 网络 | 千兆内网网卡，分配静态 IP | 同 GPU 节点同网段 |
| 高可用（可选） | 双机热备（keepalived + VIP） | 50 人规模通常单机足够 |

### 3.3 容器服务清单

跟方案 A 完全一致（见 § 2.3），唯一差别：

- **postgres** 强烈推荐独立机器或客户已有 DBA 托管的 PG 实例
- **skills-hub** 磁盘扩展到 100 GB（私部场景客户 skill 增长更快）

### 3.4 与客户基础设施对接

| 对接点 | 客户需提供 | catfish 团队负责 |
|---|---|---|
| **OIDC SSO** | discovery URL + client_id + secret 或 LDAP 域控信息 | identity-server 适配（一次性） |
| **邮件出口**（员工提醒）| SMTP relay (587/465) | gateway / hermes-fork 直接 SMTP 调 |
| **DNS** | 内网解析 `catfish.客户.internal` → Server-1 | nginx server_name 配置 |
| **HTTPS 证书** | 内部 CA 颁发 或 自签 | 部到 `./certs/fullchain.pem`+ `privkey.pem` |
| **日志归集** | 客户 SIEM/ELK 收 jsonl 路径 | catfish 写 `/var/log/catfish/audit.jsonl` |
| **备份** | 客户备份系统挂载 PG 卷 | `pg_dump` cron 模板 |

---

## 4 · DeepSeek V4 Flash 推理部署（284B 总 / 13B 激活, FP4+FP8 mixed）

### 4.1 模型基本属性（官方权威数据）

| 属性 | 值 | 来源 |
|---|---|---|
| 总参数量 | **284B**（MoE） | DeepSeek 官方 model card |
| 激活参数量 | **13B**（每 token 路由到的 expert 子集） | 官方 |
| Context window | **1M token** | 官方 |
| 训练精度 | FP8 Mixed | 官方 |
| 推理推荐精度 | **FP4 + FP8 Mixed** | 官方权重提供两个版本 |
| 支持工具调用 | ✓ | 官方 |
| 支持视觉 | ✗（纯文本） | 官方 |

### 4.2 内存预算（关键 — 5/25 v1.2 H20 主推重算）

> **2026-05-25 v1.2 更新**: 拉 HF [`deepseek-ai/DeepSeek-V4-Flash`](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash) + 论文"V4 Hybrid Attention (CSA+HCA) 让 KV cache 仅是 V3.2 的 10%" + 鸿波确认 **客户实际部署 H20** (国产合规版, 不支持 FP4).

| 项 | 计算 | 占用 |
|---|---|---|
| **★ 权重（FP8 fallback, H20/H800/A100 主用）** | 284B × 1 byte | **≈ 284 GB** |
| 权重（FP4+FP8 mixed, 需 H100/H200/B 系列原生 FP4, HF 158B params） | mixed precision | ≈ 158–180 GB |
| 权重（纯 FP4, 需 B100/B200） | 284B × 0.5 byte | ≈ 142 GB |
| 框架开销（activation buffer + workspace） | +20% | +35 GB |
| **KV cache 关键优化（V4 vs V3）**: V4 Hybrid Attention (CSA+HCA) → 仅 V3 的 10% | (V3 ~80 KB/token → V4 ~8 KB/token) | — |
| KV cache（15 并发，平均 16K context, V4 优化后） | 15 × 16K × 8 KB | **≈ 2 GB** |
| KV cache（1M context 极端单请求, V4 优化后） | 1M × 8 KB | **≈ 8 GB** |
| **★ H20 最低 VRAM 总量**（FP8 + 50 人并发 15） | 284 + 35 + 2 | **≈ 321 GB** |
| **★ H20 舒适 VRAM 总量**（含 1M 长上下文余量 × 2 并发） | 284 + 35 + 16 + 60 buf | **≈ 395 GB** |
| H100 最低 VRAM（FP4+FP8 mixed, alternative） | 178 + 35 + 2 | ≈ 215 GB |
| H100 舒适 VRAM | 178 + 35 + 16 + 60 | ≈ 290 GB |

### 4.3 GPU 硬件清单（★ H20 主推 — 客户实际部署卡型）

> **为什么是 H20 不是 H100**:
> - H20 是国产 export-compliant 卡 (96GB HBM3, 跟 H800 一档), 国内供货稳, 客户内网现成就是这卡
> - H20 **不支持原生 FP4** → V4 Flash 必走 FP8 fallback (`DeepSeek-V4-Flash-Base`), weights **284 GB**
> - H20 compute ~15% of H100 (296 vs 1979 TFLOPS FP8), 但 memory bandwidth **4.0 TB/s** (H100 是 3.35 TB/s) **反超**
> - MoE 推理 active 13B params 主要 memory-bound, bandwidth 反超让 TTFT 仅慢 H100 1.5-2x, 不是 7x compute 差距

#### ★ H20 配置档 (主推)

| 档位 | GPU 配置 | 总 VRAM | weights+fw+KV | 余量 | 最大并发 | TTFT | 月度电费 |
|---|---|---|---|---|---|---|---|
| **❌ 不可行** | 2× H20 | 192 GB | 284+35 = 319 GB | **-127 GB** | — | weights 装不下 | — |
| **❌ 不推荐** | 3× H20 | 288 GB | 319 GB | **-31 GB** | — | 紧张, 框架挤死 | — |
| **入门 / PoC** | **4× H20 96G** | 384 GB | 321 GB | **+63 GB** | 5–10 (16K avg) | 1.5–2.0s | ¥6,000 |
| **★ 推荐 / 50 人 / 并发 15** | **8× H20 单机** | **768 GB** | 321 GB | **+447 GB** | **25–40** | 1.0–1.5s | ¥12,000 |
| **长 context heavy** (1M 多并发) | 8× H20 × 2 台 (16 卡) | 1536 GB | 321 GB | +1215 GB | 60+ / 8–10 并发 1M | 0.8–1.2s | ¥24,000 |

> ⚠️ **8 卡 H20 而不是 4 卡的真原因**:
> - weights 284 GB + framework 35 GB = **319 GB**, 4 卡 H20 (384 GB) 只剩 63 GB 给 KV cache
> - V4 优化后 15 并发 16K 只需 2 GB KV, 但**长 context 撑不住** — 1 个 1M context 就要 8 GB KV, 5 并发 1M = 40 GB 接近 4 卡上限
> - **8 卡 H20 (768 GB) 给 447 GB KV 余量**, 1M 多并发不慌, 才是 50 人长期部署该上的配置
> - 4 卡 H20 仅适合 PoC / 入门 (员工就 5-10 个先试) 或员工只跑短 context (8-32K) 场景

#### 其他卡型 (alternative — 客户没 H20 / POC 借设备时用)

| 档位 | GPU 配置 | 总 VRAM | FP4 原生 | 最大并发 | TTFT |
|---|---|---|---|---|---|
| H100 推荐 (FP4+FP8 mixed) | 4× H100 80G | 320 GB | ✓ | 15–25 | 0.8s |
| H100 长 context | 8× H100 80G | 640 GB | ✓ | 30–50 | 0.6s |
| H800 (FP8 fallback) | 8× H800 80G | 640 GB | ✗ | 18–25 | 1.5s |
| H200 (新一代) | 4× H200 141G | 564 GB | ✓ | 30–50 | 0.5s |
| A100 (FP8 fallback, 384 GB 紧巴, 推荐 8 卡) | 8× A100 80G | 640 GB | ✗ | 12–18 | 1.8s |
| B100/B200 (新一代旗舰, 客户基本买不到) | 4× B100/B200 | 768+ GB | ✓ | 50+ | 0.4s |

> ⚠️ **A100 / H800 / H20 / 国产 910C 不支持原生 FP4** → 跑 V4 Flash 走 FP8 fallback (`DeepSeek-V4-Flash-Base`), 显存翻倍 + TTFT 慢 1.5-2x.
>
> H100 / H200 / B100 / B200 才有原生 FP4 Tensor Core. 但客户内网现有 H20, 不用 H100.
>
> **V4 KV cache 仅 V3.2 的 10%** 这优化是模型层 (Hybrid Attention CSA+HCA), 跟卡无关, H20 部署同样受益. 老 V3 部署经验估的 KV 大 10x, V4 用同样硬件能撑 5-10x 更多并发.
>
> 「最大并发」按平均 prompt 6K + completion 1K 估, 1M context 显著降低 (8× H20 撑 5-8 个 1M 并发, 16× H20 撑 8-10 个).

### 4.4 推理软件栈

| 组件 | 版本 | 说明 |
|---|---|---|
| **vLLM** | 0.7.0+ | 对 DeepSeek V4 FP4+FP8 mixed 的支持需要新版 |
| 备选 SGLang | 0.4.3+ | 长上下文（>100K）场景吞吐更高 |
| CUDA | **12.4+**（H100/H200）/ **12.6+**（B100/B200 FP4） | 跟 GPU 驱动匹配 |
| NCCL | 2.22+ | 多卡 tensor parallel + expert parallel |
| Python | 3.11+ | |
| OpenAI-compatible server | vLLM 自带 / SGLang 自带 | catfish-gateway 通过 `INTERNAL_LLM_BASE_DEEPSEEK_V4_FLASH=http://10.x.x.x:port/v1` 调 |

### 4.5 启动命令模板（vLLM，★ 8× H20 推荐档 ★）

```bash
# ★ 客户内网 H20 主推: FP8 fallback (`-Base`), tensor parallel 8 卡
# 5/25 v1.2 修: H20 不支持原生 FP4, 必走 FP8 Base 版本
# weights 284 GB + framework 35 + KV ~10-30 GB = 充裕在 768 GB 总 VRAM 内
python -m vllm.entrypoints.openai.api_server \
  --model deepseek-ai/DeepSeek-V4-Flash-Base \
  --tensor-parallel-size 8 \
  --enable-expert-parallel \
  --kv-cache-dtype fp8 \
  --max-model-len 1048576 \
  --max-num-seqs 32 \
  --gpu-memory-utilization 0.92 \
  --api-key $INTERNAL_LLM_KEY \
  --port 8000
```

> **vLLM 版本要求**: H20 上跑 V4 Flash-Base FP8 需要 vLLM **0.7.0+** (老版 0.6.x 对 DeepSeek V4 MoE expert parallel 支持有问题). 装前先 `pip install -U vllm`.

H100/H200 (有 FP4) alternative — 客户没 H20 借设备时用:

```bash
# FP4+FP8 mixed, 需要 H100/H200/B-系列原生 FP4
# 4 卡足够 50 人 / 并发 15 (因为 weights 仅 178 GB vs H20 的 284 GB)
python -m vllm.entrypoints.openai.api_server \
  --model deepseek-ai/DeepSeek-V4-Flash \
  --tensor-parallel-size 4 \
  --enable-expert-parallel \
  --quantization fp4_fp8_mixed \
  --kv-cache-dtype fp8 \
  --max-model-len 1048576 \
  --max-num-seqs 24 \
  --gpu-memory-utilization 0.92 \
  --api-key $INTERNAL_LLM_KEY \
  --port 8000
```

老 H800/A100 4 卡兜底（FP8 only, weights ~284 GB 紧巴, 推荐 8 卡）：

```bash
# H800/H20 不支持原生 FP4 → 走 Base (FP8 mixed) 版本
# 注意: weights ~284 GB → 4 卡 (320 GB) 紧巴巴, 推荐 8 卡
python -m vllm.entrypoints.openai.api_server \
  --model deepseek-ai/DeepSeek-V4-Flash-Base \
  --tensor-parallel-size 8 \
  --enable-expert-parallel \
  --max-model-len 524288 \
  --max-num-seqs 16 \
  --gpu-memory-utilization 0.95 \
  --api-key $INTERNAL_LLM_KEY \
  --port 8000
```

### 4.6 catfish 端配置

```yaml
- name: catfish-public-deepseek-flash
  display_name: "DeepSeek V4 Flash · 内网私部（284B/13B active）"
  upstream:
    model: openai/deepseek-v4-flash
    api_base: ${INTERNAL_LLM_BASE_DEEPSEEK_V4_FLASH}  # → http://10.10.40.x:8000/v1
    api_key_env: INTERNAL_LLM_KEY
    timeout: 120  # 1M context 可能稍慢
  context_window: 1048576    # 1M, V4 真实支持
  # 员工承诺先收到 256K，避免预期太高；后续看实测调整
  # max_output_tokens 等真撞 vLLM 上限再补
  supports_tool_use: true
  supports_vision: false
  cost_tier: free  # 私部按机器成本
```

### 4.7 V4 几个 unique 特性 (5/25 鸿波 review HF 后整理)

| 特性 | 详情 | 部署影响 |
|---|---|---|
| **Hybrid Attention (CSA + HCA)** | 论文核心创新: Compressed Sparse Attention + Heavily Compressed Attention | **1M context 单 token 推理 FLOPs = V3.2 的 27%, KV cache = V3.2 的 10%** — 是 GPU 数量降半的根本原因 |
| **3 种 reasoning mode** | `Non-think` / `Think High` / `Think Max` (system prompt 切换) | 跟 catfish-gateway 现有的 `param_overrides` 适配, `Think Max` 推荐 context ≥ 384K |
| **没 Jinja chat template** | 不带 `tokenizer_config.json` 的 chat_template 字段, 改用专用 `encoding/` Python script | **hermes / LiteLLM 需要适配** — 默认 chat template 渲染会失败. 用 vLLM `--chat-template` 显式传 encoding/chat_template.jinja2 (官方 inference 目录有), 或 wait LiteLLM 官方支持 |
| **HF 上目前无 Inference Provider** | "11 ask for provider support", 没有公有云推理服务 (5/25) | **只能自部署**, 没有"先用公网 API 试跑"的选项 — POC 周期长 1 周左右走 vLLM 自部署 |
| **License: MIT** | 跟 V3 一样宽松, 商用 OK | 客户合同里写"用 V4-Flash" 不需要额外授权 |
| **Pre-train 32T tokens** | V3 是 14.8T, 2x+ 训练数据 | 模型质量提升明显, 部署完后 demo 用 GPQA / MMLU-Pro 跑 benchmark 给客户看 |
| **Safetensors 158B params** | HF Model size 158B (mixed precision counting), 不是 284B (那是参数总数) | 磁盘 weights 实际 ~158-180 GB, 比按 FP16 估的 568 GB 小 3.5x |

### 4.8 实际部署前 trigger checklist

- [ ] **vLLM ≥ 0.7.0** (跑 `pip show vllm` 看版本, 老版没 `fp4_fp8_mixed` quant 支持)
- [ ] **CUDA ≥ 12.4** (H100/H200) 或 **CUDA ≥ 12.6** (B100/B200 FP4 原生)
- [ ] **NCCL ≥ 2.22** (4 卡 tensor parallel + expert parallel)
- [ ] **下载 HF weights** `huggingface-cli download deepseek-ai/DeepSeek-V4-Flash --local-dir /models/v4-flash` (~170 GB)
- [ ] **chat_template 处理**: 看 `inference/` 目录 + 把 encoding script 包成 Jinja 给 vLLM (临时方案: 走原始 prompt 字符串, 不要让 vLLM 自动 chat 渲染)
- [ ] **catfish-gateway 拨通**: `INTERNAL_LLM_BASE_DEEPSEEK_V4_FLASH=http://10.x.x.x:8000/v1` 后 `curl /v1/models` 验
- [ ] **真测 1M context**: 上 `MRCR 1M` 基准跑一发, 跑通后才能给客户承诺长 context 能力

---

## 5 · DeepSeek V4 Pro 推理部署（1.6T 总 / 49B 激活, FP4+FP8 mixed）

> ⚠️ **重要**：1.6T 总参数 → 集群级部署门槛。50 人规模私部 Pro 本质上是为了**合规 / 数据不出门**，纯算账远不如公网 API。下文给出可行的最小硬件方案，但**强烈建议先确认客户合规真的要求 Pro 私部**，能用 Flash 顶住的就别强上 Pro。

### 5.1 模型基本属性（官方权威数据）

| 属性 | 值 |
|---|---|
| 总参数量 | **1.6T**（MoE，比 Flash 大 5.6 倍） |
| 激活参数量 | **49B**（每 token 路由，比 Flash 大 3.8 倍） |
| Context window | **1M token** |
| 训练精度 | FP8 Mixed |
| 推理推荐精度 | **FP4 + FP8 Mixed** |
| 支持工具调用 | ✓ |
| 支持视觉 | ✗ |

### 5.2 内存预算（关键 —— 这是部署成本的主要驱动）

| 项 | 计算 | 占用 |
|---|---|---|
| 权重（FP4+FP8 mixed） | 1.6T × 5/8 byte | **≈ 1.0 TB** |
| 权重（纯 FP4） | 1.6T × 0.5 byte | ≈ 800 GB |
| 权重（FP8 fallback，老卡跑） | 1.6T × 1 byte | ≈ 1.6 TB |
| 框架开销 + workspace（49B active） | +15% | +150 GB |
| KV cache（15 并发，平均 16K context） | 15 × 16K × 300 KB | ≈ 75 GB |
| **最低 VRAM 总量**（FP4+FP8 + 常规并发） | | **≈ 1.25 TB** |
| **舒适 VRAM 总量** | | **≈ 1.5 TB** |

> 49B active 的每 token KV cache 约 300 KB（hidden_dim 大、attention head 多）。即便单请求短上下文，权重本身就吃掉 1 TB，**没有 1 TB+ VRAM 集群跑不起来**。

### 5.3 GPU 硬件清单（基于真实 1.6T 权重）

| 档位 | GPU 配置 | 总 VRAM | FP4 原生 | 最大并发 | TTFT | 月度电费 | GPU 一次性 |
|---|---|---|---|---|---|---|---|
| **❌ 不可行** | 8× H100 80G 单机 | 640 GB | ✓ | — | — | — | — |
| **❌ 不可行** | 8× H800 80G 单机 | 640 GB | ✗ | — | — | — | — |
| **入门** · 紧张 | **8× H200 141G** 单机 HGX | 1128 GB | ✓ | 5–10 | 3.5s | ¥22,000 | ¥2.4M-3M |
| **推荐**（50 人 / 并发 15） | **16× H100 80G**（2 节点 NVLink） | 1280 GB | ✓ | 12–20 | 2.5s | ¥36,000 | ¥3.5M-4.5M |
| **推荐 · 新一代** | **16× H200 141G**（2 节点） | 2256 GB | ✓ | 25–40 | 1.8s | ¥44,000 | ¥4M-5M |
| **国产替代** | **24× H800 80G**（3 节点，FP8 only） | 1920 GB | ✗ | 8–15 | 4s | ¥45,000 | ¥4M-5M |
| **Blackwell 首发** | **8× B200 192G** 单机 | 1536 GB | ✓✓（原生 FP4 算力翻倍） | 30+ | 1.2s | ¥30,000 | ¥4.5M-6M（2026 供货紧）|

> ⚠️ **关键约束**：
>
> 1. **必须 ≥1 TB VRAM**，无任何 PCIe 4 卡方案能搞定。最低门槛 8× H200 141G 单节点 或 16× H100 80G 双节点。
> 2. **跨节点必须 InfiniBand**（最低 NDR 400Gb/s 或 HDR 200Gb/s）。普通以太网撑不住 tensor parallel 通信带宽，会让 TTFT 飙到 10s+。
> 3. **H800 跑 Pro 没有 FP4，权重要 1.6 TB，3 节点 24 卡才够**，但实测吞吐反而比 16× H100 差（FP8 慢一半 + 跨节点通信开销翻倍）。除非客户禁 H100，否则不推荐。
> 4. **Blackwell (B100/B200)** 是 V4 Pro 的"亲生"硬件（DeepSeek 也在 B 系列上训的），原生 FP4 算力 4x 提升。2026 上半年国内供货紧张，建议谈采购时锁单。

### 5.4 推理软件栈

跟 Flash 同（vLLM 0.7.0+ / SGLang 0.4.3+ / CUDA 12.4+），但 **必须** 开 expert parallel：

```bash
# 16× H100 80G, 2 节点配置（每节点 8 卡）
# 节点 1: master
python -m vllm.entrypoints.openai.api_server \
  --model deepseek-ai/DeepSeek-V4-Pro \
  --tensor-parallel-size 8 \
  --pipeline-parallel-size 2 \
  --enable-expert-parallel \
  --quantization fp4_fp8_mixed \
  --kv-cache-dtype fp8 \
  --max-model-len 524288 \
  --max-num-seqs 16 \
  --gpu-memory-utilization 0.94 \
  --distributed-executor-backend ray \
  --api-key $INTERNAL_LLM_KEY \
  --port 8001
```

需要先起 Ray cluster 跨节点（详见 vLLM 多节点部署文档）。

### 5.5 catfish 端配置

```yaml
- name: catfish-public-deepseek-pro       # 新增 entry
  display_name: "DeepSeek V4 Pro · 推理增强（内网私部 1.6T/49B active）"
  upstream:
    model: openai/deepseek-v4-pro
    api_base: ${INTERNAL_LLM_BASE_DEEPSEEK_V4_PRO}  # → http://10.10.40.y:8001/v1
    api_key_env: INTERNAL_LLM_KEY
    timeout: 300              # Pro 长输出可能 4-5 分钟
  context_window: 1048576    # 1M, V4 真实支持
  # max_output_tokens 等真撞上限再补
  supports_tool_use: true
  supports_vision: false
  recommended_for: [reasoning, complex_reasoning, code, long_context]
  cost_tier: paid       # UI 提示员工"贵 + 慢"，鼓励默认用 Flash
  fallback:
    on_errors: [429, 500, 502, 503, 504, "timeout"]
    chain: [catfish-public-deepseek-flash]   # Pro 挂了自动落 Flash
    max_hops: 1
```

### 5.6 50 人规模 Pro 私部的现实建议（直言）

**鸿波 / 客户决策树**：

1. **客户只是想"以后能上 Pro"** → 现在只部 Flash，给 Pro 一个 yaml 注释占位，等客户真有 5+ 个推理重度用户再上。
2. **客户必须 Pro 私部（金融 / 央国企 / 涉密强合规）** → 16× H100 推荐档，¥3.5M-4.5M 一次性 + ¥36k/月运行。
3. **客户可以接受混合**（重度推理走公网 API + 日常走内网 Flash）→ 内网只部 Flash 8× H100，Pro 走 `api.deepseek.com` 公网；catfish 已有 fallback 链支持。
4. **客户暂时无 Pro 需求** → 完全跳过本章，省 ¥4M。

---

## 6 · 容量与成本对照（50 人 / 月度 152M token）

### 6.1 GPU 自部署成本（一次性 + 月度）

| 选项 | GPU 一次性投入 | 月度电费 | 月度运维（折旧 3 年 + 机房）| 月度总成本 | 折合 ¥/M token |
|---|---|---|---|---|---|
| **仅 Flash · 推荐**（8× H100 80G 单机）| ¥1.8M-2.4M | ¥18,000 | ¥60,000 | ¥78,000 | ≈ ¥510 |
| **仅 Flash · 国产替代**（8× H800 80G，FP8 fallback）| ¥1.6M-2.0M | ¥15,000 | ¥55,000 | ¥70,000 | ≈ ¥460 |
| **仅 Flash · 新代**（4× H200 141G）| ¥1.4M-1.8M | ¥12,000 | ¥50,000 | ¥62,000 | ≈ ¥410 |
| **Flash + Pro 双栈**（8× H100 Flash + 16× H100 Pro）| ¥5.3M-6.9M | ¥54,000 | ¥180,000 | ¥234,000 | ≈ ¥1,540 |
| **Pro 大集群**（16× H200 双节点） | ¥4M-5M | ¥44,000 | ¥150,000 | ¥194,000 | ≈ ¥1,275 |

> 月度运维 = 一次性投入 / 36（3 年折旧）+ 机房电费 + 网络费 + 运维人力分摊。50 人规模实际机房和人力是固定成本，可摊到更多用户后单价骤降。

### 6.2 与公网 API 对比

| 模型 | 公网 API 价（DeepSeek 官方现价）| 50 人 / 月度费用 |
|---|---|---|
| V4 Flash | 输入 ¥1/M + 输出 ¥2/M | 132 × 1 + 19.8 × 2 = **¥172/月** |
| V4 Pro（假设官方定价类似旧 deepseek-reasoner）| 输入 ¥4/M + 输出 ¥16/M | 132 × 4 + 19.8 × 16 = **¥845/月** |
| **两者全用公网** | | **≈ ¥1,000/月** |

### 6.3 平账分析（多少人才让私部划算？）

| 模型 | 私部月成本 | 公网单 token 价 | 私部平账人数 |
|---|---|---|---|
| V4 Flash（H100 单机） | ¥78k/月 | ¥1-2/M token | **≈ 2,000 人**（按当前 152M token/50 人推算）|
| V4 Pro（H100 16 卡）| ¥234k/月 | ¥4-16/M token | **≈ 800 人** |

> **结论**：50 人规模下，纯算账 **公网 API 便宜约 50-200 倍**。私部的真实价值在：
>
> 1. **数据不出门** — 客户内部 prompt / 文档 / 工作上下文不进任何外部公司
> 2. **合规** — 央国企 / 金融 / 政务的「不得使用境外 / 公网 LLM」红线
> 3. **稳定性** — 不受 DeepSeek 公司服务挂掉影响
> 4. **可控成本** — 重度用户场景，token 消耗失控时私部固定成本反而可预测
>
> 如果客户**没有以上 4 条任意一条**，强烈建议先走公网 API + catfish-gateway 当中央，跑半年攒数据再决定是否私部。

---

## 7 · 部署 checklist

### 7.1 部署前（客户 IT 准备）

- [ ] 确认部署方案（A 公有云 / B 全内网）
- [ ] 准备域名（`catfish.客户.com` 或 `.internal`）+ ICP 备案（公网必需）
- [ ] 准备 SSL 证书（公网用 Let's Encrypt / 内网用客户 CA）
- [ ] 申请 OIDC client（客户 IdP 处）：拿到 issuer / client_id / secret / discovery URL
- [ ] 准备 SMTP 邮件出口（员工提醒功能用）
- [ ] GPU 节点装好 NVIDIA driver + CUDA 12.4+ + Docker NVIDIA runtime
- [ ] DeepSeek V4 Flash / Pro 模型权重下载到位（HuggingFace 镜像或客户内网 minio）
- [ ] 方案 A：开通公有云 ECS + 配好云联网 / VPN 到客户机房

### 7.2 中央服务部署（catfish 团队 ~半天）

```bash
git clone <内网 git>/catfish.git
cd catfish/central
cp .env.production.example .env
# 编辑 .env: 填 OIDC / DB 密码 / LLM endpoint / SMTP
docker compose up -d
docker compose logs -f gateway   # 看启动是否健康
curl http://localhost:8999/healthz
```

### 7.3 GPU 推理节点部署（客户 IT + catfish 团队 ~1 天）

```bash
# Flash
docker run -d --gpus all --name deepseek-v4-flash \
  -p 8000:8000 -v /models:/models \
  vllm/vllm-openai:0.6.5 \
  --model /models/DeepSeek-V4-Flash \
  --tensor-parallel-size 4 \
  --max-model-len 131072 \
  --api-key $INTERNAL_LLM_KEY

# Pro
docker run -d --gpus all --name deepseek-v4-pro \
  -p 8001:8001 -v /models:/models \
  vllm/vllm-openai:0.6.5 \
  --model /models/DeepSeek-V4-Pro \
  --tensor-parallel-size 8 \
  --enable-expert-parallel \
  --quantization fp8 \
  --kv-cache-dtype fp8 \
  --port 8001 \
  --api-key $INTERNAL_LLM_KEY
```

### 7.4 客户端分发

- [ ] Companion macOS .dmg / Windows .msi 准备好
- [ ] 内置 `companion.yaml` 默认指向 `https://catfish.客户.com` + OIDC discovery
- [ ] 内部 wiki 教员工：装、登、问"鲶鱼"什么意思（产品介绍 5 分钟视频）

### 7.5 验收

- [ ] 5 名种子员工试用 1 周，记录痛点 + 反馈
- [ ] 监控验证：Grafana 拉到 gateway 的 metrics
- [ ] 安全验证：渗透测试报告（OIDC token 不泄漏、audit log 完整）

---

## 8 · 安全与合规

| 项 | 措施 |
|---|---|
| **传输加密** | 全链路 TLS 1.2+，nginx 强制 https，证书 90 天自动续期 |
| **身份认证** | OIDC + refresh_token rotation（30 天）；不允许 dev_token 进生产 |
| **审计日志** | 每次 LLM 调用写 `/var/log/catfish/audit.jsonl`：user / model / prompt_tokens / completion_tokens / latency / status |
| **配额** | `config/quotas.yaml` 按部门 / 个人天/月限额，防滥用 |
| **数据驻留** | 方案 B 全程不出门；方案 A 客户合规可要求 prompt 走专线，仅元数据走公网 |
| **Secret 管理** | API key 在 `.env` + chmod 600；可对接 客户 Vault / KMS |
| **RBAC** | sysadmin / admin / manager / employee 四级，写在 catfish-identity 的 `dev_users.yaml` 或同步自 LDAP |
| **依赖更新** | docker compose pull 每月一次；安全漏洞 24h 内 patch |

---

## 9 · 监控、备份、告警

### 9.1 监控指标（gateway 已自带 `/metrics` Prometheus endpoint）

| 指标 | 告警阈值 | 处置 |
|---|---|---|
| `catfish_llm_request_total{status="error"}` 比例 | > 5% 持续 5 分钟 | 检查上游 LLM 健康 |
| `catfish_llm_latency_p95` Flash | > 10s | GPU 节点是否过载 |
| `catfish_llm_latency_p95` Pro | > 30s | 同上 + 检查 batch size |
| `process_resident_memory_bytes` | > 6 GB | 重启 gateway 容器 |
| postgres connection count | > 50 | 调 max_connections |
| 磁盘剩余 | < 20 GB | 清理 audit log 或扩容 |

### 9.2 备份

| 项 | 策略 |
|---|---|
| postgres | 每日凌晨 `pg_dump` → 客户备份系统，保留 30 天 |
| `gateway_data` 卷（quota.db / audit.jsonl） | 每日 rsync 到备份目录，保留 90 天 |
| `hubdata` 卷（skills） | 每日快照，保留 14 天 |
| **跨地域容灾**（方案 A） | 公有云快照 → 跨区复制 |

### 9.3 告警通道

- 钉钉 / 飞书 webhook：catfish-gateway 容器 stop / restart loop
- 邮件：每日 quota 用量 + LLM 错误率周报

---

## 10 · 弹性扩容路径

| 触发条件 | 升级动作 |
|---|---|
| 用户从 50 → 150 | ECS 升 8 vCPU / 16 GB，无需停机 |
| 并发从 15 → 40 | Flash 节点：8× H100 → 16× H100，或单升 H200 141G 节省卡数 |
| 出现 Pro 重度用户 > 5 人 | 上 Pro 私部：16× H100 双节点 或 8× H200 单机；先评估走公网 fallback 是否更划算 |
| 跨地域分支机构接入 | 加 nginx 反代节点 + DNS 智能解析 |
| 引入 Embedding / RAG | 加一台 GPU 跑 BGE-M3，gateway models.yaml 加 entry |
| 需要 100% HA | 中央服务双机 keepalived；postgres 主从复制 |

---

## 11 · 已知风险与开放项

| 风险 | 缓解 |
|---|---|
| 公有云 → 客户机房专线抖动时 Companion 体验掉 | 配 nginx 上游 retry + Companion 已有重试 |
| GPU 节点宕机无 fallback（私部场景）| Flash + Pro 互为 fallback；或临时切公网 DeepSeek API（需客户授权）|
| OIDC refresh_token 30 天后强制重登 | 已实现 silent refresh；要求员工 30 天至少 SSO 一次（合理） |
| audit log 写满磁盘 | logrotate + S3/OSS 归档 cron |
| DeepSeek V4 模型迭代版本号变化 | catalog 加 `version: 2026-05`，CHANGELOG 记录每次升级 |

---

## 附录 A · 当前已落地组件版本

| 组件 | 版本 | 仓库路径 |
|---|---|---|
| catfish-gateway | 0.1.x | `central/llm-gateway/` |
| catfish-identity | 0.1.x | `central/identity-server/` |
| skills-hub | 0.1.x | `central/skills-hub/` |
| Companion (Tauri) | 0.1.x | `edge/companion-app/` |
| hermes-fork (员工 agent runtime) | 0.13.x | `edge/hermes-fork/` |
| nginx | 1.27 | docker hub |
| postgres | 16-alpine | docker hub |
| vLLM (推荐) | 0.6.5+ | pip / 客户内网 mirror |

## 附录 B · 关键 .env 模板（合并版）

```bash
# ── catfish 中央 ──
HOST=0.0.0.0
PORT=8999
CATFISH_ENV=prod
PG_USER=catfish
PG_PASSWORD=<强随机>
PG_DB=catfish
CATFISH_DB_URL=postgresql://catfish:<密码>@postgres:5432/catfish

# ── OIDC ──
CATFISH_OIDC_ISSUER=https://sso.客户.com
CATFISH_OIDC_AUDIENCE=catfish-gateway
CATFISH_OIDC_CLIENT_ID=catfish-prod
CATFISH_OIDC_CLIENT_SECRET=<客户 IdP 给>

# ── DeepSeek 内网私部 ──
INTERNAL_LLM_KEY=<vLLM 启动时 --api-key 同值>
INTERNAL_LLM_BASE_DEEPSEEK_V4_FLASH=http://10.10.40.x:8000/v1
INTERNAL_LLM_BASE_DEEPSEEK_V4_PRO=http://10.10.40.y:8001/v1

# ── 上游公网（仅方案 A 备用 / 不配则禁用）──
# GEMINI_API_KEY=
# DASHSCOPE_API_KEY=
# GROQ_API_KEY=

# ── audit ──
CATFISH_AUDIT_PATH=/var/log/catfish/audit.jsonl
```

---

## 附 · 文档 changelog

### v1.2 — 2026-05-25 凌晨 (鸿波二次修正: "客户用 H20 不是 H100")

**触发**: 鸿波 review v1.1 后立刻提醒 "不是用 H20 吗?" — LLM 第一次按通用 H100 估的, 没区分客户实际部署 H20 (国产合规版, 不支持原生 FP4) 跟 alternative H100 (有 FP4) 的差异.

**关键差异**:
- H20 不支持 FP4 → 必走 FP8 fallback (`DeepSeek-V4-Flash-Base`), weights **284 GB** (不是 H100 mixed 178 GB)
- 4× H20 (384 GB) 装下但余量 63 GB 太紧, 长 context 撑不住 → **必须 8× H20 (768 GB)**
- H20 compute 比 H100 慢 7x 但 memory bandwidth 4.0 TB/s 反超 (H100 3.35) → MoE 推理 memory-bound, TTFT 仅慢 1.5-2x

**改动**:
- 头部修正摘要从 v1.1 (按 H100 算) 改 v1.2 (强调 H20 主推)
- §4.2 内存预算: 把 H20 FP8 (284 GB) 作为 ★ 主推数, H100 mixed (178 GB) 作 alternative
- §4.3 GPU 表: 完全重写, "★ 推荐 50 人 / 并发 15" = **8× H20 (768 GB)** 不是 4× H100. H100/H200 alternative 表降到下方
- §4.5 启动命令: 默认走 H20 (FP8 Base 版本 + 8 卡 tensor parallel), H100 (FP4+FP8 mixed) 作 alternative

**为啥 H20 8 卡比 H100 4 卡更贵但更合适**:
| 配置 | 总 VRAM | weights | 余量 | 投资 | 适配性 |
|---|---|---|---|---|---|
| H100 4 卡 (FP4+FP8 mixed) | 320 GB | 178 GB | 142 GB | ~¥80 万 | ✓ 但 H20 客户买不到/不在内网 |
| **H20 8 卡 (FP8)** | **768 GB** | **284 GB** | **484 GB** | **~¥60 万** | ✓ 客户实际部署, 余量更大 |

**H20 反而更便宜的原因**: H20 单卡价 ~7-8 万 (国产合规版), H100 单卡价 ~20 万 (合规渠道). 8× H20 ~¥60 万 < 4× H100 ~¥80 万. 加上 H20 内网现有不用进口, 部署周期 2 周搞定; H100 走合规渠道 2-3 个月.

### v1.1 — 2026-05-25 (鸿波 review HF 后修正)

**触发**: 鸿波直接拉 HF [`deepseek-ai/DeepSeek-V4-Flash`](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash) model card, 让 LLM 重新核对 GPU 数量推算.

**关键发现**:
1. **V4 KV cache = V3.2 的 10%** (Hybrid Attention CSA+HCA 论文核心创新) — 5/23 v1.0 doc 没引用论文这一句, 按 V3 经验估的 KV ~80 KB/token 偏大 10x
2. HF Safetensors 实测 158B params (mixed precision counting), 跟 v1.0 估的"FP4+FP8 mixed ~178 GB" 接近, weights 估算大方向正确
3. HF 上**无任何公有云 Inference Provider 上线** (5/25), 51 人在 issue 求支持 — POC 必走自部署, 公网 API 试跑这条路不通

**改动**:
- §4.2 内存预算: KV cache 计算公式从 80 KB → 8 KB/token (V4 优化), 重算最低/舒适 VRAM
- §4.3 GPU 表: **"推荐 50 人 / 并发 15" 档从 8× H100 改 4× H100** — 省一半 GPU 投资 (~¥100 万 → ~¥50 万)
- §4.5 vLLM 启动命令: `--tensor-parallel-size 8 → 4`, `--max-num-seqs 32 → 24`
- §4.7 新增 V4 unique 特性表 (Hybrid Attention / 3 reasoning modes / 无 Jinja chat template / HF 无 inference provider / 32T pre-train / 158B safetensors)
- §4.8 新增"部署 trigger checklist" (vLLM ≥ 0.7.0 / chat_template 适配 / 真测 1M MRCR)

**未变 (v1.0 估的对的)**:
- weights 178 GB 估算 (HF 真值 158-180 GB 范围内)
- H100 vs H800 vs A100 vs H20 FP4 原生支持差异
- V4 Pro 1.6T 集群级部署门槛 (§5)
- 公有云 ECS GPU 配置 (方案 A)

**仍需真测确认**:
- vLLM 0.7+ `--quantization fp4_fp8_mixed` 真实显存占用 (这里估算 35 GB framework, 真测可能 ±10 GB)
- 4 卡 H100 50 人 / 并发 15 32K context 平均场景的真 TTFT 和 throughput
- 1M context single-request 真用时 GPU 满载情况 (论文说 KV 8 GB, 但 1M context inference 本身就是 5-30 秒级)

---

*文档归属：catfish 平台团队 · 版本 1.2 · 2026-05-25 (H20 主推修正)*
