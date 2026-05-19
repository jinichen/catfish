---
name: project-approval
version: "1.0.0"
deprecated: false
kind: procedural
triggers:
  - 立项
  - 立项报告
  - 立项申请
  - 立项请示
  - 项目立项
  - 项目可研
  - 可行性研究
  - 可研报告
  - 项目方案及预算
description: |-
  ⚠️ MUST CALL: 员工说"立项"/"立项报告"/"立项申请"/"项目可研"/"可研报告"等关键词时, **立即调用此 skill 的 tool_call** (catfish_run_skill name=catfish-project-approval 或 hermes 原生 skill_manage), 不要先输出"我将: 1. 收集材料... 2. 起草..."等计划文字 — BL-LLM-PLAN-WITHOUT-ACT 红线.

  ⭐ 生成项目立项 / 立项申请 / 项目可研 / 立项报告 .docx 文档. 严格按真实公文样式 (复用 leadership-briefing 渲染层).

  **文档结构** (4 段固定逻辑顺序):
  - 标题 1-N 行居中加粗 (一般 1-2 行: "关于 XXX 项目的立项申请" + 申请人/部门)
  - **§ 1 项目背景与必要性** — 业务现状 / 痛点 / 政策背景 / 立项理由
      * 例 heading: "项目背景及立项必要性" / "业务现状与立项理由" / "1. 项目背景"
  - **§ 2 项目方案与预算** — 业务范围 / 技术路线 / 实施步骤 / 人力预算 / 设备/外包预算
      * 例 heading: "项目方案及预算" / "实施方案与投资估算" / "2. 方案设计"
      * 长清单 (人员表 / 设备清单) → 走 CSV 附件
  - **§ 3 风险分析与应对** — 技术风险 / 进度风险 / 预算风险 / 合规风险 + 缓解措施
      * 例 heading: "风险评估与应对措施" / "项目主要风险" / "3. 风险分析"
  - **§ 4 进度计划与立项建议** — 里程碑表 / 关键日期 / 立项请示
      * 例 heading: "进度安排及立项建议" / "下一步计划与请示事项" / "4. 进度计划"

  **每段是若干 block 的有序列表**, block 类型跟 leadership-briefing 完全相同:
      * `paragraph` — 普通段落 (首行缩进, 支持红字高亮)
      * `kv_table` — 项目-内容两列表 (公文方案 / 立项摘要专用)
      * `table` — 多列普通表 (预算明细 / 风险矩阵 / 里程碑表)
      * `ordered_list` — 1./2. + (1)(2) 数字层级列表

  **附件 (CSV)**:
  - 命名: `附件1-XXX.csv`, `附件2-YYY.csv`...
  - 跟主 .docx **同目录**
  - 典型: 附件1-项目人力预算明细 / 附件2-设备清单 / 附件3-里程碑甘特
  - UTF-8 BOM (Excel/Numbers 直接打开中文不乱码)

  **输出路径**:
  - 员工指定路径 → 尊重之
  - 没指定 → `~/.catfish/output/YYYY-MM-DD/HHMMSS_<标题>/`

  **格式细节**:
  - 红字高亮: 关键金额 / 关键日期 / 重大风险, important_phrases 自动标 RGB(255,0,0)
  - 页码: `1 / 2` 居中页脚
  - 字体: 跟 leadership-briefing 一致 (方正小标宋 / 黑体 / 仿宋_GB2312)

  ✅ 必触发关键词: 立项 / 立项申请 / 立项报告 / 项目立项 / 可研 / 可研报告 / 项目可行性 / 项目申请 / 项目立项书 / 立项材料 / 申报材料 / 项目方案 / 项目预算申请 / 申请项目 / 报项目 / 项目立项请示 / 写一份立项 / 提交立项

  ❌ 不触发: 周报 → weekly-report / 月度汇报 / 决策事项 → leadership-briefing / 内部说明 → docx skill / PPT 立项汇报 → pptx

  ⚠️ **调用本 skill 前必读员工画像** (BL-MM7/MM8): 先调 `catfish_user_profile_get` + `catfish_style_fingerprint_get`. 立项的语气尤其受员工风格影响 (有的偏严谨保守, 有的偏激进推动): personality.feedback_style + writing_style.tone 决定 § 3 风险分析的措辞强度; 高频词 (业务领域) 优先用; 偏列表的员工 § 4 进度计划用 ordered_list 不用大段 paragraph. 详见 SOUL.md § BL-MM8.
