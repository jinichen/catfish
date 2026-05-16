# Catfish 隐私设计原则 (核心立场)

> **状态**: 强制纪律. 任何 PR / 设计提议违反这些原则 = 不收.
>
> **写作背景**: 5/16 晚 catfish + 鲶鱼一起做 "记忆部门" follow-up 评估时, 鲶鱼建议"跨员工 memory 同步 / GDPR 中心化审计 / 企业 vs 个人 memory 池", 鸿波指出全部反 catfish 设计. 当晚固化这条立场, 防未来再"乱".
>
> **关联文档**: `MAY-DEMO-Q-AND-A.md` (客户话术版), `SOUL.md` BL-MM1 红线段 (LLM 写 memory 的纪律), 本文档 (工程纪律版).

---

## 一句话

**员工私有数据 = 永远本机, 中心永不采集, 除非员工 explicit 上传授权.**

中心只看 **metadata** (token 数 / 模型选择 / 调用时长 / quota), **看不到内容**.

---

## 5 条强制原则

### 1. 员工本机存储 = 不可挪到中心

下面这些路径的数据**必须**永远在员工电脑:

```
~/.hermes/memories/USER.md         (员工身份 / 关系 / 偏好)
~/.hermes/memories/MEMORY.md       (项目 / 技术 / 操作事实)
~/.hermes/state.db                 (session 历史 + messages)
~/.catfish/session_facts.json      (deprecated 但仍在本机)
~/.catfish/user_profile.json       (9 字段画像)
~/.catfish/employee_journal.md     (对话日记)
~/.catfish/distilled_facts.md      (精华)
~/.catfish/feedback.jsonl          (反馈)
~/.catfish/output/                 (生成的 docx / pptx 等)
~/.catfish/uploads/                (上传文件)
~/.catfish/.catfish_audit.jsonl    (工具调用 audit)
~/.hermes/.catfish_audit.jsonl     (同上, hermes 路径下)
```

**不允许**:
- ❌ 设计任何"自动同步到中心"功能
- ❌ 中心 admin 后台显示员工 memory / 对话 / 反馈具体内容
- ❌ 跨员工查询某人的 memory / 画像 (e.g. "查张三都记了啥")
- ❌ 中心数据库存员工本机文件的 mirror / index
- ❌ 任何形式的"为了客户成功 / 运营优化"主动采集

**允许** (员工 explicit 操作触发):
- ✅ 员工主动点"导出我的 memory" 拿到 zip, 自己上传
- ✅ 员工主动 explicit consent 把某条 memory 提议为公司 skill (走 Skills Hub 审核)
- ✅ 员工自己用 iCloud / Dropbox / Time Machine 同步本机文件 (catfish 不参与)

### 2. 中心只看 metadata

`catfish-gateway` 中央服务允许采集:

```
- token 数 (prompt_tokens / completion_tokens)
- 模型选择 (catfish-private-main / catfish-public-deepseek-flash 等)
- 调用时长 (latency_ms)
- quota 用量 (per-user-day / per-dept-day)
- 错误状态 (error_type, 不含具体 message)
- 时间戳
- 员工 sub (用于 quota 归属, 不含别的)
```

**禁止采集**:
- ❌ 对话内容 (user prompt / assistant response 文字)
- ❌ tool_calls 的 args (可能含员工敏感信息)
- ❌ tool_calls 的 result (可能含查到的客户数据)
- ❌ 注入到 system prompt 的 inject 内容 (memory / journal / 画像 文字)

工程上对应: `catfish.metrics` 写的 `llm_request` JSON 只允许上面那些字段. 任何 PR 想加内容字段 = 拒.

### 3. 凭据 = secret_ref + 平台 keychain

员工密码 / API key / token 永不进 LLM context:

- 存 macOS Keychain / Win Credential Manager / Linux Secret Service
- LLM 写 prompt 只看到 `secret_ref://eis_password` 这种符号
- 真值在 tool-bridge 调用工具落地的一瞬间才解析
- catfish-policy R9 拦截 LLM 试图改 `~/.hermes/config.yaml` / `USER.md` 等
- gateway prompt 入口加 regex 检测员工明文密码 → audit + 自动改成 ref

