# Catfish 项目相关 ArXiv 技术论文调研笔记

调研日期：2026-08-30
调研目标：系统检索 ArXiv 上与 Catfish 核心技术相关的论文，并映射到项目模块、当前实现与可借鉴方向。

## 关键问题

1. Catfish 的核心技术模块分别对应哪些研究方向？
2. 哪些 ArXiv 论文与当前实现直接相关，哪些适合作为下一阶段技术升级依据？
3. 论文中的方法是否适合 Catfish 的员工数据主权、中央/边缘分离、隐私与本地优先约束？

## 项目技术范围（初步）

- LLM Gateway：多模型路由、fallback、模型/供应商抽象、用量审计与配额。
- Agent / Tool Bridge：工具调用、MCP、权限边界、沙箱与可靠执行。
- Companion / Edge：本地优先、个人记忆、知识库、Wiki、邮件/日程等个人数据处理。
- Retrieval：BM25、BGE-M3 embedding、混合检索、向量缓存与知识库语义搜索。
- Agent Memory：跨会话记忆、摘要、相关性筛选、个人知识沉淀。
- Security / Privacy：最小权限、数据零出端、审计、提示注入防护、供应商隔离。
- Multimodal / OCR：视觉模型、文件解析与附件处理。
- Deployment：Docker 交付、离线/内网部署、模型服务接入与可观测性。

## 预期输出

- 按技术方向分组的 ArXiv 论文清单。
- 每篇论文的 ArXiv 链接、年份、核心贡献、与 Catfish 的关联模块和适用优先级。
- 当前项目可直接借鉴的实现建议与待验证问题。

## 发现

### 第一轮：Embedding、RAG 与知识图谱

1. **BGE M3-Embedding: Multi-Lingual, Multi-Functionality, Multi-Granularity Text Embeddings Through Self-Knowledge Distillation**（2024）
   - ArXiv：https://arxiv.org/abs/2402.03216
   - 关联 Catfish：当前 `catfish-private-embed` / 本机 BGE-M3 语义搜索。
   - 要点：支持 100+ 语言、dense / multi-vector / sparse 三种检索形态、最长约 8192 tokens；适合中英文混合的企业知识库。
   - 初步判断：直接相关，属于当前实现的模型依据；但当前代码主要使用单一 dense 向量 + cosine，没有利用 BGE-M3 的 sparse/multi-vector 能力。

2. **Graph Retrieval-Augmented Generation: A Survey**（2024）
   - ArXiv：https://arxiv.org/abs/2408.08921
   - 关联 Catfish：Wiki 的实体、概念、related 关系，以及未来知识库关系检索。
   - 要点：将 GraphRAG 拆为图索引、图引导检索、图增强生成，并讨论结构化关系对上下文检索和可解释性的价值。
   - 初步判断：高相关背景/架构参考；Catfish 已有关系字段，但当前语义搜索仍主要是向量 cosine，尚未做图引导召回。

3. **LightRAG: Simple and Fast Retrieval-Augmented Generation**（2024）
   - ArXiv：https://arxiv.org/abs/2410.05779
   - 关联 Catfish：本地 Wiki 的关系网络、增量索引、低成本检索。
   - 要点：将图结构与向量表示结合，提供低层实体检索和高层概念发现，并强调增量更新。
   - 初步判断：非常适合对比 Catfish 当前“每次语义搜索扫描 Wiki、按 mtime 更新缓存”的实现；可作为后续增量索引和关系召回的参考。

4. **HyperGraphRAG: Retrieval-Augmented Generation with Hypergraph-Structured Knowledge Representation**（2025）
   - ArXiv：https://arxiv.org/abs/2503.21322
   - 关联 Catfish：复杂的多实体关系、部门/项目/人员/制度等 n-ary 关系。
   - 要点：用超边表示一条关系中的多个实体，避免普通二元图拆分后丢失整体语义。
   - 初步判断：中长期研究方向；当前 Wiki 的 `related` 关系还不足以支撑超图，不宜马上引入。

5. **A Survey on the Memory Mechanism of Large Language Model based Agents**（2024）
   - ArXiv：https://arxiv.org/abs/2404.13501
   - 关联 Catfish：个人记忆、跨会话摘要、相关性筛选、Wiki 沉淀。
   - 要点：系统整理 Agent 的短期/长期记忆设计、记忆写入、检索、更新与评估。
   - 初步判断：直接相关的总览论文，适合用来检查 Catfish 的 memory / Wiki 分层是否完整。

