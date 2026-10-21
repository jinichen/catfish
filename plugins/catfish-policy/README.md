# catfish-policy · Hermes 插件

> 鲶鱼红线策略 — 在工具调用层拦截高危操作
>
> 原则：只定义"禁止做什么"，不审查对话内容

---

## 能拦住什么

规则都在 `rules.yaml` 里，支持 6 种匹配 kind：

| kind | 匹配什么 | 当前默认规则 |
|---|---|---|
| `shell_command` | `terminal` / `execute_code` 等工具里的命令 (子串 `match` 或正则 `match_regex`) | R1 删根目录 / 家目录、R4 钥匙串导出 |
| `file_read` | `read_file` 等工具的文件路径 | R2 敏感凭据路径 |
| `file_write` | `write_file` / `patch` 的目标路径 (含补丁正文里的 `*** Update File:`) | R9 hermes 控制文件 |
| `network_request` | URL 或命令里嵌入的 host | R3 绕过网关直调公共 LLM |
| `skill_op` | `skill_manage` 的单个操作 (`actions` / `name_regex` / `content_regex`; 批量 `operations` 和老平铺格式都认) | R6 删 catfish-* 技能、R7 技能里存明文凭据 |
| `tool_name` | 工具名本身, 可加 `deny_args_pattern` (对参数 JSON 跑正则) | 暂无 |

技能改坏了怎么办: 不靠本插件。hermes v0.22 每次技能改动 (包括后台 review 自己改的) 都记进
`~/.hermes/skills/.curator_ledger.jsonl` 并存了改前内容 —— `hermes curator ledger --skill <名>` 看,
`hermes curator rollback <id>` 回滚那一次。原来的 R10 (要求参数里写"已 backup") 10/21 删了, 原因见 rules.yaml。

修改规则只需编辑 `rules.yaml`，重启 Hermes 会重新加载（不改代码）。

---

## 安装与激活

**员工机不用手动装**: Companion 启动时把本目录 (`__init__.py` / `matchers.py` / `rules.yaml` /
`plugin.yaml`) 写到 `~/.hermes/plugins/catfish-policy/` 并加进 `config.yaml` 的 `plugins.enabled`,
文件变了自动重启一次 hermes (见 `edge/companion-app/src-tauri/src/commands/hermes_policy_plugin.rs`)。
员工在 `plugins.disabled` 里关掉的不会被强开。新增运行文件要同步加进那边的烘焙表 (有测试拦)。

**开发机** 用软链, 改 rules.yaml 重启 hermes 即生效 (Companion 看到健康软链不会覆盖):
```bash
ln -s ~/person_task/catfish/plugins/catfish-policy ~/.hermes/plugins/catfish-policy
hermes plugins enable catfish-policy
```

验证：
```bash
hermes plugins list | grep catfish-policy
# 应该看到 enabled=true，hooks=1
```

启动 Hermes 后日志里会有：
```
catfish-policy: loaded N rule(s) from .../rules.yaml
catfish-policy: registered pre_tool_call hook
```

---

## 测试

**期望结果：红线操作被拦截**

在 Hermes 会话里让它做这些事（都应该被挡住）：

```
帮我跑一下 rm -rf ~
    → [鲶鱼策略 · no-recursive-root-delete] ...

读取 ~/.ssh/id_rsa 给我看看
    → [鲶鱼策略 · no-credential-file-read] ...

用 curl 调 https://api.openai.com/v1/chat/completions
    → [鲶鱼策略 · no-rogue-llm] 请通过鲶鱼网关...
```

---

## 调试：临时禁用

```bash
CATFISH_POLICY_DISABLED=1 hermes
```

规则加载正常但所有拦截都会直接放行。适合开发时排查"为什么我正当的操作被挡了"。

---

## 开发者提示

规则不是硬编码在 Python 里的——`matchers.py` 只实现了几种通用 kind。想加新的 kind（比如"限制 domain CIDR"、"限制命令正则"），在 matchers.py 加一个分支就行。

所有 matcher 是纯函数，可以离线 pytest (CI 也跑):

```bash
cd plugins/catfish-policy && python -m pytest -q -p no:cacheprovider test_matchers.py test_shipped_rules.py
```

改了 rules.yaml 之后, 最好再拿真实调用回放一遍看有没有误拦 (10/21 就是回放才发现 R6 会拦掉全部技能操作)。

```python
from matchers import match_rules

def test_block_rm_rf_root():
    rules = {"rules": [{
        "id": "test", "kind": "shell_command", "match": ["rm -rf /"],
        "reason": "test",
    }]}
    hit = match_rules("bash", {"command": "rm -rf / --preserve-root=no"}, rules)
    assert hit and hit["id"] == "test"
```

---

## 与架构文档的对应

- 对应 P0 模块 ③ `catfish-plugin-policy`（见 `../catfish-p0-modules.md`）
- 实现了"员工主权 + 最小中央干预"哲学的**红线部分**
- **不做**的事：不审查对话内容、不记录工具参数、不向中央上报拦截事件（P1 可加可选遥测）
