# catfish-tool-bridge

Companion App 调用 hermes-agent 35+ tools 的桥接服务。

## 定位

Companion App 作为 GUI 聊天客户端，自己直调 gateway `/v1/chat/completions`，
但 LLM 想用 tools 时（read_file / browser_navigate / shell / ...），
这些 tool 实现都在 hermes-agent 里。

`catfish-tool-bridge` 是个**长期跑的 Python 服务**：
- 启动时 `import model_tools` 触发 hermes 全部 tool 注册到 `tools.registry`
- 通过 Unix domain socket 暴露 JSON-RPC 接口
- Companion 用 Rust 当 RPC 客户端连过去

```
┌─────────────┐                              ┌─────────────────────┐
│  Companion  │                              │  tool-bridge         │
│  (Rust)     │  ←── unix socket ─────→      │  (Python)            │
│             │     JSON-RPC 2.0 / NDJSON     │                      │
│             │                                │  + hermes-agent     │
│             │                                │    tools.registry    │
│             │                                │    (35 tools)        │
└─────────────┘                              └─────────────────────┘
```

## RPC 协议

newline-delimited JSON over unix socket。

| 方法 | 参数 | 返回 |
|------|------|------|
| `tools/list` | 无 | tool 定义数组（OpenAI tool calling 兼容） |
| `tools/dispatch` | `{name, args}` | tool 执行结果 |
| `health` | 无 | `{ok: true, tools: <count>}` |

## 启动

```bash
# Companion 自动起，开发可手动:
python -m catfish_tool_bridge --socket /tmp/catfish-tool-bridge.sock

# 自定义 hermes-agent 路径:
HERMES_AGENT_PATH=/custom/path python -m catfish_tool_bridge ...
```

## 关闭路径

读 hermes-agent 的 `tools/registry.py` —— 每个 tool 模块在 import 时
通过 module-level `registry.register()` 副作用自动注册。
我们 `import model_tools` 一次性把所有 tool 拉起来。
