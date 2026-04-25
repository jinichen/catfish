# catfish-policy · Hermes 插件

> 鲶鱼红线策略 — 在工具调用层拦截高危操作
>
> 原则：只定义"禁止做什么"，不审查对话内容

---

## 能拦住什么

规则都在 `rules.yaml` 里，支持 4 种匹配 kind：

| kind | 匹配什么 | 当前默认规则 |
|---|---|---|
| `shell_command` | `bash` / `execute_code` 等工具里的命令子串 | R1 系统破坏命令、R4 钥匙串导出 |
| `file_read` | `read_file` / `cat` 等工具的文件路径 | R2 敏感凭据路径 |
| `network_request` | URL 或命令里嵌入的 host | R3 绕过网关直调公共 LLM |
| `tool_name` | 工具名本身 | （P1 用于 cronjob 之类） |

修改规则只需编辑 `rules.yaml`，重启 Hermes 会重新加载（不改代码）。

---

## 安装与激活

这个插件是 Hermes 的 "user plugin"。两种装法：

**方式 A · 软链**（推荐，改 rules.yaml 立即生效）
```bash
mkdir -p ~/.hermes/plugins
ln -s /Users/chenhongbo/person_task/catfish/plugins/catfish-policy \
      ~/.hermes/plugins/catfish-policy
```

**方式 B · 复制**
```bash
cp -r /Users/chenhongbo/person_task/catfish/plugins/catfish-policy \
      ~/.hermes/plugins/
```

然后**激活**（Hermes 插件默认是 opt-in，装了也要显式 enable）：
```bash
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
帮我跑一下 rm -rf /
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

规则不是硬编码在 Python 里的——`matchers.py` 只实现了 4 种通用 kind。想加新的 kind（比如"限制 domain CIDR"、"限制命令正则"），在 matchers.py 加一个分支就行。

所有 matcher 是纯函数，可以离线 pytest：

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
