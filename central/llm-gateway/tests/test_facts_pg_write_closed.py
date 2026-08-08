"""facts 落中央 PG 的字段面是**封闭**的 (8/9 加)。

# 这道闸补的是哪个洞

中央端三条写入面, 8/9 之前只有两条守住了:

    审计记录 (metrics)   参数列死 12 个, 无 **kwargs; PG extra 兜底列实测 6619 条 0 键
    运行日志             8/8 加 test_central_log_no_content.py (AST 闸)
    facts 落 PG          ← 什么都没有

facts 那条是 `json.dumps(整个 dict)` 直接进 jsonb。8/8 抓到的 `raw` (LLM 整段
原始输出, 只写不读) 就是这么进去的 —— 而且**没有任何测试会红**。

同样形状的写入点在 `facts_db.py` 里有 4 处, 不是 1 处:

    pg_write_facts_json → facts_json    pg_upsert_fact   → facts_json
    pg_replace_patches  → changes_json  pg_audit         → meta_json

# 两层闸, 各管一件事

`test_每个_jsonb_写入点都过投影`  ← **结构**闸 (AST)
    新加一个 jsonb 写入点忘了投影 → 直接红。这层管"以后不会再有第 5 个洞"。

`test_白名单键集钉死` + 各 `test_丢弃_*`  ← **行为**闸
    白名单加键必须动这个文件 (= 要过 review); 约定外的键确实被丢掉。

单靠 AST 闸不够: 投影函数被人改成"什么都放行"它照样绿。单靠行为闸也不够:
新写入点压根不调投影函数, 行为测试测不到。

# 为什么是"丢弃"不是"报错"

facts 是管理员的运维动作, 不是员工 chat 主路径。为一个多余的键让整个
extract 失败, 换来的是运维困惑不是安全。丢弃会 warn, 白名单钉在这里要过
review —— 闸在 review 上, 不在运行时。
"""
from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import pytest

from catfish_gateway.facts_schema import (
    AUDIT_META_KEYS,
    FACT_POINT_KEYS,
    FACTS_JSON_KEYS,
    PATCH_CHANGE_KEYS,
    project_audit_meta,
    project_facts_json,
    project_patch_changes,
)

FACTS_DB = Path(__file__).resolve().parent.parent / "src" / "catfish_gateway" / "facts_db.py"

#: 投影函数名前缀 —— `json.dumps(...)` 的第一个参数必须是这类调用。
_PROJECTOR_PREFIX = "project_"


def _strip_comments(src: str) -> str:
    """去掉注释再做字面量检查。

    8/9 踩到: 我在修复处写了条注释解释"原来这里是 f\"LLM 调用失败: {e}\"",
    结果这条测试拿它当成了违规现场。**解释修复的注释必然含有被修复的写法** ——
    对源码做字面量断言就得先把注释剥掉, 否则测试会跟文档打架。
    """
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type != tokenize.COMMENT:
                out.append(tok.string)
    except (tokenize.TokenError, IndentationError):
        return src  # 词法出错就退回原文, 宁可误报也不放行
    return "\n".join(out)


# ── 结构闸: AST ────────────────────────────────────────────────

def _is_projector_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and (
        getattr(node.func, "id", "").startswith(_PROJECTOR_PREFIX)
        or getattr(node.func, "attr", "").startswith(_PROJECTOR_PREFIX)
    )


def _projected_locals(scope: ast.AST) -> set[str]:
    """本作用域内由 project_* 调用赋值出来的局部名。

    8/9 第一版闸只认"参数直接是 project_* 调用", 跑第一次就把
    `pg_write_facts_json` 里的 `projected = project_facts_json(...)` 判红了。
    那是**误报**: 那儿要用两次投影结果 (facts_json 列 + llm_summary 列),
    inline 就得调两遍投影 —— 多一次遍历、warn 打两遍。

    与其把代码扭成闸认得的样子, 不如让闸认得这个写法。多认这一层的代价是
    "名字被重新赋值成别的东西"会漏 —— 下面 test_闸本身认得出违规写法 里钉了
    这个方向的用例。
    """
    names: set[str] = set()
    for n in ast.walk(scope):
        if isinstance(n, ast.Assign) and _is_projector_call(n.value):
            names.update(t.id for t in n.targets if isinstance(t, ast.Name))
        elif isinstance(n, ast.AnnAssign) and n.value is not None:
            if _is_projector_call(n.value) and isinstance(n.target, ast.Name):
                names.add(n.target.id)
    # 之后又被赋成别的东西 → 不认 (防 projected = project_x(d); projected = d)
    for n in ast.walk(scope):
        if isinstance(n, ast.Assign) and not _is_projector_call(n.value):
            names -= {t.id for t in n.targets if isinstance(t, ast.Name)}
    return names


