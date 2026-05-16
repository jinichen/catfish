"""BL-FORCE-TOOL-NUDGE — **已废弃 (5/15 15:17 鸿波 '补丁的偷懒方式')**.

# 失败史

| 版本 | 时间 | 机制 | 失败方式 |
|---|---|---|---|
| v1 | 5/15 14:50 | `tool_choice={"type":"function","function":...}` 强制 | Qwen Go gRPC adapter 不接 specific function, 134s 0 token 卡死 |
| v2 | 5/15 15:10 | messages 中间插 system hint | Qwen 严格校验 messages 角色顺序, 400 BadRequest 直返 |

# 根因 (鸿波 5/13 早说过, 我忽略了)

LLM 嘴炮 stop 不调 tool 是**模型层问题**, 不在 gateway 治. 我连续两次想用
gateway 端的小聪明 (强制 tool_choice / 注入 hint) 绕过, 都被上游适配器拒收.
应该认账, 把问题推到正确层:

1. **模型层**: 模型 prompt 调优 / 换更听话的模型 (Gemini Pro / Claude)
2. **客户端层**: Companion "停下接着发" 按钮自动多次重试
3. **接受**: agent 偶尔嘴炮 stop, 用户点继续就好 (鸿波 5/13 拍板)

# 本模块状态

完全 dead code. 没人 import 它. 测试 (test_tool_choice_nudge.py) 同步废弃.
留着只为档案 — 提醒下次别重蹈"补丁覆盖架构判断"的覆辙.
"""

# 此文件保留为废弃标记. 内容曾被实测撞坏, 不要再 import 或调用任何函数.
