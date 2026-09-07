# Wiki 关联行动

Wiki 本体和执行能力分两层：Markdown 只记录稳定的 `action_refs`，执行定义由 Companion 内置并在本机按需扩展。这样知识条目不会绑定某个模型、工具名或操作系统，也不依赖中央端。

## 配置

Companion 自带 `edge/contracts/action_registry.default.yaml` 的内置行动目录，普通员工不需要准备任何文件。开发者或高级用户可以把 `edge/contracts/action_registry.example.yaml` 放到 `~/.catfish/action-registry.yaml`，覆盖同名行动或追加本机行动；也可以用 `CATFISH_ACTION_REGISTRY` 做开发/测试时的完整替换。

每个注册项必须声明 `id`、展示信息、`executor: skill`、`skill_path`、审批策略、支持平台和超时时间。当前版本只允许通过已有 `catfish_run_skill` bridge 执行；其他 executor 会显示为不可执行，不会绕过工具权限直接起 shell 或调用模型。

## 使用

在 Wiki 关系工作台的“关联行动”区域从行动名称下拉选择即可，系统自动保存 action ID。普通用户看不到也不需要填写 `skill_path`。执行写操作前会弹出确认框；行动不可用、平台不支持或 skill bridge 不可用时，界面会显示原因，不会自动执行。

行动不会在切换 Tab、读取 Wiki 或刷新关系图时触发。实际执行仍经过现有 tool bridge，并沿用其权限、审计和员工身份上下文。
