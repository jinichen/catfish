# BL-LEARN-RECMODE V1 mock 验证 (5/14 20:10, 提前 5 天验)

> **目的**: 不等 5/19 真种子数据, 用现有 EIS 截图 + 手写 mock events/语音 验证 V1 关键假设:
> **catfish-private-main 真能不能看懂中文 UI 截图 + 推出"用户在 EIS 检查资质"的语义?**
>
> **不通过** → 整套 BL-LEARN-RECMODE 设计返工 (加 vision 30B 辅助 / fall back gemini-pro / 加分段)
> **通过** → 整套设计可行, 5/19 真数据来后只调 prompt 收尾

---

## 鸿波本机操作 (5 分钟)

1. 打开 **Companion**, 新会话
2. 把下面"喂给鲶鱼的 prompt" 整段贴进 chat input
3. **同时上传 2 张 EIS 截图** (📎 附件):
   - 截图 A: EIS 首页, **点了"应用" tab** 看到 12 个图标网格 (含资质管理图标)
   - 截图 B: 资质管理 → 企业资质列表页 (10 条 / 页, 显示证书有效期列)
4. 发送 → 等鲶鱼回输出
5. **拷贝鲶鱼输出贴回这里发我** (我看输出质量判 V1 通过/不通过)

---

## 喂给鲶鱼的 prompt (整段贴, 含附图)

```
[V1 mock 验证 — 你不要真做, 只演示能不能从录屏数据生成 skill]

我在测一个新功能 BL-LEARN-RECMODE: 用户录屏 + 语音教学, 后端 LLM 综合
events + 截图 + 语音转写 → 自动生成 catfish skill (SKILL.md + main.py).

下面是模拟的录屏数据 (1 个 5 分钟的 EIS 资质检查教学), 你扮演后端 LLM
看完这些数据 → 输出 SKILL.md 草稿 (按下面 schema), 测一下你能不能理解.

# 输入数据

## 截图 (按 timestamp 升序)
[附件 1] keyframe_001 — ts=0.0, EIS 首页应用 tab
[附件 2] keyframe_002 — ts=18.5, 企业资质列表 (跳过中间点资质管理图标 / 点企业资质菜单 2 步)

## events JSONL (按 ts 升序, 简化 8 条)
{"ts": 0.0,  "kind": "page_navigated", "url": "http://eis.ffcs.cn/",
 "title": "EIS 首页", "screenshot_id": "kf_001"}
{"ts": 3.2,  "kind": "click", "text": "应用",
 "selector_record": "div.tab-app", "near_text": "通讯录"}
{"ts": 5.8,  "kind": "dom_changed",
 "diff_summary": "tab-content 切换, 新增 12 个 .app-icon"}
{"ts": 8.4,  "kind": "click", "text": "资质管理",
 "selector_record": "a[href='/qual']"}
{"ts": 12.1, "kind": "page_navigated",
 "url": "http://eis.ffcs.cn/qual", "title": "资质管理"}
{"ts": 14.6, "kind": "click", "text": "企业资质",
 "selector_record": ".side-menu li:nth-child(2)"}
{"ts": 18.5, "kind": "dom_changed",
 "diff_summary": "右侧表格出现, 10 行, 列: 序号/企业资质名称/公司名称/证书编号/发证中心/发证时间/证书有效期/等级/维护人",
 "screenshot_id": "kf_002"}
{"ts": 35.2, "kind": "long_pause", "duration_s": 16.7,
 "screenshot_id": "kf_002"}

## 语音转写 (按 ts 对齐)
{"ts": 1.5,  "text": "我现在演示企业资质检查"}
{"ts": 4.0,  "text": "点应用这个 tab"}
{"ts": 9.0,  "text": "进资质管理"}
{"ts": 15.0, "text": "看左边企业资质这一项"}
{"ts": 20.0, "text": "重点看证书有效期这一列"}
{"ts": 24.0, "text": "跟今天比, 离到期不到 90 天就要标记出来续期"}
{"ts": 30.0, "text": "我们公司有 7 页 63 条要翻完"}

# 输出要求

输出 JSON, 严格按 schema:

{
  "skill_name": "<snake_case 名字>",
  "namespace": "<department / personal / public>",
  "description": "<一句话, 给 LLM skill_catalog 用>",
  "intent_summary": "<3-5 句话的用户意图描述, 你从语音 + events 综合理解>",
  "params_schema": [
    {"name": "...", "type": "...", "default": ..., "description": "..."}
  ],
  "steps": [
    {
      "step_no": 1,
      "intent": "<人话: 这一步在干嘛>",
      "tool": "<catfish_browser_navigate / find_by_text / click / execute_code>",
      "args_template": {<tool 参数, 用 {param} 引用 params_schema>},
      "selector_hint": {
        "text": "...",
        "near_text": "...",
        "role": "..."
      },
      "expected_after": "<这步跑完应该看到啥>"
    }
  ],
  "execute_code_segment": "<最后一步: Python 代码翻所有页 + 解析表格 + 找 < days_threshold 天到期>",
  "output_schema": {
    "report_path": "string",
    "expiring_count": "number",
    "expiring": "list[dict with name/maintainer/expire/days_left]"
  },
  "confidence": <0-1 你对这个 skill 自动跑成功的把握>,
  "questions_for_user": ["<不确定 / 需用户 confirm 的点>"]
}

# 注意

1. selector 不要硬编码 — 用 selector_hint.text + near_text, skill 跑时
   实时 find_by_text (DOM 改了能跟上)
2. 数据提取走 execute_code (BeautifulSoup 解析表格)
3. params_schema 通用化: 不要硬编码 "chenhb" / "90 天", 让 skill 通用
4. 你看不全的直接写 questions_for_user, 别瞎猜

开始.
```

---

## 鸿波贴回输出后, 我看 5 个判断点

| 点 | 通过 = | 不通过 = |
|---|---|---|
| **P1: skill_name 合理** | `eis_qualification_check` 类 | 乱写 / 抄输入 |
| **P2: intent_summary 抓住"找快过期"** | "扫所有页找证书有效期 < N 天的资质" | 误解为 "翻页演示" |
| **P3: steps 步骤序对** | navigate → click 应用 → click 资质管理 → click 企业资质 → execute_code 翻页 + 提取 | 漏步 / 重复 / 顺序错 |
| **P4: selector_hint 不硬编码** | 用 text + near_text | 复用了我输入的 `selector_record: "div.tab-app"` (说明 LLM 没理解纪律) |
| **P5: execute_code 段含 BeautifulSoup + 翻页 + 日期 diff** | 真实可跑代码 | 伪代码 / 占位 |

5 点全过 → V1 PASS, 整套 BL-LEARN-RECMODE 路径可行
3-4 点过 → V1 PARTIAL, 可接受但要调 prompt
< 3 点过 → V1 FAIL, 要 audit 是不是要 fall back gemini-pro / 加 vision 辅助 / 加 few-shot

---

## 同时我做的事 (你贴 prompt 时并行)

写 `central/llm-gateway/src/catfish_gateway/recmode/cdp_listener.py` 骨架 — 连 ws://localhost:9222 监听 events 落 JSONL. 不依赖 V1 结果, 即使 V1 不通过这部分代码也复用 (events 捕获跟 main 模型理解是分开的).

CDP listener ship 后, 你 5/19 录屏时我们就有真 events 数据了 (而不是 macOS 屏幕录制 mp4 — 那个解析复杂).
