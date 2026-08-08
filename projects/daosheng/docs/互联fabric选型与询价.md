# GPU 互联 fabric —— 选型、台数与 2026 年 8 月实价

> 8/6 鸿波：「infiniban 的交换机要几台，去查下 2026 年的价格」
>
> 结论：**交换机 2 台**（不是 1 台，也不用 spine）；fabric 总价 **110–250 万**，
> 原估 150–350 万偏高。另外查到一条更要紧的：**这个场景下国产 RoCEv2 可能比 InfiniBand 更合适。**

---

## 一 · 为什么会有这一项

两台 GPU 服务器的方案里**没有**这一项 —— 两台各跑各的模型、互为备份，机器之间不需要高速通信。

一旦主力模型（2.4T 级）大到单机装不下、必须横跨多台，节点之间就要跑 MoE 的
all-to-all，**互联从「不需要」变成「必需」**。这是六台方案里最容易漏掉的一笔。

---

## 二 · 交换机要几台：2 台

### 端点数

6 台 × 8 卡 = **48 个 GPU 端点**（按每卡一张 400G 网卡的标准配法）。

### 单台交换机的容量

NVIDIA Quantum-2 **QM9700 / QM9790**：1U，32 个 OSFP 物理口 → **64 × 400Gb/s**，
聚合双向吞吐 51.2 Tb/s。

**所以一台就装得下 48 个端点，还余 16 口。**

### 但一台不行

我们刚把六台设计成「三台一组、两组互备」的双实例结构，为的是单机故障不停服。
**如果两个实例都挂在同一台交换机上，交换机就是单点 —— 双实例互备白做。**

### 2 台的配法

| | 服务对象 | 用几口 |
|---|---|---|
| 交换机 A | 实例一（节点 1–3） | 24 |
| 交换机 B | 实例二（节点 4–6） | 24 |

**两个实例本就是互相独立的副本，之间不需要通信** —— 所以：

- 不需要 spine 层
- 不需要交换机之间互联
- 每台只用 24/64 口，余量留给后续扩容

这是这个规模下最省的拓扑。（几百节点的集群才需要 rail-optimized 八台叶交换机 + spine，
那是训练集群的做法，六节点推理集群套上去是浪费。）

---

## 三 · 2026 年 8 月实价

### 查到的硬数据

| 项 | 型号 | 价格 | 来源 |
|---|---|---|---|
| NDR 交换机 | MQM9790-NS2F（64 口 400G，外部管理型） | **26.25 万元** | ZOL 中关村在线挂牌 |
| 400G 网卡 | ConnectX-7 MCX75310AAS-NEAT（单口 OSFP，PCIe 5.0 ×16） | **$1,640–2,220** | Tech-America / FiberMall / SHI / Newegg |

网卡是美行报价，国内渠道含税加价后按 **1.5–2.5 万元/张** 估。
光模块 / DAC 未查到公开报价，按 **0.5–1.5 万元/链路** 估（同机房内 DAC 便宜，跨柜走光模块贵）。

### 两种配法的总价

| 配法 | 交换机 | 网卡 | 链路 | 合计 |
|---|---|---|---|---|
| **精简** 4 卡/节点（24 张） | 2 × 24–28 | 24 × 1.5–2.5 | 24 × 0.5–1.5 | **96–152 万** |
| **标准** 8 卡/节点（48 张） | 2 × 24–28 | 48 × 1.5–2.5 | 48 × 0.5–1.5 | **144–248 万** |

**方案仍取 150–350 万**（8/6 鸿波定，保持原估）。

上面那组是**挂牌价折算**，不含进口环节费用、整包集成、上架布线与调试。
按查到的数直接填进预算会偏乐观，留出这段余量是对的 —— 尤其在这一项
**一份正式报价都还没拿到**的情况下。等询价回来再收窄区间。

推理场景（非训练）对 all-to-all 带宽的要求低于训练，**精简配法很可能够用**，
但要在选型实测时用真实并发去压，不能拍脑袋定。

---

## 四 · ★ 更要紧的一条：先别默认 InfiniBand

