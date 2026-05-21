# BL-LEARN-RECMODE: 录屏+语音教学引擎设计 (v1, 5/14 2:15 起草)

> **维护人**: 鸿波 / Catfish 项目
> **状态**: ⬜ 待评估 / 待 5/19 关键假设验证
> **关联 task**: #59 BL-LEARN-RECMODE
> **触发**: 5/14 1:50 鸿波看完 eis-checkin / eis-qualification-check 12 句教学剧本后反思 "现在这种教学方式很不人性, 也很难复制"

---

## 1. 问题陈述

### 1.1 现状

catfish 当前的 skill 教学流程 (`catfish_teach_start` → 用户给步骤 → `catfish_freeze_skill`) 要求用户写**精确剧本**, 含 catfish 内部 API 名:

```
句 3 (找上班打卡按钮):
> 调 catfish_browser_find_by_text(text="上班打卡", role="button")

句 4 (点上班打卡):
> 调 catfish_browser_click(selector="<上一步 selector>")
```

### 1.2 三个根本问题

1. **用户被迫学 catfish 内部 API** — `catfish_browser_find_by_text(text=..., role=...)` 这种语法跟 "我教你做事" 心智模型完全不匹配. 普通员工 (HR / 财务 / 销售) 看到这种语法就不学了
2. **selector 漂移没人修** — freeze 时记录 `div.tab-app` 这种具体 selector, DOM 一改 skill 就废, 普通用户不会修, 来找鸿波 / 工程师修, 不可复制
3. **拆步抽象只懂代码人能做** — 用户能写 "12 句剧本" 是因为脑子里能拆 "意图 → find_by_text + click + extract" 这套 tool call 抽象, 这个抽象**只开发者有**, 央企非工程岗 99% 不会

### 1.3 跟 catfish 产品定位的冲突

catfish 是 **FFCS / 央企非开发者员工自助平台**. 但当前 skill 教学只有开发者能用 → 跟产品定位反着走.

如果 catfish 真要在 FFCS 推广 ("HR 教鲶鱼自动跑 onboarding 流程", "财务教鲶鱼月底跑报表"), 教学这一关绕不过去.

---

## 2. 目标

**让非开发者员工通过录屏 + 顺嘴说意图, 5-10 分钟教鲶鱼学会一个新流程.**

具体形态:

1. 用户在 Companion 点 "🎙 开始 RecMode 教学" 按钮
2. 用户**正常操作 Catfish Chrome** (登 EIS / 点应用 / 翻页 / 看数据)
3. **顺嘴说**当前在干嘛 ("现在点这是为了进资质管理 / 这个数字判 90 天内")
4. 用户操作完点 "✅ 完成教学"
5. catfish 后端自动综合 (events + 语音转写 + 截图序列) → 生成 `~/.catfish/skills/<namespace>/<skill_name>/SKILL.md` + `main.py`
6. 跑一次让用户看效果, OK 就 freeze 落档
7. 之后用户一句"做 X 流程" → 鲶鱼调 skill 自动跑

**用户全程不写一行代码, 不指定一个 selector, 不学一个 API 名.**

---

## 3. 架构

### 3.1 Pipeline 总览

```
[用户操作 Catfish Chrome + 语音说意图]
         │
         ├──> CDP listener (后端)         ──> events JSONL
         │    Page.frameNavigated, DOM mutations,
         │    Input.*, Page.screencastFrame
         │
         ├──> ffmpeg + whisper.cpp (本地)  ──> 语音转写 + timestamps
         │
         └──> Companion 前端                ──> 截图 keyframe (DOM 大变化时刻)
                                              抽样
         │
         ▼
[keyframe 抽取]                      ← 减 token 70% (50 张 → 10-15 张)
         │
         ▼
[catfish-private-main 一次推理]       ← 单模型, 128K context, 内网
   输入: keyframes (vision) +
         events JSON (摘要) +
         语音转写 (timestamps 对齐 events)
   输出: 步骤分解 + selector 策略 +
         数据提取规则 + 错误兜底
         │
         ▼
[SKILL.md + main.py 落档]
   ~/.catfish/skills/<namespace>/<skill>/
         │
         ▼
[用户 review + 跑一次验证 + freeze]
```

### 3.2 events 捕获方案 (CDP, 不要 Chrome 扩展)