6. **From Storage to Experience: A Survey on the Evolution of LLM Agent Memory Mechanisms**（2026）
   - ArXiv：https://arxiv.org/abs/2605.06716
   - 关联 Catfish：从对话轨迹保存到摘要、经验抽象和可持续个人知识。
   - 要点：提出 Storage → Reflection → Experience 三阶段框架，并讨论长期一致性、动态环境和持续学习。
   - 初步判断：对 Catfish 的员工个人记忆路线图很有参考价值，尤其是区分“保存原文”和“抽象经验”。

### 第二轮：Agent 工具调用与安全

7. **ReAct: Synergizing Reasoning and Acting in Language Models**（2023）
   - ArXiv：https://arxiv.org/abs/2210.03629
   - 关联 Catfish：Agent 调用 MCP / Tool Bridge、检索知识库后再行动。
   - 要点：把推理轨迹与外部行动交替进行，让模型通过工具获取外部信息并处理异常。
   - 初步判断：是 Catfish 工具调用链的基础范式；但生产实现必须叠加权限、确认和审计，不能只依赖模型自觉。

8. **InjecAgent: Benchmarking Indirect Prompt Injections in Tool-Integrated Large Language Model Agents**（2024）
   - ArXiv：https://arxiv.org/abs/2403.02691
   - 关联 Catfish：邮件、网页、知识库内容进入 Agent 后触发工具调用的安全边界。
   - 要点：包含 1,054 个测试案例、17 类用户工具和 62 类攻击工具；论文显示 ReAct 风格 GPT-4 Agent 在测试中仍会受到间接提示注入影响。
   - 初步判断：高优先级安全基准参考，尤其适合测试“外部文档/邮件内容 → Tool Bridge → 写入或外发”的链路。

9. **AgentDojo: A Dynamic Environment to Evaluate Prompt Injection Attacks and Defenses for LLM Agents**（2024）
   - ArXiv：https://arxiv.org/abs/2406.13352
   - 关联 Catfish：个人邮箱、银行/网页类工具、动态不可信数据和 Agent 权限控制。
   - 要点：提供 97 个真实任务和 629 个安全测试案例，强调在动态环境中同时评估任务完成率和安全性。
   - 初步判断：适合借鉴测试框架思路，为 Catfish 建立“工具可用性 + 越权/外发安全”的双指标回归集。

10. **The Instruction Hierarchy: Training LLMs to Prioritize Privileged Instructions**（2024）
    - ArXiv：https://arxiv.org/abs/2404.13208
    - 关联 Catfish：系统指令、员工指令、工具返回内容和知识库内容之间的优先级。
    - 要点：训练模型区分不同权限级别的指令，提升对提示注入和冲突指令的鲁棒性。
    - 初步判断：可作为模型层防线参考，但不能替代 Catfish 已有的工具权限、沙箱和人工确认。

11. **Toolformer: Language Models Can Teach Themselves to Use Tools**（2023）
    - ArXiv：https://arxiv.org/abs/2302.04761
    - 关联 Catfish：工具选择、参数生成、日历/邮件/知识库等外部 API 使用。
    - 要点：让模型学习何时调用 API、传什么参数以及如何吸收工具结果。
    - 初步判断：适合作为工具调用策略的理论参考；Catfish 当前更偏“受控工具目录 + 权限边界”，不应直接采用完全自主 API 发现。

12. **FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance**（2023）
    - ArXiv：https://arxiv.org/abs/2305.05176
    - 关联 Catfish：Gateway 的模型 fallback、级联调用、成本与质量权衡。
    - 要点：通过 prompt 适配、模型近似和 LLM cascade，在质量不降的情况下减少调用成本。
    - 初步判断：直接对应 Gateway 的 fallback/多模型编排；值得借鉴“先便宜模型、失败或困难时升级”的策略，但要结合 Catfish 的数据出端策略。

13. **RouteLLM: Learning to Route LLMs with Preference Data**（2024）
    - ArXiv：https://arxiv.org/abs/2406.18665
    - 关联 Catfish：不同 chat 模型之间的动态路由、公共/私有模型选择。
    - 要点：用偏好数据训练轻量 router，在强模型质量和弱模型成本之间动态取舍。
    - 初步判断：中期可研究方向；当前 Catfish 主要使用配置角色和错误 fallback，还没有基于任务难度/偏好的质量路由。

14. **Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks**（2020）
    - ArXiv：https://arxiv.org/abs/2005.11401
    - 关联 Catfish：知识库检索结果注入 Agent/Advisor 上下文。
    - 要点：把参数化语言模型与外部 dense vector index 结合，支持知识更新和来源追踪。
    - 初步判断：RAG 基础论文；Catfish 当前是“先检索 Top-K，再把摘要/片段注入 prompt”的轻量实现。