查价过程中出来的行业面数据：

- **Broadcom 2026 Q1**：约 **70% 的新建 AI 部署选以太网 fabric**，不是 InfiniBand
- **中国「东数西算」项目中 RoCEv2 正在成为主流**，理由是以太网生态成熟、成本更低、
  **本土供应链支撑好**，与自主可控的目标一致
- 性能差距：InfiniBand 延迟略低、原生无损；**RoCEv2 延迟 2–5 μs，对多数 AI 负载够用**
- 国产可选：华为 CloudEngine 8875（iLossless 智能无损）、新华三 NSS18500-08（盛科国产交换芯片）
- Ultra Ethernet Consortium（AMD / Broadcom / Cisco / HPE / Intel / Meta / Microsoft）
  正把 InfiniBand 的原生能力写进以太网标准

### 对本项目的三点影响

1. **供应链**：InfiniBand 是单一供应商（NVIDIA）。已经买了 6 台 H20，互联再绑同一家，
   集中度过高。
2. **成本**：国产 RoCEv2 交换机与网卡通常低于同代 InfiniBand。
3. **申报口径** ★：渠道中心有政府关系与专项申报一条线，**国产化率在多数专项里是加分项**。
   互联这一层选国产，是花同样的钱多拿一项分。

**性能上，这是 6 节点的推理集群，不是几百节点的训练集群** —— RoCEv2 的
2–5 μs 延迟不构成瓶颈。真正的瓶颈在显存带宽（见「BF16 / FP8 / INT4」那一节的测算）。

### 结论

**两条路线都要询价比选，且要在 GPU 采购前定。** 网卡与交换机是配套的，
InfiniBand 网卡插不进 RoCE 交换机，选错要重买。

---

## 五 · PCIe 插槽够不够，多口网卡有没有用

> 8/6 鸿波：「PCIe 插槽够不够插满 8 张网卡，这个问题怎么可以不确认，
> 是不是有多口的网卡可以用？」—— 原稿把它挂成待办是错的，这是可查的事实，
> 且直接决定 fabric 的配法与金额。已查证，结论如下。

### 5.1 插槽：够，而且 8 张是参考设计里的标准配法

| 机型 | PCIe 扩展槽 | 1:1 GPU:网卡 |
|---|---|---|
| ASUS ESC N8-E11（HGX H100/H200 8-GPU） | 12 个（H100）/ 10+1（H200） | 支持 |
| 浪潮 NF5688G7（元脑 8-GPU） | **最多 12 个 PCIe Gen5 x16**，支持 OCP 3.0 / CX7 / 各类智能网卡 | 支持 |

HGX 8-GPU 整机的参考拓扑本来就是**每卡配一张网卡的 rail-optimized 设计**，
专用的 1 GPU ↔ 1 NIC 拓扑最多支持 8 张网卡。

**所以插满 8 张 400G 不是勉强，是设计内的标准配法。插槽不构成约束。**

### 5.2 多口网卡：有，但在 400G 这一档换不来带宽

ConnectX-7 官方 datasheet 写得很清楚：**「delivers up to 400Gb/s total bandwidth」——
这是整卡总带宽，不是每口。** 该卡可做 1 / 2 / 4 个端口，但**总带宽封顶 400 Gb/s**。

| 形态 | 型号示例 | 端口 | **整卡总带宽** |
|---|---|---|---|
| 单口 400G（OSFP） | MCX75310AAS-NEAT | 1 × 400G | 400 Gb/s |
| 双口 200G（QSFP112） | MCX755106AS-HEAT | 2 × 200G | **400 Gb/s（一样）** |

**物理原因在主机接口，不在网卡。** ConnectX-7 主流形态是 PCIe 5.0 x16：
每 lane 32 GT/s，128b/130b 编码，x16 ≈ 504 Gb/s 理论、实际约 400 Gb/s 可用。
**槽本身就是天花板。**

