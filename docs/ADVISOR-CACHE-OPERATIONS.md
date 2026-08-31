# 早安 Advisor 缓存边界

## 数据所有权

早安页面的当前待办依据是本轮采集到的 Reminders、Calendar、邮件，以及用户明确维护的当前计划文件。`~/.catfish/distilled_facts.md` 和近期 MEMORY 只提供历史上下文，不能单独创建当前待办。

`~/.catfish/advisor_cache.json` 是派生缓存，不是任务数据库。它只用于减少重复 LLM 调用、复用当前任务的 `taskUid` 和保存当前任务的聊天状态。

## 缓存何时有效

缓存必须同时满足以下条件：

- 缓存格式版本和输入边界版本匹配；
- 自然周窗口匹配（周一 00:00 到下周一 00:00）；
- Reminders、Calendar、邮件和当前计划的输入指纹匹配；
- 模型和缓存新鲜度满足配置；
- 缓存中的每条主菜仍能通过当前输入依据校验。

旧缓存没有 `sourceMeta`，会被当作无效缓存。它不会再进入 `previousTasks`，也不会作为超时回退结果显示。

## 任务生命周期

任务状态保存在 `taskChatSummaries[taskUid]` 中。完成、忽略、暂停和重新打开属于状态变化，不会把历史任务重新变成当前任务。只有当前来源仍存在时，缓存中的 UID 和状态才会被复用。

## 故障处理

清理派生缓存只使用：

```bash
rm -f ~/.catfish/advisor_cache.json
```

这不会删除数据库、Reminders、Calendar、邮件或知识库。清理后重新打开早安页面，系统会基于当前数据生成新缓存。不要通过删除 `distilled_facts.md`、聊天记录或数据库来处理 Advisor 缓存问题。