15. **Dense Passage Retrieval for Open-Domain Question Answering**（2020）
    - ArXiv：https://arxiv.org/abs/2004.04906
    - 关联 Catfish：BGE-M3 query/document 向量化和 dense retrieval。
    - 要点：用双编码器学习 query 与 passage 的稠密表示，并与 BM25 对比。
    - 初步判断：直接支撑 dense 检索的基础参考；也提醒我们需要单独评估中文企业语料，而不能照搬开放域 QA 指标。

16. **BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models**（2021）
    - ArXiv：https://arxiv.org/abs/2104.08663
    - 关联 Catfish：BM25、dense、hybrid 检索效果评估。
    - 要点：跨多个领域和任务评估零样本检索模型，强调不同数据集上的泛化差异。
    - 初步判断：高优先级评估方法参考；Catfish 应建立自己的中文企业知识库 query/相关性标注集，而不是只看“搜到了几个结果”。

17. **Lost in the Middle: How Language Models Use Long Contexts**（2023）
    - ArXiv：https://arxiv.org/abs/2307.03172
    - 关联 Catfish：Advisor 上下文拼接、知识库片段数量、长对话/记忆注入。
    - 要点：相关信息放在长上下文中间时，模型利用率可能显著下降；首尾位置通常更好。
    - 初步判断：直接支持 Catfish 的 Top-K、长度上限和片段排序策略；不能因为模型支持长上下文就无限注入 Wiki/Memory。

### 第四轮：RAG 基础、检索评估与上下文工程

- RAG 的基础路线已经成熟，但 Catfish 的实际瓶颈更可能在“哪些内容应写入、如何分块、如何排序、如何评估”和“检索结果能否安全进入工具调用链”。
- 当前实现同时保留 BM25 与 dense semantic search 是合理的：BM25 擅长精确术语，dense 检索补充同义/语义表达；下一步可以做 hybrid/RRF，而不是直接删除 BM25。
- Advisor 的上下文应继续限制长度并保留来源/标题，避免长上下文造成“检索到了但模型用不到”。

18. **MemGPT: Towards LLMs as Operating Systems**（2023）
    - ArXiv：https://arxiv.org/abs/2310.08560
    - 关联 Catfish：短期上下文、长期记忆、跨会话对话和大文档处理。
    - 要点：借鉴操作系统分层内存，通过虚拟上下文管理把有限上下文与外部记忆结合。
    - 初步判断：直接对应 Catfish 的 memory 分层；可重点参考“何时换入/换出”和“记忆管理动作”，不应简单把所有历史都塞进 prompt。

19. **Generative Agents: Interactive Simulacra of Human Behavior**（2023）
    - ArXiv：https://arxiv.org/abs/2304.03442
    - 关联 Catfish：员工个人经历、摘要、反思和后续行动建议。
    - 要点：保存经历，逐步合成高层反思，并在规划行为时动态检索相关记忆。
    - 初步判断：适合参考 Catfish 从 journal 到 Wiki/经验的蒸馏链路；需要额外加入员工确认、删除和数据主权控制。

20. **Reflexion: Language Agents with Verbal Reinforcement Learning**（2023）
    - ArXiv：https://arxiv.org/abs/2303.11366
    - 关联 Catfish：任务执行后的复盘、经验记忆和后续任务改进。
    - 要点：不更新模型权重，而是把反馈转成语言形式的 episodic memory，影响后续决策。
    - 初步判断：可用于设计“任务失败/成功后是否生成个人经验”的机制；要防止错误反思长期污染个人知识库。

### 第五轮：个人记忆与长期 Agent

- 记忆论文共同说明：记忆不只是“向量库”，还包括写入策略、压缩/反思、检索、过期、冲突和可删除性。
- Catfish 已有本地个人数据和员工主权约束，因此论文中的自动写入机制只能作为候选，必须增加显式同意、来源溯源和撤销机制。

21. **Efficient Memory Management for Large Language Model Serving with PagedAttention**（2023）
    - ArXiv：https://arxiv.org/abs/2309.06180
    - 关联 Catfish：中央 GPU 上的多模型服务、并发请求、OOM 与 KV cache 管理。
    - 要点：PagedAttention 减少 KV cache 碎片和浪费，vLLM 在相同延迟下提高吞吐。
    - 初步判断：与当前 Docker/vLLM 内网模型部署直接相关；适合排查 Gateway workers、并发和显存配置问题。

22. **SGLang: Efficient Execution of Structured Language Model Programs**（2023）
    - ArXiv：https://arxiv.org/abs/2312.07104
    - 关联 Catfish：复杂 Agent 流程、结构化输出、工具调用和 RAG 多轮推理。
    - 要点：通过结构化程序运行时、RadixAttention 和结构化解码提升复杂 LLM 程序效率。
    - 初步判断：可作为 vLLM 之外的服务运行时对比；当前不建议为解决单一部署问题直接更换栈。