> ⚠ 查价时有二手资料称「双口 ConnectX-7 在 PCIe 5.0 x16 下可达每口 400G、
> 合计 800 Gb/s」——**与 NVIDIA 自己的 datasheet 矛盾，不成立**。
> 真要 800G 得上 ConnectX-8 + PCIe 6.0。
> （ConnectX-7 另有 x32 lane 的 Socket Direct 形态可突破，但占两个槽，不省槽反而更费。）

### 5.3 所以真正的选择是三行，不是两行

| 配法 | 网卡 | 每节点总带宽 | 全集群网卡数 | 端口冗余 |
|---|---|---|---|---|
| 满配 | 8 × 单口 400G | 3,200 Gb/s | 48 张 | 卡级 |
| 半配 | 4 × 单口 400G | 1,600 Gb/s | 24 张 | 卡级 |
| **半配 · 多口** | **4 × 双口 200G** | **1,600 Gb/s** | **24 张 / 48 端口** | **★ 链路级** |

第三行是多口网卡真正有价值的地方：**带宽和第二行一样，但端口数翻倍，
单个端口或光模块故障不会掉整张卡。** 对我们「双实例互备」的设计，链路级冗余是加分的。

网卡数从 48 降到 24，在 fabric 预算里省约 36–60 万 —— 仍在 150–350 万区间内。

**推理场景（非训练）的 all-to-all 压力远小于训练，半配很可能够。**
但这要在选型实测时用真实并发压出来，不能拍脑袋定。

> 注：双口 200G 要求交换机侧有 200G 口。QM9700 的 64 × 400G 可拆成 128 × 200G，
> 或用 400G 转 2×200G 分支线，不构成障碍。

---

## 六 · 待办

- [ ] 向 NVIDIA 授权代理（中科新远 / 超擎数智 / 捷易科技等）询 QM9700 + ConnectX-7 + 线缆整包价
- [ ] 向华为 / 新华三询同规模 400G RoCEv2 整包价
- [ ] 选型实测阶段用真实并发压 all-to-all，定满配 / 半配（**插槽不是约束，带宽需求才是**）
- [x] ~~确认 H20 服务器的 PCIe 插槽数与拓扑~~ —— 见 5.1，8–12 个 Gen5 x16，够

---

## 来源

- [ZOL 中关村在线 · 迈络思交换机报价](https://detail.zol.com.cn/switches/mellanox/)
- [NVIDIA Quantum-2 QM9700 · 超擎数智](https://chaoqing-i.com/list_99.html)
- [ConnectX-7 MCX75310AAS-NEAT · FS.com](https://www.fs.com/products/212161.html)
- [ConnectX-7 MCX75310AAS-NEAT · FiberMall](https://www.fibermall.com/sale-460593-nvidia-mellanox-mcx75310aas-neat.htm)
- [NVIDIA ConnectX-7 官方 datasheet（"up to 400Gb/s total bandwidth"）](https://www.nvidia.com/content/dam/en-zz/Solutions/networking/infiniband-adapters/infiniband-connectx7-data-sheet.pdf)
- [ASUS ESC N8-E11 HGX 8-GPU 规格（12 PCIe，1:1 GPU:NIC）](https://dlcdnet.asus.com/pub/ASUS/server/ESCN8-E11/Datasheet/DataSheet_ESC_N8-E11_20240829.pdf)
- [浪潮 NF5688G7（最多 12 个 PCIe Gen5 x16）](https://www.ieisystem.com/product/ai/15080.html)
- [GPU 服务器网卡配置与光通信方案 · NADDOD](https://www.naddod.com/blog/quick-understanding-gpu-server-network-card-configuration-in-ai-era)
- [GPU Networking for AI Clusters: InfiniBand vs RoCE vs Spectrum-X (2026) · Spheron](https://www.spheron.network/blog/gpu-networking-infiniband-roce-spectrum-x-guide/)
- [RoCEv2 vs InfiniBand: AI Cluster Networking Compared (2026) · NetPilot](https://www.netpilot.io/blog/ai-cluster-networking-rocev2)
- [华为 CloudEngine 8875 数据中心交换机](https://zhuanlan.zhihu.com/p/696378257)
