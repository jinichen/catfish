"""project-approval smoke test — 跑一遍 render, 检查输出文件存在 + docx 不空.

不验内容细节 (复用 leadership-briefing 的 render, 它有自己的细节测).
只确认:
1. 入口可调用
2. 主 docx 生成
3. 附件 CSV 生成
4. 返回字典字段全
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# 加 skill 目录到 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from script import render_project_approval  # noqa: E402

_HAS_DOCX = importlib.util.find_spec("docx") is not None


@pytest.mark.skipif(not _HAS_DOCX, reason="python-docx 未装")
def test_render_project_approval_basic(tmp_path: Path) -> None:
    """跑一个最小立项材料, 验主 docx + 附件都生成."""
    output = tmp_path / "test-立项.docx"

    result = render_project_approval(
        title_lines=[
            "关于 Catfish 鲶鱼平台升级项目的立项申请",
            "申请人: 研发部",
        ],
        sections=[
            {
                "heading": "一、项目背景及立项必要性",
                "blocks": [
                    {
                        "type": "paragraph",
                        "text": "现有 Catfish 平台 v0.1.0 已 ship, 支持单员工 AI 副手. "
                                "为推进 Phase 2 团队版, 需启动平台升级项目, 投入 120 万 / 6 个月.",
                    },
                ],
            },
            {
                "heading": "二、项目方案与预算",
                "blocks": [
                    {
                        "type": "kv_table",
                        "rows": [
                            ["项目周期", "6 个月 (2026-06 ~ 2026-11)"],
                            ["总预算", "120 万 (人力 90 + 外包 20 + 设备 10)"],
                            ["团队规模", "5 人 (1 后端 + 2 前端 + 1 PM + 1 QA)"],
                        ],
                    },
                    {
                        "type": "paragraph",
                        "text": "详细人力预算见附件1, 设备清单见附件2.",
                    },
                ],
            },
            {
                "heading": "三、风险分析与应对措施",
                "blocks": [
                    {
                        "type": "table",
                        "header": ["风险", "等级", "应对措施"],
                        "rows": [
                            ["招聘进度延误", "中", "先签 1 个核心后端, 其他外包过渡"],
                            ["客户需求变更", "中", "PoC 阶段双周 demo 锁需求"],
                            ["技术债积累", "低", "每月 1 周技术债 sprint"],
                        ],
                    },
                ],
            },
            {
                "heading": "四、进度计划与立项建议",
                "blocks": [
                    {
                        "type": "ordered_list",
                        "items": [
                            {"text": "阶段一 (M1-M2): 团队组建 + 架构升级"},
                            {"text": "阶段二 (M3-M4): RBAC + Skills Hub 全量 ship"},
                            {"text": "阶段三 (M5-M6): Win 跨平台 + PoC 客户落地"},
                        ],
                    },
                    {
                        "type": "paragraph",
                        "text": "建议 Q3 前完成立项, 争取 6 月启动. 请审批.",
                    },
                ],
            },
        ],
        attachments=[
            {
                "kind": "csv",
                "filename": "附件1-人力预算明细",
                "headers": ["角色", "月薪", "月数", "小计"],
                "rows": [
                    ["后端工程师", "30,000", "6", "180,000"],
                    ["前端工程师 ×2", "25,000", "6", "300,000"],
                    ["PM", "28,000", "6", "168,000"],
                ],
            },
        ],
        important_phrases=["120 万", "6 个月", "Q3 前"],
        output_path=str(output),
    )

    assert "docx" in result
    assert Path(result["docx"]).exists(), "主 docx 应该生成"
    assert Path(result["docx"]).stat().st_size > 1000, "docx 不该空"

    assert len(result["attachments"]) == 1, "应该有 1 个 CSV 附件"
    assert Path(result["attachments"][0]).exists()
    assert Path(result["attachments"][0]).suffix == ".csv"

    # files 字段是所有合并
    assert len(result["files"]) >= 2  # 主 + 附件