23. **LayoutLM: Pre-training of Text and Layout for Document Image Understanding**（2019）
    - ArXiv：https://arxiv.org/abs/1912.13318
    - 关联 Catfish：PDF/扫描件/附件解析、OCR 后知识入库。
    - 要点：联合建模文本、版面和视觉信息，面向表单、票据和扫描文档理解。
    - 初步判断：如果知识库要从复杂 PDF/表格中抽取结构化内容，比单纯把 OCR 文本直接 embedding 更值得参考。

24. **LayoutLLM: Layout Instruction Tuning with Large Language Models for Document Understanding**（2024）
    - ArXiv：https://arxiv.org/abs/2404.05225
    - 关联 Catfish：多模态文档解析、版面敏感的知识抽取。
    - 要点：引入 layout-aware 预训练、监督微调和 LayoutCoT，让模型关注与问题相关的页面区域。
    - 初步判断：中期文档理解升级方向；当前 Catfish 的 Markdown/Wiki 主路径不需要立即引入。

25. **Privacy-Preserving Retrieval Augmented Generation with Differential Privacy**（2024）
    - ArXiv：https://arxiv.org/abs/2412.04697
    - 关联 Catfish：员工个人数据、知识库检索、中央/边缘分离和防止 RAG 泄露。
    - 要点：通过差分隐私预算，把隐私保护重点用于真正需要敏感信息的 token。
    - 初步判断：理论上相关，但 Catfish 当前优先采用本地存储/本地检索和私有模型隔离；差分隐私不是当前最直接的工程解。

26. **The Good and The Bad: Exploring Privacy Issues in Retrieval-Augmented Generation (RAG)**（2024）
    - ArXiv：https://arxiv.org/abs/2402.16893
    - 关联 Catfish：知识库内容被检索后进入 LLM，以及通过查询反推出私有文档的风险。
    - 要点：实证分析 RAG 可能泄露检索库，同时也讨论 RAG 对模型训练数据泄露的缓解作用。
    - 初步判断：高优先级风险评估参考；应纳入 Catfish 的知识库越权检索和数据外发测试。

27. **Securing the Model Context Protocol (MCP): Risks, Controls, and Governance**（2025）
    - ArXiv：https://arxiv.org/abs/2511.20920
    - 关联 Catfish：MCP Registry、Tool Bridge、沙箱、网关和跨工具审计。
    - 要点：讨论内容注入、供应链攻击、工具越权，并提出 scoped authorization、溯源、容器沙箱、输入输出检查和集中治理。
    - 初步判断：与 Catfish 的 MCP 架构高度贴合；值得对照检查现有 Registry、权限和审计是否覆盖这些控制点。

28. **Model Context Protocol (MCP): Landscape, Security Threats, and Future Research Directions**（2025）
    - ArXiv：https://arxiv.org/abs/2503.23278
    - 关联 Catfish：MCP 生命周期、工具生态与安全治理。
    - 要点：从创建、运行、更新三个阶段分析 MCP 的安全和隐私风险。
    - 初步判断：适合作为 MCP 设计总览；与上一论文结合阅读，不必把论文中的行业数据直接当成 Catfish 结论。

### 阶段摘要（第 2 阶段）

- 中央服务层最直接的研究参考是 vLLM/PagedAttention、SGLang、FrugalGPT/RouteLLM：分别对应吞吐/显存、结构化 Agent 执行、模型成本与质量路由。
- 文档入库不能只看 embedding：扫描 PDF、表格和版面结构需要单独的文档理解链路。
- MCP 和 RAG 的安全问题是同一条攻击面上的不同位置：不可信内容可以通过检索进入上下文，再通过工具权限变成真实副作用。

29. **AgentBench: Evaluating LLMs as Agents**（2023）
    - ArXiv：https://arxiv.org/abs/2308.03688
    - 关联 Catfish：多轮 Agent、工具调用、任务完成率和跨模型比较。
    - 要点：在 8 个交互环境中评估 LLM Agent，指出长期推理、决策和指令遵循是主要失败来源。
    - 初步判断：可借鉴为 Catfish 的 Agent 回归测试框架，不应只测单轮聊天质量。

30. **Evaluation of Retrieval-Augmented Generation: A Survey**（2024）
    - ArXiv：https://arxiv.org/abs/2405.07437
    - 关联 Catfish：知识库召回、回答相关性、准确性和 faithfulness。
    - 要点：分别讨论 Retrieval 和 Generation 的可量化指标、基准和统一评估流程。
    - 初步判断：适合建立 Catfish 的“召回质量 + 回答是否忠于来源”双层评估。

