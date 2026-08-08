"""中央端的**运行日志**不许打员工正文 (8/8 加)。

# 为什么单独加这一道

`test_central_edge_boundary.py` 守的是"中央代码读员工本机文件"。审计记录那边
另有保证 —— `metrics.log_request_metadata` 参数列死 (12 个, 没有 `**kwargs`),
`error` 8/8 已改成封闭词表, PG 的 `extra` 兜底列实测 6619 条记录里落进去 0 个键。

**日志没有这层保证。** 对比:

    审计记录            运行日志
    ─────────────────  ────────────────────────
    参数列死           logger.xxx(...) 传什么打什么
    改签名要过 review   任何人随手加一行就行
    有 CI 闸           ← 8/8 之前没有

事故的形状不是"精心构造的泄漏", 是**一次调试忘了删**: 有人为了查问题写
`logger.error("body=%s", body)`, 员工正文就落到中央服务器的日志文件里, 而且
没有任何测试会红。8/8 就在 facts_pipeline.py:180 找到一处真的 ——
`logger.warning("LLM 返非 JSON: %s\\n原文: %s", e, text[:500])`, 500 字 LLM
原始输出进中央日志, 而 facts 的输入是员工上传的文件。

hermes v0.20 的 monitoring plane 把这条写成设计原则:
「content-free by design: **rendered log messages are not exported**」——
它靠"日志和导出面彻底分开"来保证。我们的网关日志就在中央服务器上, 客户 IT
看得到, 没有那层分离, 所以得在源头拦。

# 判据: 归约过的不算

只看 `logger.<level>(...)` 的**参数**, 不看格式串本身 (格式串是我们自己写的常量)。
参数里出现正文类标识符就报, 但**穿过归约函数的放行**:

    logger.info("... %d chars", len(content))          ✓ 放行 (len 归约成数字)
    logger.warning("... type=%s", type(content).__name__)  ✓ 放行
    logger.info("tools=%d", len(body.get("tools")))    ✓ 放行
    logger.warning("原文: %s", text[:500])              ✗ 报

这条区分是拿真实代码调出来的: 不加归约白名单时基线 8 处命中, 逐个打开看
**全是 len()/type() 这类安全用法**; 加上之后剩 2 处, 其中 1 处是真的。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "catfish_gateway"

#: 正文类标识符 —— 这些变量的**值**不该进中央日志。
#:
#: 只列名词, 不列 `err` / `status` / `model` 这类元数据。`result` 在列里是因为
#: LLM 返回值常叫这个名; `args` 是工具调用参数 (边界文档明确点名不许存)。
RISKY_NAMES = frozenset({
    "body", "messages", "message", "content", "contents",
    "prompt", "prompts", "completion", "answer", "question",
    "text", "payload", "transcript", "journal",
    "memory", "memories", "args", "arguments", "tool_args", "result",
})

#: 把值压成不含原文的东西 —— 穿过这些就放行。
#:
#: 故意**不**收 `str` / `repr` / `format` / `json.dumps`: 它们不是归约, 是转述。
SAFE_REDUCERS = frozenset({
    "len", "type", "bool", "int", "float", "id", "hash", "count", "abs", "round",
})

#: 取这些属性拿到的是元数据不是值。
SAFE_ATTRS = frozenset({"name", "__name__", "__class__", "status_code", "id"})

_LOG_LEVELS = frozenset({
    "debug", "info", "warning", "warn", "error", "exception", "critical", "log",
})
_LOGGER_OBJS = frozenset({"logger", "log", "LOGGER", "_logger"})

#: 存量豁免 —— **故意留空**。
#:
#: 第一版这里放了一条 ("facts_pipeline.py", 348)。写完就漂了: 我在同一个文件
#: 上面改了一处, 下面那行变成 355, 豁免立刻失效。行号跟代码不是一回事, 拿它
#: 当键必然过期, 而过期的表现是"闸门悄悄不管用了"或者"莫名其妙红了"。
#:
#: 改成行内 `# noqa: LOGCONTENT` —— 跟着代码走, 而且理由写在出事的那一行旁边,
#: 看代码的人不用翻到测试文件才知道为什么放行。
#:
#: 留着这个机制是给"没法在源文件加注释"的场合 (比如生成的代码)。真要用,
#: 记得它会漂。
KNOWN_OK: dict[tuple[str, int], str] = {}

#: 单行 `# noqa: LOGCONTENT` 也可以放行 (给确实需要且想清楚了的场合)。
NOQA = "noqa: LOGCONTENT"


def _risky_identifiers(node: ast.AST) -> set[str]:
    """返回这个表达式里**暴露原文**的标识符; 穿过归约的不算。"""
    found: set[str] = set()

    def walk(n: ast.AST, guarded: bool) -> None:
        if isinstance(n, ast.Call):
            fn = n.func
            fname = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            inner = guarded or (fname in SAFE_REDUCERS)
            for child in ast.iter_child_nodes(n):
                walk(child, inner)
            return
        if isinstance(n, ast.Attribute) and n.attr in SAFE_ATTRS:
            return
        if isinstance(n, ast.Name) and not guarded and n.id in RISKY_NAMES:
            found.add(n.id)
            return
        for child in ast.iter_child_nodes(n):
            walk(child, guarded)

    walk(node, False)
    return found


def _scan(py: Path) -> list[tuple[int, list[str]]]:
    try:
        source = py.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    out: list[tuple[int, list[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in _LOG_LEVELS):
            continue
        base = func.value
        base_name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
        if base_name not in _LOGGER_OBJS:
            continue

        risky: set[str] = set()
        # 只看参数, 不看格式串本身 (那是我们写死的常量)
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            risky |= _risky_identifiers(arg)
        if not risky:
            continue

        # 整个调用横跨的行里有 noqa 就放行 (调用常是多行的)
        end = getattr(node, "end_lineno", node.lineno)
        span = "\n".join(lines[node.lineno - 1:end])
        if NOQA in span:
            continue
        out.append((node.lineno, sorted(risky)))
    return out


def test_中央端日志不打员工正文():
    if not SRC_ROOT.exists():
        pytest.skip(f"src 不存在: {SRC_ROOT}")

    new: list[str] = []
    for py in sorted(SRC_ROOT.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        for lineno, names in _scan(py):
            if (py.name, lineno) in KNOWN_OK:
                continue
            new.append(f"  {py.name}:{lineno}  ← 参数里有 {names}")

    if new:
        raise AssertionError(
            "❌ 中央端运行日志里可能打出员工正文\n"
            "   中央端严禁持有员工数据, 日志也算持有。\n\n"
            + "\n".join(new)
            + "\n\n修法 (按优先级):\n"
            "  1. 改成打**归约值**: len(x) / type(x).__name__ / 分类码 —— 排查需要的\n"
            "     信息通常一点没少 (见 facts_pipeline.py:180 那次的改法)\n"
            "  2. 真的必须打原文 → 想清楚为什么中央端需要它, 然后加\n"
            f"     `# {NOQA}` 并在旁边写明理由\n"
            "  3. 存量且确认安全 → 加进 KNOWN_OK 并写清楚为什么"
        )


def test_归约用法不误报():
    """这条钉住白名单本身 —— 少了它, 有人为了消红把 len() 也算成违规就没人发现。"""
    src = (
        "import logging\n"
        "logger = logging.getLogger('x')\n"
        "def f(content, body, text):\n"
        "    logger.info('%d chars', len(content))\n"
        "    logger.warning('type=%s', type(content).__name__)\n"
        "    logger.info('tools=%d', len(body.get('tools') or []))\n"
        "    logger.debug('empty=%s', bool(text))\n"
    )
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _LOG_LEVELS:
                risky: set[str] = set()
                for a in list(node.args) + [k.value for k in node.keywords]:
                    risky |= _risky_identifiers(a)
                assert not risky, f"line {node.lineno} 误报: {risky}"


@pytest.mark.parametrize("bad_call", [
    "logger.error('body=%s', body)",
    "logger.warning('原文: %s', text[:500])",
    "logger.info(f'msg={messages}')",
    "logger.debug('%s', content)",
    # str/repr/json.dumps 是转述不是归约, 必须报
    "logger.info('%s', str(content))",
    "logger.info('%s', repr(body))",
])
def test_真会漏原文的写法一定报(bad_call):
    src = f"import logging\nlogger = logging.getLogger('x')\ndef f(body, text, messages, content):\n    {bad_call}\n"
    tree = ast.parse(src)
    hit = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _LOG_LEVELS:
                for a in list(node.args) + [k.value for k in node.keywords]:
                    if _risky_identifiers(a):
                        hit = True
    assert hit, f"没报: {bad_call}"


def test_豁免表要小():
    """豁免表长了就说明闸门在退化 —— 到时候该改代码不该加豁免。"""
    assert len(KNOWN_OK) <= 5, f"KNOWN_OK 有 {len(KNOWN_OK)} 条, 该回头改代码了"
    for (fname, lineno), reason in KNOWN_OK.items():
        assert len(reason) >= 15, f"{fname}:{lineno} 的豁免理由太短: {reason!r}"
