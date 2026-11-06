# Wiki 关联行动

Wiki 本体和执行能力分两层：Markdown 只记录稳定的 `action_refs`，执行定义由 Companion 内置并在本机按需扩展。这样知识条目不会绑定某个模型、工具名或操作系统，也不依赖中央端。

## 配置

Companion 自带 `edge/contracts/action_registry.default.yaml` 的内置行动目录，普通员工不需要准备任何文件。开发者或高级用户可以把 `edge/contracts/action_registry.example.yaml` 放到 `~/.catfish/action-registry.yaml`，覆盖同名行动或追加本机行动；也可以用 `CATFISH_ACTION_REGISTRY` 做开发/测试时的完整替换。

每个注册项必须声明 `id`、展示信息、`executor: skill`、`skill_path`、审批策略、支持平台和超时时间。当前版本只允许通过已有 `catfish_run_skill` bridge 执行；其他 executor 会显示为不可执行，不会绕过工具权限直接起 shell 或调用模型。

## 使用

在知识页「关联行动」区域从下拉选择行动即可，系统自动保存 action ID；普通用户看不到也不需要填写 `skill_path`。下拉里是内置注册表加上本机装了的技能（`~/.catfish/skills/`、专家包，开发机还有仓库 `skills/`），本机没装对应技能的灰掉。

点「交给小鲶」（11/06 起）会切到工作台开一段新对话，让小鲶：先用 `catfish_wiki_read` 读这条知识和它的关系，从中取技能要的输入；能直接调的技能（有 `script.py` / `workflow.yaml`）走 `catfish_run_skill`，只有说明的技能读它的 `SKILL.md` 照着做；缺的输入问员工，登录、提交、发送这类对外操作照常先确认。原来的做法是在知识页直接调 `catfish_run_skill` 且不传任何参数，有必填项的技能一点就失败，已去掉。

行动不会在切换 Tab、读取 Wiki 或刷新关系图时触发。

## 历史关系整理

早期 Wiki 可能使用 `related: ["[[目标条目]]"]`，这种格式只有目标没有关系类型。
关系整理页会把它标为“关系异常”，但这不代表目标一定断链。点击“检查旧关系”后，
可以一次性把这类关系规范化为 `rel: "关联"`；原目标和正文不变，执行前会备份到
`~/.catfish/wiki/.migration-backups/`。找不到目标或存在歧义的关系不会被自动猜测，
仍需人工确认。