31. **ARES: An Automated Evaluation Framework for Retrieval-Augmented Generation Systems**（2023）
    - ArXiv：https://arxiv.org/abs/2311.09476
    - 关联 Catfish：自动评估知识库上下文相关性、回答忠实度和回答相关性。
    - 要点：用轻量模型评估 context relevance、answer faithfulness、answer relevance，并结合少量人工标注。
    - 初步判断：可以参考其“自动评估 + 少量人工校准”思路，降低企业知识库评测成本。

32. **Benchmarking Large Language Models in Retrieval-Augmented Generation**（2023）
    - ArXiv：https://arxiv.org/abs/2309.01431
    - 关联 Catfish：中文知识库 RAG 测试。
    - 要点：提出中英文 RAG Benchmark，测试噪声鲁棒性、负例拒答、信息整合和反事实鲁棒性。
    - 初步判断：比只测命中率更接近 Catfish 的实际问题，尤其是“检索到错误内容时能否拒绝”。

### 第六轮：Agent 与 RAG 评估

- Catfish 后续应同时测四件事：检索召回、上下文使用、事实忠实度、工具副作用安全。
- 中文企业数据需要自建评测集；公开英文 benchmark 只能作为方法参考，不能直接代表达华场景效果。

33. **ColBERT: Efficient and Effective Passage Search via Contextualized Late Interaction over BERT**（2020）
    - ArXiv：https://arxiv.org/abs/2004.12832
    - 关联 Catfish：知识库中对长文档和细粒度段落的高质量检索。
    - 要点：query/document 独立编码，检索时用 late interaction 保留 token 级相似度，在效率和精度间折中。
    - 初步判断：当 Wiki 数量和内容长度增长时，可作为比单向量 cosine 更精细的候选方案；当前规模不必马上引入。

34. **Precise Zero-Shot Dense Retrieval without Relevance Labels（HyDE）**（2022）
    - ArXiv：https://arxiv.org/abs/2212.10496
    - 关联 Catfish：员工自然语言问题与企业文档措辞差异较大的情况。
    - 要点：先生成假设文档，再将其向量化，用假设文档的语义表征检索真实文档。
    - 初步判断：可改善复杂查询，但生成假设文档会增加一次 LLM 调用并引入幻觉；在数据零出端要求下需谨慎。

35. **An Analysis of Fusion Functions for Hybrid Retrieval**（2022）
    - ArXiv：https://arxiv.org/abs/2210.11934
    - 关联 Catfish：BM25 + BGE-M3 的混合检索。
    - 要点：比较分数加权和 RRF，指出不同融合方法的参数敏感性与跨域表现差异。
    - 初步判断：直接支持后续 hybrid 检索设计；不能只把 BM25 分数和 cosine 分数未经归一化相加。

### 第七轮：混合检索与细粒度召回

- 现有 Catfish 的 BM25 + dense 双路径是正确的工程基础；下一步应先做归一化、融合和离线评测，再考虑 ColBERT/HyDE 等增加复杂度的方案。
- 对企业知识库而言，专有名词、编号、合同号等精确词面信息仍然重要，纯 embedding 不能替代 BM25。

## 调研结论

### 关键事实

1. Catfish 当前知识库的技术核心是本地 Markdown、BGE-M3 embedding、SQLite 向量缓存、cosine 检索和 Wiki 关系字段；中央 wiki-hub 主要负责发布/拉取文档，不负责向量化。
2. 当前最贴合项目的研究组合是：BGE-M3 + BM25/dense hybrid + GraphRAG/LightRAG + Agent memory + AgentDojo/InjecAgent 安全评测。
3. “知识库检索质量”和“Agent 是否安全行动”必须分开测量：召回到恶意或错误内容，不代表可以把它当成指令执行。
4. 本地优先和员工数据主权是 Catfish 的架构约束；云端 RAG、差分隐私和联邦学习论文只能作为补充方案，不能覆盖这一基本约束。

### 建议阅读顺序

#### P0：直接影响当前实现

1. BGE M3-Embedding：https://arxiv.org/abs/2402.03216
2. Retrieval-Augmented Generation：https://arxiv.org/abs/2005.11401
3. An Analysis of Fusion Functions for Hybrid Retrieval：https://arxiv.org/abs/2210.11934
4. LightRAG：https://arxiv.org/abs/2410.05779
5. Evaluation of RAG：https://arxiv.org/abs/2405.07437
6. Lost in the Middle：https://arxiv.org/abs/2307.03172

#### P1：直接影响 Agent、工具和安全