---

# project-approval — 项目立项 / 立项申请 skill

> 复用 leadership-briefing 的渲染层 (blocks / 字体 / 页脚 / typo_check), 但段落语义改为立项专用 4 段.

---

## 何时触发

员工说出以下意图时**必须**用这个 skill:

- 写一份项目立项 / 立项报告 / 立项申请
- 给公司报项目 / 报立项 / 申报项目
- 项目可研 / 项目可行性研究
- 立项请示 / 重大事项请示 (含项目立项内容)
- 项目方案+预算+进度 一起的材料

## 何时**不**触发 (用别的 skill / 直接对话)

- 项目进度汇报 (无新立项内容) → leadership-briefing
- 周报 → weekly-report
- 项目立项 PPT (而非 docx) → pptx skill
- 内部 markdown 说明 → docx skill
- 口头汇报草稿 → 直接对话, 不出文档

## 入口签名

```python
from skills.department.project_approval.script import render_project_approval

render_project_approval(
    title_lines=[
        "关于 XXX 平台开发项目的立项申请",
        "申请人: 研发部",
    ],
    sections=[
        {
            "heading": "一、项目背景及立项必要性",
            "blocks": [
                {"type": "paragraph", "text": "..."},
                {"type": "ordered_list", "items": [{"text": "理由 1"}, {"text": "理由 2"}]},
            ],
        },
        {
            "heading": "二、项目方案与预算",
            "blocks": [
                {"type": "paragraph", "text": "..."},
                {"type": "kv_table", "rows": [["项目周期", "6 个月"], ["总预算", "120 万"]]},
                {"type": "table", "headers": ["类别","金额","说明"], "rows": [...]},
                {"type": "paragraph", "text": "详细预算见附件1."},
            ],
        },
        {
            "heading": "三、风险分析与应对措施",
            "blocks": [
                {"type": "table", "headers": ["风险","等级","应对"], "rows": [...]},
            ],
        },
        {
            "heading": "四、进度计划与立项建议",
            "blocks": [
                {"type": "ordered_list", "items": [
                    {"text": "阶段一 (M1-M2): 需求 ..."},
                    {"text": "阶段二 (M3-M4): ..."},
                ]},
                {"type": "paragraph", "text": "建议尽快立项, 争取 X 月 X 日前启动."},
            ],
        },
    ],
    attachments=[
        {
            "kind": "csv",
            "filename": "附件1-项目人力预算明细",
            "headers": ["角色","月薪","月数","小计"],
            "rows": [
                ["后端工程师", "30,000", "6", "180,000"],
                ...
            ],
        },
    ],
    important_phrases=["120 万", "6 个月", "Q3 前必须上线"],
    output_path="~/Desktop/立项-XXX平台.docx",  # 可选, 没指定走默认目录
)
```

## 内容质量铁律 (跟 leadership-briefing 同源)

每个 paragraph 至少包含 **论点 + 数据 / 事实 + 推论** 三要素.
不写"加强协作 / 提升效率"等空话, 必须给具体方案 + 量化指标.

风险段落必须给**具体应对措施**, 不要空写"加强管控".

预算 / 周期 / 人力都用红字高亮 (important_phrases).

## 错别字检查

复用 leadership-briefing 的 `typo_check.py`: 公文字典 + pycorrector 双 backend, 按 (old, pos) 去重.
立项材料公文味重, 这个 check 同样适用.