### 4. 公司知识 ≠ 员工 memory

两条独立路径, 不混:

| 类型 | 存哪 | 谁写 | 谁看 |
|---|---|---|---|
| 员工个人 memory | 本机 `~/.hermes/memories/` | LLM 主动 / 员工 explicit | 仅员工本人 (LLM 注入到本人对话) |
| 公司知识 (SOP / 流程) | Skills Hub 中央 | 员工 propose → 公司审核 | 全员可装 |

跨员工知识共享走 **Skills Hub 流程**, 不走 "memory 同步".

### 5. 审计在本机

`.catfish_audit.jsonl` (本机) 是 source of truth:
- 每次工具调用一行 (tool name / args 摘要 / ok / latency / 时间)
- 员工自己 + 客户 IT 部门可以扫盘看
- 中心 audit 服务**不存** audit 文件本身, 只存 quota 用量统计

---

## 客户 / 法务可能问的问题 (预答)

**Q1: 员工换电脑怎么继承 memory?**
A: 员工自己用 iCloud Drive / Time Machine / 自家 sync 工具同步 `~/.hermes/` + `~/.catfish/`. catfish 不做云端 sync.

**Q2: 团队想共享某员工的 SOP 怎么办?**
A: 员工把 SOP 走 Skills Hub propose (`catfish_propose_skill`), 公司审核后全员可装. Memory 不共享, skill 才共享.

**Q3: GDPR 删除权 (员工要求"删我所有数据") 怎么响应?**
A: 中心数据库**根本没存**员工内容, 只有 quota 用量统计 (基于 sub 聚合). 员工自己 `rm -rf ~/.hermes ~/.catfish` 即"删除全部". 中心 quota 表删 sub 对应行即可 (1 SQL).

**Q4: 客户安全部门要求"审计员工跟 AI 聊了啥"** (合规要求)
A: 这跟 catfish 设计冲突. 两个选项:
- A. 不卖给这个客户 (我们立场)
- B. 客户 IT 装 catfish 时启用 `audit_full_log=true` env 选项, 让 .catfish_audit.jsonl 含完整 prompt/response, **但仍在员工本机** (客户 IT 自己扫盘合规). 中心仍不存. (P3 feature, 不默认开)

**Q5: 中央 admin 后台都能看什么?**
A: 仅:
- 各员工 token / 调用次数 (匿名化或脱敏 sub)
- 模型可用性 / latency p50/p99
- quota 配额管理
- 部门预算消耗
- 不能看任何对话 / memory / 反馈具体内容

---

## 如何检测违反原则

PR review 时 + 设计提议时 grep 这些反模式:

| 反模式 keyword | 警告 |
|---|---|
| "同步到中心" / "上传 memory" / "中心 mirror" | 反原则 1 |
| metrics 字段含 `content` / `prompt` / `response` 字段 | 反原则 2 |
| "中心 admin 看员工" / "跨员工查询" | 反原则 1, 5 |
| "GDPR 中心审计" / "合规中心日志" | 反原则 5 |
| "企业 memory" / "memory 池" / "共享 memory" | 反原则 4 (应该走 Skills Hub) |

---

## 商业差异化

这条原则是 catfish 对客户的**核心 USP**:
- ChatGPT / 通义 / 文心: 对话上传厂商服务器 (客户数据外流)
- 鲶鱼: 对话全本机, 厂商只调 LLM 推理拿不到上下文 (跟 LiteLLM SDK 直调 provider API 一样, 不存历史)

政企客户 (国央企 / 部委 / 大集团) 选 catfish 一半理由就是这条. 砸这条 = 砸客户信任 = 砸 catfish 商业模式.

---

**作者**: 鸿波 + 鲶鱼
**生效日期**: 2026-05-16 (周六 23:55 立)
**修订**: 任何修订需要鸿波拍板, 不允许工程师 / AI 单方面松绑