7. ReAct：https://arxiv.org/abs/2210.03629
8. AgentBench：https://arxiv.org/abs/2308.03688
9. InjecAgent：https://arxiv.org/abs/2403.02691
10. AgentDojo：https://arxiv.org/abs/2406.13352
11. Securing MCP：https://arxiv.org/abs/2511.20920
12. The Instruction Hierarchy：https://arxiv.org/abs/2404.13208

#### P1：直接影响个人记忆

13. MemGPT：https://arxiv.org/abs/2310.08560
14. Generative Agents：https://arxiv.org/abs/2304.03442
15. Reflexion：https://arxiv.org/abs/2303.11366
16. Agent Memory Survey：https://arxiv.org/abs/2404.13501

#### P2：服务端和文档处理

17. PagedAttention/vLLM：https://arxiv.org/abs/2309.06180
18. SGLang：https://arxiv.org/abs/2312.07104
19. LayoutLM：https://arxiv.org/abs/1912.13318
20. Privacy Issues in RAG：https://arxiv.org/abs/2402.16893

### 对 Catfish 的落地建议

- 短期：给中文企业 Wiki 建 query/相关文档标注集，评估 BM25、BGE-M3 和 hybrid；记录 Recall@K、NDCG、上下文使用率、faithfulness。
- 中期：把现有 `related` 关系接入图引导召回；保留 BM25 作为精确术语通道。
- 安全：把知识库、邮件、网页和 MCP 返回内容统一标记为“不可信数据”，增加间接注入、工具越权、数据外发回归测试。
- 记忆：把“写入、摘要、反思、检索、删除、冲突处理”分开设计，任何自动沉淀都保留来源并允许员工撤销。
- 服务：先用现有 vLLM/网关做 workers、显存、KV cache 和延迟基线，再评估 SGLang 或更复杂的路由器。

### 待确认问题

- 达华实际知识库规模、中文 query 分布和人工相关性标注尚未取得，无法仅凭公开论文决定 BGE-M3、BM25 和 hybrid 的最终权重。
- 当前生产 Companion 是走远程 BGE-M3 还是本机 ONNX，需要结合运行日志确认；论文只能说明方法，不能替代现场链路验证。
- MCP 相关论文较新，多为预印本；需要结合项目实际协议版本和 Registry 实现复核。

### 第三轮：工具调用、模型路由与成本

- 工具调用研究提供“模型何时行动”的方法，但 Catfish 的核心差异在于工具不是无边界 API，而是带员工主权、最小权限、审计和人工确认的受控能力。
- FrugalGPT/RouteLLM 与当前 Gateway 的 fallback 方向一致，但必须把“数据是否允许出端”作为比价格和质量更高的路由约束。
- InjecAgent/AgentDojo 说明，知识库、邮件等外部内容应被视为不可信输入；检索正确不等于行动安全。

### 阶段摘要（第 1 阶段，前三轮搜索）

- Catfish 当前知识库的核心技术路径是“本地文件 + BGE-M3 向量 + cosine + 关系字段”，不是中央 Wiki Hub 的向量数据库。
- 当前实现已经覆盖了最小可用的 dense semantic retrieval，但 BGE-M3 论文中的 sparse/multi-vector 能力尚未利用。
- 下一步最有价值的升级方向不是盲目增加模型，而是：增量索引、混合 BM25+dense、关系/图引导召回、向量身份与缓存一致性、以及针对不可信文档的 AgentDojo/InjecAgent 风格安全测试。
- 工具调用与知识库检索必须一起评估：检索到的内容既是上下文，也可能是攻击载荷。

## 来源列表

