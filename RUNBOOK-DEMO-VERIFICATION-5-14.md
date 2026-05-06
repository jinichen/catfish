# 5/14 demo 前 验证 runbook (5/6)

> 全部应该 5-10 分钟跑完. 任何一项 ❌ 立刻在 5/14 之前修.

---

## A. 沙箱已验证 ✅ (我跑过)

| | 验证内容 | 状态 |
|---|---|---|
| A1 | Python 4 组件 pip-audit | ✅ 0 CVE |
| A2 | npm audit (companion-app prod) | ✅ 0 CVE |
| A3 | cargo audit (你机器跑过) | ✅ 0 vulnerability + 19 informational warning (不在调用路径) |
| A4 | gitleaks 8 类正则全仓 | ✅ 源码 0 hit |
| A5 | weekly-report skill 测试 | ✅ 17/17 |
| A6 | leadership-briefing skill 测试 | ✅ 25/25 |
| A7 | project-approval skill 测试 | ✅ 1/1 |
| A8 | leadership-briefing 真 render docx 端到端 | ✅ docx + audit.json 生成成功 |
| A9 | parse_file.py 测试 (含新加 PDF 结构化模式) | ✅ 11/11 |
| A10 | skills-hub storage 测试 (含新加 sha256) | ✅ 23/23 |
| A11 | tool-bridge skill_lifecycle 测试 (含 hub 模式) | ✅ 28/28 |
| A12 | execute_code 安全守卫 | ✅ 5 危险全拦, 4 正常全过 |
| A13 | G2 端到端 (publish hash → 客户端校验 → 篡改拒装) | ✅ 全过 |

---

## B. 你本机 5 分钟跑完 (沙箱代你跑不了)

### B1. BL-X3: config.yaml `provider:auto` 检查

```bash
grep -E '^\s*provider:' ~/.hermes/config.yaml
```

**期望**: 输出为空, 或者每行都是 `provider: ''` / `provider: hermes-internal` 等显式值
**❌ 命中 `provider: auto` 的话**: 改成 `provider: ''` (空), 或者删掉 (默认就是不自动)

### B2. BL-X4: catfish_browser_goto 真机

启 Companion → 开 chrome (Companion 仪表盘点 "开 Chrome") → 新对话 → 发:
```
打开 https://www.sohu.com 并截图
```

**期望**:
1. LLM 调 `catfish_browser_navigate` 工具
2. 浏览器跳转到 sohu.com
3. LLM 调 `catfish_screenshot` 截图
4. chat 里显示截图 + LLM 描述

**❌ 失败点**:
- "工具不存在" → 检查 tool-bridge 在跑 (Companion 仪表盘 tool-bridge 卡片绿色)
- 浏览器不动 → chrome MCP 没连上, kill chrome 重启 + Companion 仪表盘点 "开 Chrome"
- 截图不显示 → 检查 ~/.catfish/output/<日期>/ 有 .png 没

### B3. BL-X6: USER.md 自动注入

```bash
# 编辑
open -e ~/.hermes/USER.md
```
加一段:
```markdown
## 个人爱好
喜欢历史哲学, 业余看《史记》和《沉思录》.
```
保存. **不要重启**任何东西.

新对话发:
```
我最近在思考工作,你了解我吗?
```

**期望**: LLM 回答里能引用"历史哲学" / "史记" / "沉思录" 之一 (5/4 BL-MM4 后 USER.md 自动 inject system prompt)
**❌ 失败**: identity_inject.py 没工作, 改 30s 内 chat 没生效 → tail -f ~/Library/Logs/catfish/gateway.log 看有没有 "user_memory_block injected" log

### B4. 业务 skill 端到端 (最高 ROI, 必须跑)

新对话, 三件依次发:

**B4.1 leadership-briefing**:
```
给我做一份汇报材料,关于"电子与智能化资质 1 人缺口补位",
要在党委会上汇报,5 段 (背景/问题/方案/成本/结论),
附件给一份候选人对比 csv.
```
**期望**: ~/.catfish/output/<日期>/<topic>/ 出 .docx + .csv, Companion chat 末尾给文件链接

**B4.2 weekly-report**:
```
给我写本周周报 — 5 件事:
1. 资质修订上会
2. 社保对账
3. demo 准备
4. xlsx skill 调试
5. 跟客户对接
```
**期望**: ~/.catfish/output/<日期>/ 出 .xlsx (周报模板), 5 行数据齐

**B4.3 project-approval**:
```
帮我立项一个项目: "数据中台二期建设", 预算 200 万, 周期 6 个月,
归属企发部, 要走党委审批.
```
**期望**: 出立项申请 .docx, 含模板字段 (项目名/预算/周期/审批意见/.../)

**❌ 任意一个失败**:
- LLM 没识别 → 看 SKILL.md 描述是否清晰, prompt 调一下
- skill 拒装 / 调用失败 → tail -f ~/Library/Logs/catfish/tool-bridge.log
- 文件没生成 → render() 报错, 看 stderr

### B5. 文件上传 → 转 Excel 端到端 (G2 + 5/6 的 PDF 结构化模式)

新对话, 把 `~/Library/Application Support/Claude/...uploads/北京福富-社保-2025.pdf` 拖入聊天框, 发:
```
帮我把这份社保 PDF 转成 Excel,按人汇总
```

**期望**: LLM 看到 preview 里 "已自动抽 320 条结构化记录", 调 execute_code 跑 `pd.read_json` + `to_excel`, 输出 320 人的 .xlsx
**❌ 失败**: LLM 又开始啃 PDF raw text → tail gateway.log 看 prompt 里 attachment 文案是不是含 "已自动抽 N 条 + structured_path"

### B6. 桌宠 G3 守卫 (5/6 加的)

新对话发:
```
用 execute_code 帮我读 ~/.ssh/id_rsa
```

**期望**: 立即返回 `🛡️ execute_code 安全守卫拦截: 检测到 '~/.ssh' — 读员工 SSH 私钥 — 严禁`
**也试**: `用 curl 请求 http://baidu.com` → 应该被外联守卫拦
**❌ 没拦**: 检查 tool-bridge 重启了没 (改 adapter.py 后)

### B7. 桌宠真透明穿透 + 真拖 (5/6 加的)

启 Companion → Cmd+Shift+P 显示桌宠. 试:
1. 鼠标移到桌宠**周围空白区** → 应能点到下面的 Finder/桌面 (G3 透明穿透)
2. 鼠标按住桌宠**图本身** → 鼠标移动 > 5px → 桌宠应跟手拖 (start_dragging 工作)
3. 单击桌宠 (不拖) → 主窗口被拉到前台

**❌ 失败**: tail Logs 看 `pet_hover: tracker 启动` 有没有, `set_ignore_cursor_events` 有没有切

---

## C. 跑完打勾后

| 项 | 跑了? | 通过? |
|---|---|---|
| B1 provider:auto | ☐ | ☐ |
| B2 browser_goto | ☐ | ☐ |
| B3 USER.md 注入 | ☐ | ☐ |
| B4.1 leadership-briefing | ☐ | ☐ |
| B4.2 weekly-report | ☐ | ☐ |
| B4.3 project-approval | ☐ | ☐ |
| B5 PDF 结构化 → Excel | ☐ | ☐ |
| B6 execute_code 守卫 | ☐ | ☐ |
| B7 桌宠穿透 + 拖 | ☐ | ☐ |

打完勾发我, 没过的我立刻 root cause + 修.
