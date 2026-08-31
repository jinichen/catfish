# 微信历史记录本机分析 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在不改变现有模型治理的前提下，为 Catfish 增加微信聊天导出文件的本机只读分析入口；分析使用员工当前 Picker，中央不保存微信正文。

**Architecture:** Companion 负责选择导出文件、显示数据源状态和员工显式授权；Tool Bridge 提供会话列表、按范围读取、关键词搜索三个只读工具；导出文件由独立的 `catfish-wechat-reader` 解析，由 `~/.catfish/bin/` 稳定入口调用，不修改 Hermes；分析结果回到当前会话并沿用 Picker。

**Tech Stack:** Python 3.10+ 标准库、Tauri/Rust、React/TypeScript、Tool Bridge/Python、pytest、Vitest、Cargo test。

---

## 已核实的现有边界

- Picker 的持久化真源是 `~/.catfish/picker_model` 与 `~/.catfish/picker_state.json`；微信工具不得新增模型字段、角色兜底或 YAML 模型配置。
- Tool Bridge 的工具结果会回到当前 Hermes 会话，因此模型分析天然沿用当前 Picker；工具自身不应调用 `/v1/chat/completions`。
- 中央只管理模型目录、权限、配额和审计元数据，不保存员工微信正文。
- Hermes 自带微信平台能力只覆盖在线消息接入，不提供本机历史库读取；本功能属于 Catfish 边缘工具，不重复实现 Hermes 工具。
- 当前公开 `wechat-cli` 主线存在明文密钥、持久化明文缓存和自动重签微信等风险，不能直接打进 Companion。

## 安全契约

读取器需提供 `doctor --json`，且必须明确返回：

```json
{
  "protocol_version": 1,
  "read_only": true,
  "secure_key_store": true,
  "ephemeral_plaintext_cache": true,
  "modifies_wechat_app": false
}
```

任一字段不满足，Tool Bridge 拒绝读取。Catfish 永远不调用读取器的安装、初始化、抓密钥或重签命令。

## Task 1：实现独立导出文件读取器

**Files:**
- Create: `edge/wechat-reader/pyproject.toml`
- Create: `edge/wechat-reader/src/catfish_wechat_reader/__main__.py`
- Create: `edge/wechat-reader/src/catfish_wechat_reader/readers.py`
- Create: `edge/wechat-reader/src/catfish_wechat_reader/commands.py`
- Test: `edge/wechat-reader/tests/test_cli.py`

1. 先写失败测试，覆盖 `doctor`、JSON/JSONL/CSV、非法字段、时间过滤、会话过滤、关键词搜索和输出上限。
2. 定义 Catfish 标准消息字段：`message_id/session_id/session_name/sender_id/sender_name/timestamp/type/text/is_self`。
3. 实现流式读取 JSONL/CSV；JSON 只接受对象数组或 `{messages: [...]}`，拒绝未知格式和过大文件。
4. 实现 `doctor/sessions/history/search` 固定协议；不保存索引、不复制正文、不访问网络。
5. 跑 `python -m pytest edge/wechat-reader/tests -q`，预期全部通过。

## Task 2：让 Tool Bridge 传递员工选择的数据源

**Files:**
- Create: `edge/tool-bridge/src/catfish_tool_bridge/catfish_tool_schemas_wechat.py`
- Create: `edge/tool-bridge/src/catfish_tool_bridge/wechat_archive.py`
- Modify: `edge/tool-bridge/src/catfish_tool_bridge/catfish_tool_schemas.py`
- Modify: `edge/tool-bridge/src/catfish_tool_bridge/catfish_tools.py`
- Test: `edge/tool-bridge/tests/test_wechat_archive.py`

1. 先写失败测试：未选文件、文件失效、未授权、Picker 变化、安全自检失败、参数越界均拒绝。
2. 定义 `catfish_wechat_sessions`、`catfish_wechat_history`、`catfish_wechat_search` 三个跨 macOS/Windows schema。
3. 从授权配置读取 `source_type=export_file` 和绝对 `source_path`，向读取器显式传 `--source`；禁止 shell、限制超时/输出大小/消息条数，并使用每次调用独立临时目录。
4. 只返回消息必要字段，不返回附件二进制、密钥、数据库路径或读取器内部日志。
5. 工具参数和实现中不得出现 `model`；结果交给当前会话 Picker 模型分析。

## Task 3：增加 Companion 文件选择和显式授权

**Files:**
- Create: `edge/companion-app/src-tauri/src/commands/wechat_archive.rs`
- Modify: `edge/companion-app/src-tauri/Cargo.toml`
- Modify: `edge/companion-app/src-tauri/src/commands/mod.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/invoke_handler.rs`
- Create: `edge/companion-app/src/tabs/Dashboard/WeChatArchiveCard.tsx`
- Modify: `edge/companion-app/src/tabs/Dashboard/WeChatBindingCard.tsx`

1. 状态接口显示导出读取器是否安装、当前数据源是否有效、是否启用、授权时的 Picker 和当前 Picker。
2. 使用原生文件选择器只允许 JSON、JSONL、CSV；选中新文件后清除旧授权。
3. 启用时必须由员工勾选数据范围确认；授权绑定当前 Picker 模型 ID。
4. Picker 改变或导出文件被替换后授权自动失效，员工需重新确认。
5. 只保存授权元数据、读取器路径和导出文件路径，不复制正文。
6. macOS 和 Windows 均开放导入文件；不实现微信进程、数据库或密钥读取入口。

## Task 4：把读取器纳入 Companion 安装链路

**Files:**
- Modify: `edge/companion-app/scripts/build-mac-resources.sh`
- Modify: `edge/companion-app/scripts/build-msi-local.ps1`
- Modify: `edge/companion-app/src-tauri/src/commands/hermes_install_artifacts.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/hermes_install_steps.rs`
- Modify: `edge/companion-app/src-tauri/src/commands/hermes_install.rs`

1. 构建期生成零依赖 wheel，随平台资源包分发。
2. 首次安装或检测到 reader 缺失时，只补装 reader，不重装 Hermes。
3. macOS 建立 `~/.catfish/bin/catfish-wechat-reader` 稳定软链；Windows 配置指向 venv `Scripts` 入口。
4. 安装后运行 `doctor --json`，不通过则功能保持关闭并记录明确日志。

## Task 5：验证与文档纪律

1. 运行 Tool Bridge 定向测试。
2. 运行 Companion TypeScript 构建/测试和 Rust 定向测试。
3. 运行读取器完整测试、Companion TypeScript 构建/测试和 Rust 定向测试。
4. 运行 `bash scripts/check_file_sizes.sh --strict`。
5. 检查 diff，确认没有修改中央模型配置、没有新增微信正文上传接口、没有把 `wechat-cli` 或任何抓密钥/重签逻辑打进安装包。

## 明确不做

- 不直接依赖当前 `wechat-cli` 发布包，也不宣称支持未经样本验证的第三方导出格式。
- 不在 Companion 内扫描微信进程内存、提取密钥或自动重签微信。
- 不新增“微信分析模型”配置；当前 Picker 是唯一模型来源。
- 不把微信正文、联系人或附件同步到中央数据库。
- 不解析微信官方加密备份目录；不在本次 provider-neutral 接入中实现抓密钥或进程内存扫描。