| 来源 | URL | 发布日期 | 可信度 |
|---|---|---:|---|
| BGE M3-Embedding | https://arxiv.org/abs/2402.03216 | 2024-02-05 | 高（一手论文） |
| GraphRAG Survey | https://arxiv.org/abs/2408.08921 | 2024-08-15 | 高（一手综述） |
| LightRAG | https://arxiv.org/abs/2410.05779 | 2024-10-08 | 高（一手论文） |
| HyperGraphRAG | https://arxiv.org/abs/2503.21322 | 2025-03-27 | 高（一手论文） |
| Agent Memory Survey | https://arxiv.org/abs/2404.13501 | 2024-04-21 | 高（一手综述） |
| Storage to Experience Survey | https://arxiv.org/abs/2605.06716 | 2026-05-07 | 高（一手综述） |
| ReAct | https://arxiv.org/abs/2210.03629 | 2023-03-10 | 高（一手论文） |
| InjecAgent | https://arxiv.org/abs/2403.02691 | 2024-03-05 | 高（一手基准论文） |
| AgentDojo | https://arxiv.org/abs/2406.13352 | 2024-06-19 | 高（一手基准论文） |
| Instruction Hierarchy | https://arxiv.org/abs/2404.13208 | 2024 | 高（一手论文） |
| Toolformer | https://arxiv.org/abs/2302.04761 | 2023-02-09 | 高（一手论文） |
| FrugalGPT | https://arxiv.org/abs/2305.05176 | 2023-05-09 | 高（一手论文） |
| RouteLLM | https://arxiv.org/abs/2406.18665 | 2024-06-26 | 高（一手论文） |
| RAG for Knowledge-Intensive NLP | https://arxiv.org/abs/2005.11401 | 2020-05-22 | 高（一手论文） |
| Dense Passage Retrieval | https://arxiv.org/abs/2004.04906 | 2020-04-10 | 高（一手论文） |
| BEIR | https://arxiv.org/abs/2104.08663 | 2021 | 高（一手基准论文） |
| Lost in the Middle | https://arxiv.org/abs/2307.03172 | 2023-07-06 | 高（一手论文） |
| MemGPT | https://arxiv.org/abs/2310.08560 | 2023-10-12 | 高（一手论文） |
| Generative Agents | https://arxiv.org/abs/2304.03442 | 2023-04-07 | 高（一手论文） |
| Reflexion | https://arxiv.org/abs/2303.11366 | 2023-03-20 | 高（一手论文） |
| PagedAttention / vLLM | https://arxiv.org/abs/2309.06180 | 2023-09-12 | 高（一手论文） |
| SGLang | https://arxiv.org/abs/2312.07104 | 2023-12-12 | 高（一手论文） |
| LayoutLM | https://arxiv.org/abs/1912.13318 | 2019-12-31 | 高（一手论文） |
| LayoutLLM | https://arxiv.org/abs/2404.05225 | 2024-04-08 | 高（一手论文） |
| Privacy-Preserving RAG with DP | https://arxiv.org/abs/2412.04697 | 2024-12-06 | 高（一手论文） |
| Privacy Issues in RAG | https://arxiv.org/abs/2402.16893 | 2024-02-23 | 高（一手论文） |
| Securing MCP | https://arxiv.org/abs/2511.20920 | 2025-11-25 | 高（一手论文） |
| MCP Landscape and Security | https://arxiv.org/abs/2503.23278 | 2025-03-30 | 高（一手综述） |
| AgentBench | https://arxiv.org/abs/2308.03688 | 2023-08-07 | 高（一手基准论文） |
| RAG Evaluation Survey | https://arxiv.org/abs/2405.07437 | 2024-05-13 | 高（一手综述） |
| ARES | https://arxiv.org/abs/2311.09476 | 2023-11-16 | 高（一手论文） |
| RGB | https://arxiv.org/abs/2309.01431 | 2023-09-04 | 高（一手基准论文） |
| ColBERT | https://arxiv.org/abs/2004.12832 | 2020-04-27 | 高（一手论文） |
| HyDE | https://arxiv.org/abs/2212.10496 | 2022-12-20 | 高（一手论文） |
| Hybrid Retrieval Fusion | https://arxiv.org/abs/2210.11934 | 2022-10-21 | 高（一手论文） |

## 2026-08-30 追问：BM25 + BGE-M3 之后的新优化

问题：BM25 + BGE-M3 的基础组合较早，2024–2026 年是否有更先进、适合 Catfish 的检索优化？

检索重点：modern sparse retrieval、dense embedding、late interaction、reranking、query rewriting、hybrid fusion、GraphRAG 和 RAG evaluation。

### 新检索结果

1. **SPLADE-v3: New baselines for SPLADE**（2024）
   - ArXiv：https://arxiv.org/abs/2403.06789
   - 神经稀疏检索：保留倒排词项的可解释性，同时扩展同义表达；论文报告其在多组 query 上优于 BM25 和 SPLADE++。
   - 对 Catfish：是 BM25 之后最值得评估的稀疏检索候选，尤其适合专有名词、合同号和语义表达混合的企业知识库。

2. **NV-Embed: Improved Techniques for Training LLMs as Generalist Embedding Models**（2024）
   - ArXiv：https://arxiv.org/abs/2405.17428
   - 新 dense embedding 训练：latent attention pooling、去 causal mask、两阶段 instruction tuning、hard negatives 和合成数据；NV-Embed-v2 在 2024 年 MTEB 排名领先。
   - 对 Catfish：可作为 BGE-M3 的替换候选，但模型更重，不能直接据公开 benchmark 推断中文达华 Wiki 一定更好。