**决策**: 走 **CDP** (Chrome DevTools Protocol), 不走 Chrome 扩展.

#### 三种方案对比

| 方案 | 优点 | 缺点 | 决策 |
|---|---|---|---|
| **A. Chrome 扩展** (manifest v3) | 干净, 浏览器原生事件 | 要装扩展, 用户被迫操作; central 推送扩展 IT 麻烦 | ❌ 不选 |
| **B. CDP listener** (走 ws://localhost:9222) | **复用 Catfish Chrome 已开 CDP** (BL-CHROME), 不要装扩展, 后端纯 Python | 要 Catfish Chrome 模式 (现状默认就是) | ✅ **选这个** |
| C. hermes-cli wrap browser_navigate | 最自动, 跟现有 catfish_browser_* 工具天然对齐 | 用户用原生浏览器 (没调 catfish 工具时) 不行 | ❌ 不选 (覆盖面窄) |

#### CDP 订阅事件

```python
# 订阅这些 CDP domain
await session.send("Page.enable")
await session.send("DOM.enable")
await session.send("Runtime.enable")
await session.send("Input.setInterceptDrags", {"enabled": False})

# 监听
session.on("Page.frameNavigated", on_navigated)       # URL 切换 = keyframe 触发
session.on("DOM.documentUpdated", on_dom_changed)     # 大 DOM 变化 = keyframe 触发
session.on("Page.javascriptDialogOpening", on_dialog) # alert/confirm 弹窗
session.on("Network.responseReceived", on_response)   # API 调用 (用户操作引发的, 推断业务逻辑)
```

#### 截图 keyframe 抽取

不录每帧 (太多), 按 keyframe 触发:

1. **URL 切换时** 截一张 (page navigation)
2. **DOM 大变化时** 截一张 (e.g. 弹窗 / 翻页 / tab 切换), 用 mutation observer 节流 500ms
3. **长停顿时** 截一张 (用户停 3+ 秒, 可能在看数据)
4. **用户语音 keypoint** 触发 ("现在 / 这里 / 看这个" 等指代词在语音转写里出现时)

50 张 keyframe → 实际可能 10-15 张 (5-10 分钟录屏). 对 main 模型 128K context 友好.

### 3.3 模型选型

**catfish-private-main 单模型一气呵成** (5/14 2:00 鸿波 audit 修正过, 不要双模型).

| 模型 | 用途 | 现状 |
|---|---|---|
| **catfish-private-main** (Qwen3.5 122B MoE A10B) | 截图理解 + events 推理 + 代码生成, 一气呵成 | ✓ `supports_vision: true` (5/8 鸿波亲验), `context_window: 128000` |
| **whisper.cpp** | 语音转写 + timestamps | ✓ BL-VOICE3 5/10 ship, 本地零网络 |

**为什么不切到 catfish-private-vision (30B A3B)**:

- main 122B 推理 + 代码生成强, vision 30B 弱一档
- 双模型分段需要"段间中转", 信息损失
- main context 128K, vision 64K, main 一次能撑下更多

**vision 何时备份**:

- main OCR 看错小字时 (验证码 / 章戳 / 表格细数字), 单段切到 vision (专 VL 训练) 重看
- 大量截图 batch (100+), vision 30B 比 122B 快一档 — 但 keyframe 抽取后基本用不到

### 3.4 token 算账 (一次喂得下吗)

5-10 分钟录屏:

| 组件 | token 估算 |
|---|---|
| keyframe 截图 10-15 张 | 15 × 1500 = **22.5K** |
| events JSON (摘要后) | **5K** |
| 语音转写 (中文) | **2K** |
| 上下文 (catfish skill 范式 prompt) | **5K** |
| **总输入** | **~35K** |
| **输出预算** (SKILL.md + main.py) | **20K** |

128K context 一次能撑下, **不需要分段处理**. 20 分钟+ 长录屏才需要切.

---

## 4. 数据 schema

### 4.1 events JSONL

```jsonl
{"ts": 1747194300.123, "kind": "page_navigated", "url": "http://eis.ffcs.cn/", "title": "EIS 首页"}
{"ts": 1747194302.456, "kind": "click", "selector": "div.tab-app", "text": "应用", "screenshot_id": "kf_002"}
{"ts": 1747194302.890, "kind": "dom_changed", "diff_summary": "tab-content updated, 新增 12 个 .app-icon"}
{"ts": 1747194305.111, "kind": "click", "selector": "a[href='/qual']", "text": "资质管理", "screenshot_id": "kf_003"}
{"ts": 1747194310.999, "kind": "long_pause", "duration_s": 3.2, "screenshot_id": "kf_004"}
```

**关键字段**:
- `ts`: epoch 浮点秒, 跟语音转写 timestamps 对齐
- `kind`: page_navigated / click / input / dom_changed / long_pause / network_response
- `selector`: 录制时的 CSS selector (skill 跑时**不直接用**, 只作 LLM 推理参考 — 跑时重新 find_by_text)
- `text`: click 元素的可见文本 (find_by_text 跑时用这个找, 比 selector 鲁棒)
- `screenshot_id`: 关联 keyframe 截图 (`~/.catfish/recordings/<session>/screenshots/<id>.png`)

### 4.2 语音转写 JSONL

跟 BL-VOICE3 现有 whisper.cpp 输出格式对齐, 加 timestamps:

```jsonl
{"ts": 1747194301.5, "duration": 2.1, "text": "现在我点应用 tab"}
{"ts": 1747194303.8, "duration": 1.8, "text": "进资质管理"}
{"ts": 1747194305.2, "duration": 1.2, "text": "看企业资质这一项"}
{"ts": 1747194310.5, "duration": 4.5, "text": "我现在看证书有效期 跟今天比小于 90 天的就是要续期的"}
```

LLM 综合时, 把语音转写跟 events JSONL 按 ts 对齐, 知道 "用户说这句话时正在做什么操作".

### 4.3 keyframe 截图

存路径 `~/.catfish/recordings/<session_id>/screenshots/<keyframe_id>.png`

- PNG 格式, 浏览器视口 1920x1080 (高分辨率截图, 给 main 模型看清细节)
- 文件名 `kf_<seq>.png` (e.g. `kf_001.png`, `kf_002.png`)
- events JSONL 里 `screenshot_id: "kf_002"` 引用这个

session 跑完后, **隐私敏感**: 截图含业务数据 (EIS 资质 / 个人信息). 默认 14 天后自动删, 用户可标"保留" 作 ground truth.

---

## 5. main 模型综合 prompt 模板

```
你是 catfish-skill-author. 用户刚录了一段教学过程 (events + 语音 + 截图).
你的任务: 综合理解用户在教什么流程, 输出可重现的 SKILL.md + main.py.

# 输入

## 用户意图 (从语音 keypoint 提取的最高级目标)
{intent_summary}  # e.g. "教 EIS 企业资质过期检查流程"

## 时间线 (events + 语音对齐)
{timeline}  # JSONL, 按 ts 排序, 每行含 ts + kind + 内容

## keyframe 截图 (关键时刻)
{keyframes}  # 10-15 张 base64 PNG, 按 keyframe_id 排序

# 输出要求

输出 JSON, 严格 schema:

{
  "skill_name": "<snake_case, 名字>",
  "namespace": "<department / personal / public 选一>",
  "description": "<一句话, 给 LLM 看 skill catalog 用>",
  "params_schema": [
    {"name": "...", "type": "...", "description": "...", "required": true/false}
  ],
  "steps": [
    {
      "intent": "<人话: 这一步在干嘛>",
      "tool": "<catfish_browser_navigate / catfish_browser_find_by_text / catfish_browser_click / catfish_execute_code / catfish_browser_get_page_html / etc>",
      "args": {<tool 参数>},
      "expected_screenshot": "<keyframe_id>",  // 验证用
      "selector_hint": {"text": "...", "role": "...", "near_text": "..."}, // 跑时重新 find, 不用录制 selector
      "code_snippet": "<execute_code 用, Python 字符串, 含 BeautifulSoup / 翻页循环 / 数据提取>",
      "error_handling": "<如果这一步跑失败, 怎么办: skip / retry / abort>"
    }
  ],
  "output_schema": {
    "report_path": "string",     // 跑完输出哪个文件
    "data_summary": "object"      // 关键数据返给 LLM
  }
}

# 关键纪律

1. **selector 不要硬编码** — 用 selector_hint (text + role + near_text), skill 跑时调 catfish_browser_find_by_text 实时找
2. **数据提取走 catfish_execute_code + BeautifulSoup** — 不要硬编码 ".table tr td:nth-child(3)" 这种 brittle selector
3. **每步加 expected_screenshot** — skill 跑时截图对比, 视觉差异大就报错 (DOM 大改 / 业务数据完全不同)
4. **params_schema 要有 username / 阈值** — 避免硬编码 "chenhb" / "90 天", 让 skill 通用
5. **steps 必含至少一步 LLM-only (return data 给 LLM 决策)** — 不绑死下游动作 (e.g. 不在 skill 里循环创建日历事件, 留给 LLM 看结果决定)
```

---

## 6. UI/UX

### 6.1 Companion 加 🎙 RecMode 教学按钮

跟现有 🎓 教学模式 / 🔄 自动续跑 / 🎤 语音 按钮并排 (chat input toolbar):

```
[📎] [🎓] [🔄] [🎤] [🎙 RecMode] [textarea] [发送]
```

点 🎙 RecMode 弹对话框:

```
┌─ 录屏教学 RecMode ─────────────────────┐
│ 名字: ___________________  (snake_case) │
│ namespace: [department ▼]               │
│ 简述: _____________________________    │
│                                         │
│ [开始录屏 + 录音]  [取消]              │
└─────────────────────────────────────────┘
```

点开始 → 进 recording 状态:

```
┌─ 🔴 录屏中 ─────────────────────────────┐
│ ⏱ 0:34  📸 8 keyframes  🎤 录音中     │
│                                         │
│ 现在你切到 Catfish Chrome 正常操作.    │
│ 顺嘴说意图 (鲶鱼会理解你说啥).         │
│                                         │
│ [✅ 完成教学]  [❌ 取消放弃]           │
└─────────────────────────────────────────┘
```

完成 → 弹"分析中" loading → 出来 SKILL.md preview + "跑一次试" / "保存" / "重录".

### 6.2 RecMode 状态机

```
idle → (点 🎙) → setup (填名字 / namespace)
       │
       ▼
recording → (点 ✅ 完成) → analyzing (后端综合)
       │                      │
       │ (点 ❌ 取消)          ▼
       ▼                   preview (SKILL.md 草稿 + 跑一次按钮)
   discarded                  │
                              ├─ "跑一次试" → running → result
                              ├─ "保存" → freeze → ~/.catfish/skills/...
                              └─ "重录" → recording (重新)
```

---

## 7. SKILL 输出格式 + freeze 关系

### 7.1 文件结构

```
~/.catfish/skills/<namespace>/<skill_name>/
├── SKILL.md              # 人话描述 + 触发关键词 + params 文档
├── main.py               # 实际跑的 Python (catfish_browser_* 调用 + execute_code 段)
├── recmode_meta.json     # 录制元数据 (ts / events 路径 / 模型 / context 用量)
└── examples/             # 录制时的 keyframe (作 ground truth)
    ├── kf_001.png
    ├── kf_002.png
    └── ...
```

### 7.2 跟现有 catfish_freeze_skill 的关系

`catfish_freeze_skill` (现有, 走 catfish_teach_start + catfish_teach_end 流程) 输出的 skill 文件结构跟 RecMode 兼容. RecMode 只是另一个**生成入口** — 都是输出标准 skill 文件.

跑 skill 的运行时 (`catfish_run_skill`) **完全不知道**这个 skill 是手写还是 RecMode 生成的, 一视同仁.

### 7.3 selector 漂移自动修复

skill 跑时:

1. 读 `steps[i].selector_hint = {text: "应用", role: "tab", near_text: "通讯录"}`
2. 调 `catfish_browser_find_by_text` 实时找 (不用录制时的 `div.tab-app`)
3. 找不到 → 截图当前页面 + 调 main 模型 vision "这个截图里, '应用' tab 在哪儿? 给 selector"
4. 仍找不到 → 报错 "DOM 跟教学时不一致, 你帮看看", 弹截图给用户

DOM 改了, skill 自适应跟上, **用户不用修 skill**.

### 7.4 隐私路径硬约束 (5/21 加, 防自动共享)

教学产物的隐私风险**远高于一般手写 skill**: 录屏可能含员工本人脸/工位、其他员工身份证号/姓名/工号、内部 URL、部门 know-how. 落地必须走双层路径, **绝不自动上传中央 Skills Hub**:

#### 双层路径

```
教学 freeze
   │
   ├── 默认落本机 ~/.catfish/skills/<ns>/<name>/   ← 永不出员工本机
   │     │
   │     └── 通过 hermes 自带 skills.external_dirs 配置注册 (config.yaml)
   │         → hermes registry 自动扫到, LLM 看得到调得到
   │         → external_dirs 在 Curator 扫描范围外, 不会被 archive
   │         (实现: catfish_tool_bridge.skill_register.ensure_external_dir_registered)
   │
   └── 员工显式点 "📤 发布到团队 Hub" 按钮 ← 主动行为, 不自动
         │
         ├── publish 前 3 道扫描必跑:
         │   (a) BL-D2 凭据扫描 (6 类正则, 5/10 已 ship)
         │   (b) PII 扫描: 录屏 keyframe 跑 vision 检测身份证号/姓名/工号; 转写文本跑 NER (jieba + regex)
         │   (c) 内网 URL/hostname 扫描: regex 黑名单 (10.*. / 192.168.* / *.corp.* / *.internal.*)
         │
         ├── 任何一道命中 → 拒上传 + 显示具体命中位置, 让员工确认是否脱敏后重传
         │
         └── 通过 → 走 Skills Hub 审核流 (manager publish → admin approve → live)
                   approved 前对其他员工不可见
```

#### 硬约束 (LLM 写代码时强制遵守)

| 约束 | 反模式 (LLM 不许做) |
|---|---|
| 教学 freeze 后**默认**落 `~/.catfish/skills/`, **不**自动 publish | freeze 末尾直接调 `catfish_skill_publish` |
| publish 必须**员工显式点按钮**触发, **不**走 tool call | LLM 自己调 `catfish_skill_publish` 当作 freeze 一部分 |
| publish 前 3 道扫描全过才发起请求 | 跳过扫描直接 POST 到 hub |
| publish 弹窗必须明确警告 "会被部门/全公司看到" + 列出 3 道扫描结果 | 静默 publish, 不给员工 second-confirm 机会 |
| 教学产物 metadata 加 `from_teaching: true` | publish 时 strip 这个字段 (避免 admin 看不出来源) |

#### 跟 #11 "部门级 skill auto-推" 的边界

`#11 ⬜ 部门级 skill auto-推 · 1-2 周` 这条 backlog 是潜在"自动共享"风险点. ship 前必须加约束:

- **不是 publish 就推**: 员工 publish → 进个人 namespace (`<email>/<name>`), 不进部门清单
- **Admin pull, 不是 auto-push**: admin 在 Web UI 看部门高频 skill list, 一键加进部门必装清单 (跟 Hub 审核流 manager → admin 一致)
- **教学产物强制二次审核**: metadata `from_teaching: true` 的 skill 不能直接进部门必装清单, 必须 admin 在 Web UI 单独审 (因 PII / 内网信息泄漏后果更严重)

---

## 8. 5/19 验证计划 (鸿波周一手动检查 EIS 资质时录屏 + 语音)

### 8.1 录制清单

鸿波 5/19 上午检查 EIS 企业资质时:

1. macOS 屏幕录制 (Cmd+Shift+5) 录全程, 含鼠标 + 系统声音
2. Companion 🎤 同时录语音 (whisper 转写)
3. 走完正常流程: 登 EIS → 应用 → 资质管理 → 企业资质 → 翻 7 页 → 找快过期 → (人话说) "这个 11 月 30 日的, 离今天 6 个月还行" / "如果有 90 天内的我就要标"
4. 录完手动发给我 (mp4 + Companion 语音 jsonl)

### 8.2 验证点 (4 个, 5/19 当天 1 小时验)

| 验证点 | 怎么验 | 通过标准 | 不通过怎么办 |
|---|---|---|---|
| **V1: main 模型理解中文 UI 截图** | 拿 1 张 EIS 截图 + 5 个 mock events + 1 句语音 喂 main, 让它输出 "这是在做什么" 的 200 字描述 | 描述准确含 "EIS / 资质管理 / 企业资质" 关键词 + "用户在点应用 tab 进入资质管理" 这种因果推理 | fall back 加 vision 30B 辅助 OCR; 或 audit gemini-pro 公网模式 (个人版) |
| **V2: events 序列推理** | 拿完整 events JSONL (10-15 events) + 截图 keyframes 喂 main, 让它输出 "用户演示了 ABCD 4 步" | 步骤分解准确, 跟鸿波语音意图 90% 对齐 | 加 events 摘要预处理 (LLM 看不懂原始 CDP events 的话, 加翻译层) |
| **V3: SKILL.md 草稿质量** | 让 main 输出完整 SKILL.md JSON (按 §5 schema), 看可读性 + selector_hint 准确 + execute_code 段可跑 | 跑一次成功率 > 50% (允许小修) | 调 prompt 模板 / 加 few-shot 例子 |
| **V4: token 用量真在 128K 内** | 真录 10 分钟, 实际跑一次, 算 prompt_tokens | < 90K (留 38K 输出空间) | keyframe 抽取算法收紧 (5 张/分钟改 3 张/分钟); 长录屏走分段 |

### 8.3 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| **R1: main 模型推不出 selector_hint 不写硬 selector** | 中 | 高 (skill 跑就废) | prompt 强约束 + few-shot + 后端校验 (输出 selector 就拒绝重生成) |
| **R2: 语音跟 events ts 对不齐 (whisper.cpp ts 偏移)** | 中 | 中 (LLM 不知道用户说哪句对应哪步) | 后端用 dynamic time warping 对齐, 或显式 prompt 让 LLM "尽量对齐, 不准确就标 unknown" |
| **R3: keyframe 抽取漏关键时刻** (e.g. 用户演示某个细节没触发 keyframe) | 中 | 中 (skill 漏一步) | 加 "用户长停顿 + 指代词" 双触发条件 |
| **R4: 隐私 — 截图含业务数据** | 高 (但内网模型是已知 trade-off) | 中 | 截图 14 天自动删, 用户可"保留" 标 ground truth, 中央 PG 不存截图原图只存 hash |

---

## 9. Sprint plan 拆解 (3-4 天)

5/19 V1 验证通过后开干. 否则推迟评估.

| 日期 | 项 | 工作量 |
|---|---|---|
| **Day 1 (5/26)** | CDP listener 后端 (Python asyncio + websockets) — 连 ws://localhost:9222 监听 events, 截图 keyframe 抽取算法, 落 JSONL + PNG 到 `~/.catfish/recordings/` | 4-6h |
| **Day 1 同时** | Companion RecMode UI 按钮 + 状态机 + 启停 ffmpeg 录音 (复用 BL-VOICE3) | 4h |
| **Day 2 (5/27)** | gateway endpoint `/api/learn/start` + `/api/learn/stop` + `/api/learn/analyze` 接 Companion 启停信号 + 触发后端综合 | 3h |
| **Day 2 同时** | main 模型 prompt 模板写完 + 5 个 few-shot 例子 (从 5/19 鸿波录像扒) | 4h |
| **Day 3 (5/28)** | 端到端联调 — Companion → CDP → 录屏 → whisper → main 综合 → SKILL.md 落档. 跑通 eis-qualification-check 一遍 | 6h |
| **Day 3 收尾** | 单测 + 文档 (本 LEARN-RECMODE-DESIGN.md 升级 v2 落实际数据) + CHANGELOG | 2h |
| **Day 4 (5/29) 缓冲** | 漏项补 / 鸿波本机验证 / 推 catfish-web Skills Hub 加 "RecMode 创建" 入口 | 4-6h |

**前置依赖**:
- 5/19 V1-V4 验证通过 (R1-R4 风险可控)
- catfish-private-main 跑得稳 (5/13 升 hermes 0.13 后已验)
- BL-VOICE3 whisper.cpp 已 ship (5/10 done)
- Catfish Chrome CDP 已开 (现状默认 BL-CHROME)

**不依赖**:
- BL-RBAC P0 (RecMode 单员工本地, 不要中央 RBAC)
- BL-CALENDAR / BL-REMINDER (RecMode 输出的 skill 内部可调, 但 RecMode 本身不绑)
- task_manager PG 持久化 (录屏数据本地存)

---

## 10. 跟现有模块复用关系

| 复用 | 怎么用 |
|---|---|
| **whisper.cpp** (BL-VOICE3 5/10) | 录音转写, 现成 |
| **Catfish Chrome CDP** (BL-CHROME) | events 捕获走 ws://localhost:9222, 现成 |
| **catfish-private-main** (gateway) | 综合理解 + 代码生成, 现成 |
| **catfish-gateway 鉴权 + RBAC** | RecMode endpoint 走现有 `get_current_user` Depends, 跟 sessions/tasks endpoint 同模式 |
| **Companion zustand store + chat UI 模式** | RecMode 状态机 / UI 跟 BL-AUTO-CONTINUE / BL-LEAN-SESSION toggle 同模式 |
| **catfish_freeze_skill 输出格式** | RecMode 输出标准 skill 文件, 兼容现有 catfish_run_skill 运行时 |

**不复用**:
- 现有 `catfish_teach_start / catfish_teach_end` (RecMode 是另一个生成入口, 不走 teach 这条路, 因为 teach 要用户写剧本)

---

## 11. 教训 (落档备忘)

1. **跟 catfish_teach 共存, 不替换** — RecMode 是新入口, 不下线现有 `catfish_teach_*`. 原因: 开发者写精确剧本仍是某些场景最准 (e.g. 复杂 API 调用 / 跨系统 orchestration)
2. **5/14 1:50 鸿波"不人性"反馈** 是产品级 reflection, 不是工程 bug. 类似的"我们工程师踩坑后想出的接口" → "用户看到崩溃" 模式以后**多检查**: 任何要求用户写"句 X 调 catfish_xxx_yyy" 的设计, 99% 是产品模式 mismatch
3. **多模态模型选型先 grep 现状** (5/14 2:00 鸿波 audit "main 也是多模态" 时我又踩了凭记忆乱写的坑). 写 BL 设计前先 `grep -r "supports_vision" config/` 30 秒, 别延续旧假设
4. **不依赖 5/19 数据的部分先做** (本设计文档, 不要代码), 数据驱动的部分等真种子. 这样不会"猜错重写"

---

## 12. 未来扩展 (v2+, 不在 MVP 范围)

- **跨员工分享 skill** — 鸿波录的 EIS 检查 skill, 直接分享给其他员工 (走 BL-FED2 federation 路径)
- **多设备 RecMode** — 鸿波在 mac 录, 其他员工 iOS/Windows 跑 (skill 抽象高一层, 不绑 macOS)
- **cron 自动跑 + 飞书通知** — `catfish_schedule_task` 排周一上午跑 RecMode 输出的 skill, 完了发飞书 @ 维护人
- **skill 版本化 + diff** — DOM 大改后 skill 跑失败, 自动 RecMode 微调 (用户重录失败那一段, LLM diff 老 skill + 新录像生成 patch)
- **多语言 RecMode** — 用户用英文/日文 录屏 + 语音, 同样能教 (whisper 多语言已支持)
- **RecMode + ACP /steer 集成** — 录屏中用户中途说"等等, 这一步换个方式", LLM 实时 steer 录制流程

---

## 附录 A: 术语表

- **RecMode**: Record Mode, 录屏 + 语音教学模式
- **keyframe**: 录屏关键帧 (DOM 大变化 / URL 切换 / 长停顿时刻的截图)
- **CDP**: Chrome DevTools Protocol (Catfish Chrome 已暴露 ws://localhost:9222)
- **selector_hint**: skill 步骤里的"非硬编码" selector, 跑时重新 find_by_text
- **种子数据**: 5/19 鸿波录的 EIS 检查录像 (含 events + 语音 + 截图), 作 RecMode 引擎第一个 ground truth

## 附录 B: 5/14 决策路径

- 1:50 鸿波"不人性"反思
- 1:55 我提 BL-LEARN-RECMODE 方向 + 加 task #59
- 2:00 鸿波 audit "中央端用 SQLite?" → 修 task #57 RED-2 用 PG (不在本文档)
- 2:00 鸿波问 "录屏文件用哪种模型识别?"
- 2:05 我答双模型 (vision + main) 分段
- 2:08 鸿波 audit "main 也是多模态, 为啥还要切 vision?" → 我 grep config/models.yaml 确认, 改 main 单模型
- 2:10 鸿波"现在可以开始实现吗?"
- 2:15 我抛 4 选项, 鸿波拍 A 写设计文档
- 2:15-3:15 本文档落档