def _unprojected_dumps(src: str) -> list[tuple[int, str]]:
    """找第一个参数没过 project_* 的 json.dumps。"""
    tree = ast.parse(src)
    bad: list[tuple[int, str]] = []

    scopes: list[ast.AST] = [tree]
    scopes += [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    for scope in scopes:
        safe_names = _projected_locals(scope)
        for node in ast.walk(scope):
            # 嵌套函数交给它自己那轮, 避免拿外层作用域的名字放行
            if node is not scope and isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                continue
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name != "dumps" or not node.args:
                continue
            arg = node.args[0]
            ok = _is_projector_call(arg) or (
                isinstance(arg, ast.Name) and arg.id in safe_names
            )
            if not ok:
                bad.append((node.lineno, ast.dump(arg)[:80]))
    # 同一个调用可能被外层和内层各扫一次
    return sorted(set(bad))


def test_每个_jsonb_写入点都过投影():
    src = FACTS_DB.read_text(encoding="utf-8")
    bad = _unprojected_dumps(src)
    if bad:
        raise AssertionError(
            "❌ facts_db.py 里有 json.dumps 没过字段白名单\n"
            "   整个 dict 直接进 jsonb = 上游多返一个键就静默落中央存储\n"
            "   (8/8 那个 `raw` 就是这么进去的)\n\n"
            + "\n".join(f"  facts_db.py:{ln}  ← 第一个参数: {a}" for ln, a in bad)
            + "\n\n修法: 包一层 facts_schema.project_*(), 没有合适的就新加一个\n"
              "      并把它的键集钉进本文件的 test_白名单键集钉死"
        )


def test_闸本身认得出违规写法():
    """钉住 AST 闸 —— 少了这条, 有人把判据写松了没人发现。"""
    # 该报的
    assert _unprojected_dumps("import json\njson.dumps(whatever)\n"), "裸 dict 该报"
    assert _unprojected_dumps("import json\njson.dumps(d, ensure_ascii=False)\n"), "该报"
    # 局部名放行是有条件的: 后面又被赋成别的东西就不认了
    assert _unprojected_dumps(
        "def f(d):\n"
        "    p = project_facts_json(d)\n"
        "    p = d\n"                      # ← 绕过企图
        "    return json.dumps(p)\n"
    ), "被重新赋值的名字不该放行"

    # 不该报的
    assert not _unprojected_dumps("import json\njson.dumps(project_facts_json(d))\n")
    assert not _unprojected_dumps(
        "import json\njson.dumps(project_audit_meta(m), ensure_ascii=False)\n"
    )
    assert not _unprojected_dumps(
        "def f(d):\n"
        "    projected = project_facts_json(d)\n"
        "    return json.dumps(projected, ensure_ascii=False)\n"
    ), "这就是 pg_write_facts_json 的写法, 不该误报"


def test_facts_db_真的引了投影函数():
    """防"AST 闸绿了但函数是本地同名假货"。"""
    src = FACTS_DB.read_text(encoding="utf-8")
    assert "from .facts_schema import" in src, "facts_db.py 没从 facts_schema 引投影函数"


# ── 行为闸: 白名单本身 ─────────────────────────────────────────

def test_白名单键集钉死():
    """加键必须改这里 = 必须过 review。这就是这道闸的全部意义。

    改这个测试之前先问: **这个字段的值来自哪里**? 来自上游 / 上传文件的
    自由文本, 就不该进中央 jsonb。
    """
    assert FACTS_JSON_KEYS == {"summary", "effective_date", "facts", "error"}
    assert FACT_POINT_KEYS == {
        "id", "title", "summary", "category", "keywords", "raw_quote", "impact_scope",
    }
    assert PATCH_CHANGE_KEYS == {"description", "old_snippet", "new_snippet"}
    assert AUDIT_META_KEYS == {
        "filename", "size",
        "facts_count", "impacts_count", "patches_count",
        "patch_idx", "skill", "new_version",
    }


def test_丢弃_raw_8月8日那次的回归():
    """8/8 的现场原样重放: _llm_json 曾返 {"error":…, "raw": 整段 LLM 输出}。"""
    out = project_facts_json({
        "error": "LLM 返非 JSON: Expecting value: line 1 column 1 (char 0)",
        "raw": "上传文档的整段复述……" * 500,
        "facts": [],
    })
    assert "raw" not in out
    assert out["error"].startswith("LLM 返非 JSON")
    assert out["facts"] == []


def test_丢弃_事实点里的约定外键():
    out = project_facts_json({
        "summary": "采购阈值调整",
        "facts": [{
            "id": "fact-001",
            "title": "采购阈值下调",
            "raw_quote": "采购金额超过 3 万元的须走招标流程",
            "source_document_text": "整份文件原文……",   # ← 约定外
            "debug_prompt": "system: 你是合规分析助手……",  # ← 约定外
        }],
    })
    point = out["facts"][0]
    assert set(point) <= FACT_POINT_KEYS
    assert "source_document_text" not in point
    assert "debug_prompt" not in point
    # 白名单内的原样留着 —— 投影不是脱敏, 别顺手把产品本体也削了
    assert point["raw_quote"] == "采购金额超过 3 万元的须走招标流程"


def test_丢弃_patch_changes_里的约定外键():
    out = project_patch_changes([
        {"description": "改阈值", "old_snippet": "5 万", "new_snippet": "3 万",
         "model_scratchpad": "让我想想……"},  # ← 约定外
    ])
    assert out == [{"description": "改阈值", "old_snippet": "5 万", "new_snippet": "3 万"}]


def test_丢弃_audit_meta_里的约定外键():
    out = project_audit_meta({
        "facts_count": 3,
        "user_email": "someone@corp.com",   # ← 约定外 (audit 表另有 by_user 列)
        "prompt": "……",                     # ← 约定外
    })
    assert out == {"facts_count": 3}


# ── 行为闸: 投影器不许把业务搞挂 ───────────────────────────────

@pytest.mark.parametrize("junk", [None, "字符串", 42, [], {"facts": "不是数组"}])
def test_投影器永不抛(junk):
    """facts 是运维动作, 投影器炸了不该连累 extract。"""
    assert isinstance(project_facts_json(junk), dict)
    assert isinstance(project_patch_changes(junk), list)
    assert isinstance(project_audit_meta(junk if isinstance(junk, dict) else None), dict)


def test_facts_不是数组时按空数组处理():
    out = project_facts_json({"summary": "x", "facts": "不是数组"})
    assert out["facts"] == []


def test_事实点不是_dict_的直接跳过():
    out = project_facts_json({"facts": [{"id": "fact-001"}, "脏数据", None, 42]})
    assert out["facts"] == [{"id": "fact-001"}]


# ── 顺带钉住 8/9 同批改的那条 ──────────────────────────────────

def test_LLM_调用失败不再内插上游异常原文():
    """`error` 会跟 {**result} 落进中央 PG, 所以它也得是封闭词表, 不是转述。

    跟 8/8 audit `error` 改 classify_upstream_error 是同一条。
    """
    src = _strip_comments((FACTS_DB.parent / "facts_pipeline.py").read_text(encoding="utf-8"))
    assert 'f"LLM 调用失败: {e}"' not in src, (
        "facts_pipeline 又把上游异常原文内插进 error 了 —— "
        "它会落中央 PG 的 facts_json, 改用 classify_upstream_error"
    )
    assert "classify_upstream_error" in src