3. **jina-embeddings-v3: Multilingual Embeddings With Task LoRA**（2024）
   - ArXiv：https://arxiv.org/abs/2409.10173
   - 570M 参数、最长 8192 tokens、任务 LoRA 和 Matryoshka 表征，支持把输出维度从 1024 缩到更低维度。
   - 对 Catfish：适合测试中英文混合、长 Wiki 和低维缓存；必须用达华专有名词和中文 query 验证。

4. **ColPali: Efficient Document Retrieval with Vision Language Models**（2024）
   - ArXiv：https://arxiv.org/abs/2407.01449
   - 直接对文档页面图像生成多向量表示，绕过脆弱的 OCR 文本抽取，适合表格、图表和复杂版面。
   - 对 Catfish：如果知识库大量来自扫描 PDF，这是比“纯 OCR + BGE-M3”更先进的路线；代价是显存、存储和部署复杂度更高。

5. **Lost in OCR Translation? Vision-Based Approaches to Robust Document Retrieval**（2025）
   - ArXiv：https://arxiv.org/abs/2505.05666
   - 比较视觉检索与 OCR RAG，指出视觉方案在适配过的文档上有优势，而 OCR 路线对未见文档和质量变化的泛化更好。
   - 对 Catfish：适合做“按文档类型路由”，不建议因为视觉模型更新就全面替换 OCR。

6. **Enhancing Retrieval-Augmented Generation with Two-Stage Retrieval: FlashRank Reranking and Query Expansion**（2025/2026 条目）
   - ArXiv：https://arxiv.org/abs/2601.03258
   - 先用 query expansion 提高召回，再用结合相关性、新颖性、简洁性和证据的 reranker，在 token budget 下选片段。
   - 对 Catfish：与现有“Top-K 后直接注入 Advisor”最接近；优先级高于立即换 Embedding。

7. **Corrective Retrieval Augmented Generation（CRAG）**（2024）
   - ArXiv：https://arxiv.org/abs/2401.15884
   - 先评估检索质量，再决定过滤、纠正文档或扩大检索范围。
   - 对 Catfish：可借鉴本地低置信度纠错和拒答；其公网扩展部分与数据零出端原则冲突。

8. **Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection**（2023）
   - ArXiv：https://arxiv.org/abs/2310.11511
   - 模型按需检索，并对检索片段和生成结果自检，避免固定 Top-K 带来的无效上下文。
   - 对 Catfish：可借鉴“无必要不检索、检索后检查证据”，不必直接训练特殊 reflection tokens。

9. **RAFT: Adapting Language Model to Domain Specific RAG**（2024）
   - ArXiv：https://arxiv.org/abs/2403.10131
   - 训练模型区分有用文档和 distractor，并引用相关证据。
   - 对 Catfish：适合未来有稳定达华领域数据后的私有后训练；当前先做检索和评测。

### 第八轮：2024–2026 新检索优化结论

- 新论文没有淘汰 BM25，而是把它升级为更强组合的一部分：SPLADE-v3（神经稀疏）、NV-Embed/Jina v3（dense）、ColBERT/ColPali（late interaction）、reranker、query expansion 和 retrieval correction。
- 对 Catfish 最现实的顺序是：**BM25 + BGE-M3 → 分数归一化/混合 → reranker → 低置信度纠错 → 再评估是否换 embedding 或上视觉检索**。
- BGE-M3 仍是合理的多语言基线；新模型在公开榜单领先，不等于在达华中文 Wiki、合同编号和内部简称上领先。

### 追问阶段来源

| 来源 | URL | 发布日期 | 可信度 |
|---|---|---:|---|
| SPLADE-v3 | https://arxiv.org/abs/2403.06789 | 2024-03-11 | 高（一手论文） |
| NV-Embed | https://arxiv.org/abs/2405.17428 | 2024-05-27 | 高（一手论文） |
| jina-embeddings-v3 | https://arxiv.org/abs/2409.10173 | 2024-09-16 | 高（一手论文） |
| ColPali | https://arxiv.org/abs/2407.01449 | 2024-06-27 | 高（一手论文） |
| Lost in OCR Translation | https://arxiv.org/abs/2505.05666 | 2025-05-08 | 高（一手论文） |
| Two-Stage Retrieval / FlashRank | https://arxiv.org/abs/2601.03258 | 2025-10-17 | 中（一手预印本，需复核） |
| CRAG | https://arxiv.org/abs/2401.15884 | 2024-01-29 | 高（一手论文） |
| Self-RAG | https://arxiv.org/abs/2310.11511 | 2023-10-17 | 高（一手论文） |
| RAFT | https://arxiv.org/abs/2403.10131 | 2024-03-15 | 高（一手论文） |
