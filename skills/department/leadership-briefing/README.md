# leadership-briefing — 央国企汇报材料 skill

> 一句话: 给公司领导写决策事项 / 工作汇报的标准格式 .docx, 字体 / 字号 / 4 段结构全部硬编码.

详见 [SKILL.md](./SKILL.md).

## 目录

```
leadership-briefing/
├── SKILL.md            # 触发条件 + 内容 4 段约束 + 字体规范
├── README.md           # 本文件 — 给鸿波 / 客户 IT 看
├── script.py           # python-docx 渲染主脚本
├── reference.docx      # ⚠ 待补 — 鸿波明天从公司带回真实模板抄过来
└── tests/
    └── test_render.py  # 10 个测试 (字体名 / 4 段顺序 / 落款 / 边界)
```

## 跑测试

```bash
cd catfish/skills/department/leadership-briefing
pip install python-docx pytest
python -m pytest tests/ -v
```

## 字体依赖 (合规线)

skill 渲染时把字体名硬写到 .docx XML, **本机没装字体也能生成文件**, 但拿到合规客户机打开时:

| 字体 | macOS 默认 | Windows 默认 | 客户公司机 (国企) |
|------|------------|--------------|-------------------|
| 方正小标宋简体 | ✗ | ✗ | ✓ (IT 推装) |
| 仿宋_GB2312 | ✗ | ✓ | ✓ |
| 黑体 | ✓ | ✓ | ✓ |
| 楷体_GB2312 | ✗ | ✓ | ✓ |

客户 IT 部署时一行 PowerShell 把方正字体推到所有员工机:

```powershell
Copy-Item -Path \\share\fonts\FZXBSJW.TTF -Destination "$env:WINDIR\Fonts\"
```

## TODO (鸿波明天填)

- [ ] **reference.docx** — 拿一份真实公司汇报模板放到这里, 给 LLM 当样板参考
- [ ] **方正小标宋简体.ttf** — 装到 Mac demo 机, demo 时本地预览也能显示对的字体
- [ ] **公司部门列表** — `department/` 字段的常用值 (例: "办公室 / 人力资源部 / 法务合规部 ..."), 让 LLM 自动补全
- [ ] **公司日期格式偏好** — "2026 年 4 月 30 日" vs "二○二六年四月三十日" (大写汉字格式), 公司风格选一种钉死
- [ ] **错别字二审**: pip install pycorrector 后, _maybe_audit 自动跑; 5 月 demo 前装上

## demo 演示话术

> 跟客户说: "员工在群里收到通知 '5 月 8 日前提交关于 XX 的请示件', 员工对着鲶鱼说一句话, 鲶鱼自动调 leadership-briefing skill, 输出 .docx 字体合规, 4 段齐全, 员工只 review 数字按发送."

实际 demo 操作:

1. 跟鲶鱼说: "写一份关于本部门 EIS 数据更新滞后的请示件, 发给办公室 X 主任"
2. 鲶鱼问 5 个问题 (背景 / 问题 / 推荐方案 / 备选 / 请示什么), 员工逐条回答
3. 鲶鱼调 leadership-briefing.render_briefing(...) 生成 .docx
4. ChatToolCall 里出现 `📄 EIS-请示件.docx [在 Finder 显示] [打开]` pill
5. 员工点 "打开" → Word 启动 → 字体 / 4 段全对 → 改两个字按发送
