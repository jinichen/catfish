"""context preflight —— 「这个对话太长了」那道闸 (8/10).

拦错的代价和漏过的代价**不对等**:
  漏过 → 上游返 400, 还有 request_shape_dump 兜着, 能查
  拦错 → 员工直接用不了, 而且看到一句让他莫名其妙的话

所以判据只拦"铁定不行"的, 边界一律放过去让上游判。
"""

from types import SimpleNamespace as NS

from catfish_gateway.context_preflight import (
    MIN_USEFUL_OUTPUT,
    check_context_fits,
    format_too_long_message,
)

CW = 128000


def _m(cw=CW, name="catfish-private-vision"):
    return NS(name=name, context_window=cw)


def test_每个区间都问过该不该拦():
    """⚠ 第一版判据是 `cw - est*1.3 - 2048 ≤ 0`, 等价于 est ≥ 96886 就拦 ——
    模型明明装得下 128000, **24% 的可用空间被白判死**。

    当时也跑了边界测试, 看到 `est=97000 → 拦` 还写了"边界正确" —— 只验了
    "边界在 dyn 变号那一刻", **没问 97000 到底该不该拦**。现场立刻撞上:
    一个 38 条的会话被拦, 文案自己都荒谬 (两个数字都是 19 万字)。

    所以这条测试逐个区间写明"该不该", 而不是只钉切换点在哪。
    """
    cases = [
        (38_000, False, "单轮正常"),
        (96_886, False, "旧门槛 —— 这里以前会误拦"),
        (120_000, False, "紧但还能回话"),
        (126_666, False, "★ 8/10 现场那个 38 条会话"),
        (CW - MIN_USEFUL_OUTPUT, False, "正好剩得下一句"),
        (CW - MIN_USEFUL_OUTPUT + 1, True, "少一个 token 都塞不下"),
        (CW, True, "刚好占满"),
        (380_000, True, "真超长 (703 条那个)"),
    ]
    for est, should_block, note in cases:
        blocked = check_context_fits(est, _m()) is not None
        assert blocked is should_block, f"est={est} ({note}) 该拦={should_block} 实际={blocked}"


def test_配置缺失时一律放行():
    """不知道容量就别拦 —— 让上游判, 总比拦错强。"""
    assert check_context_fits(999_999, _m(cw=0)) is None
    assert check_context_fits(999_999, NS(name="x")) is None
    assert check_context_fits(0, _m()) is None


def test_文案里两个数接近时不能都取整():
    """8/10 现场: 「已经积累到大约 19 万字, 而模型一次最多能读 19 万字左右」
    —— 两个数一样, 读起来是句废话, 员工只会觉得系统坏了。"""
    close = format_too_long_message(127_600, CW, "m")
    head = close.split("\n")[0]
    nums = [t for t in head.replace("万字", " ").split() if any(c.isdigit() for c in t)]
    assert len(set(nums)) == 2, f"两个数字长一样: {head}"
    assert "." in head, "接近时该显示一位小数"

    far = format_too_long_message(380_000, CW, "m")
    assert "." not in far.split("\n")[0], "差得远就别摆小数, 噪音"


def test_文案不出现技术词():
    """员工不需要懂 token / context_window / 上下文窗口。"""
    msg = format_too_long_message(380_000, CW, "catfish-private-vision")
    for word in ("token", "context", "window", "prompt", "catfish-private-vision"):
        assert word not in msg.lower(), f"文案里漏了技术词: {word}"
    assert "新对话" in msg, "得告诉员工怎么办, 不能只说坏了"
