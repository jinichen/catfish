"""相关性打分 —— 从 catfish_memory_render.py 拆出 (8/17)。

prefetch 的三段 (skills_catalog / strategic_docs / wiki_summary) 都要回答同一个
问题: **这份材料跟员工这句话有没有关系, 够不够格占 prompt**。判据集中在这里,
免得三处各拍一套。

# 为什么单独一个文件

拆之前 catfish_memory_render.py 已经 791 行, 离 800 红线只剩 9 行, 下一段
(skills_catalog) 一改必超。而这几个函数跟"渲染"是两件事:

  · 渲染 = 读盘 / 拼 markdown / 卡预算   → 留在 render
  · 打分 = 给定 query 和一段文本, 算相关性 → 这里

这里**不 import 任何东西** (除了 __future__), 也不碰 self —— 纯函数, 拿两个
集合算个数。这也是它能被单独测的原因。

# 判据的选择有过教训, 别当成随手写的常量

`_STRATEGIC_MIN_OVERLAP` 用 overlap 不用 Jaccard, 是 8/17 量出来的:
Jaccard 的分母是并集, 同一个完美命中在不同长度的文档上摆动 275 倍, 任何
绝对阈值都只在调它那台机器上成立。细节写在两个常量各自的注释里。

改这里的常量之前, 先跑 tests/test_prefetch_relevance.py —— 那里面钉着每个
取值的实测依据, 以及一条"文档变长不影响判定"的回归。
"""
from __future__ import annotations

# 8/15: 跟 _query_token_set 一起从 catfish_memory.py 搬过来。
# 第一版漏了它 —— 依赖计算只跑了那 14 个 render 方法, 后来临时决定把
# _query_token_set / _jaccard_similarity 也搬过来时没重跑, 于是它引用的
# 这个模块级常量留在了原处。check_undefined_names.sh 抓到的 (3 处 NameError)。
_STOPCHARS = set("的了是在我你他她我们你们和跟也都就这那有没不,.,。?!、 \n()—,—:;\"'")


def _query_token_set(text: str) -> set:
    """字符级 + bigram set (去停用字), 真返用作 Jaccard 输入.

    BL-CATFISH-WIKI-MODE P3.1 (6/4): char-only → char + bigram.
    Phrase 完整匹配 (e.g. "月度通报" 完整 hit "月度通报模板") Jaccard升,
    partial match (e.g. "月度发布" 只 hit "月度") 降. 跟 BACKLOG P3.1 BL 一致.
    不依赖 jieba (plugin light, jieba 启动 100ms+).
    """
    if not text:
        return set()
    chars = {c for c in text if c not in _STOPCHARS and c.strip()}
    bigrams = {
        text[i : i + 2]
        for i in range(len(text) - 1)
        if text[i] not in _STOPCHARS
        and text[i + 1] not in _STOPCHARS
        and text[i].strip()
        and text[i + 1].strip()
    }
    return chars | bigrams

#: strategic_docs 的相关性下限: 分数低于榜首这个比例的不进 prompt。
#: 取值依据见 _render_strategic_docs 里的实测表。
#:
#: ⚠ 第一版写的 0.3, 依据是我在旁边重写一遍打分逻辑量出来的数 —— 而那份
#:   重写用 `read_text()[:1500]` 取正文, 真代码走的是 `_read_text_safe(f, 1500)`,
#:   **1500 是字节不是字符**。中文 3 字节一个字, 真正参与打分的正文只有
#:   355-385 字, 不是我以为的 1200。分数全错, 阈值也就无从谈起。
#:   —— 判据比真事宽的又一次: 拿"我重写的实现"当"真实现"来量。
_STRATEGIC_FLOOR_RATIO = 0.25

#: 榜首至少要覆盖 query 的这个比例, 否则视为"一篇都没沾边", 整段折叠。
#:
#: 读法: 0.4 = 提问里至少 40% 的词在某份材料里出现过。
#:
#: ⚠ 这里**故意不用 Jaccard 的绝对值**。第一版写的是 `Jaccard >= 0.008`,
#:   在员工机器上那 8 篇 (每篇约 370 字) 量得好好的 —— 但那个数绑死在文档
#:   的词汇量上: 同一个完美命中, 文档从 317 字长到 717 字, Jaccard 就从
#:   0.00805 掉到 0.00353, 直接跌破 0.008 被静默折叠。摆动 275 倍。
#:   换一台机器、换一批文档, 那个常量就不成立了。
#:   —— 8/17 鸿波问"这种方式换个环境会不会不匹配", 量完确认会, 改成 overlap。
#:
#: 0.3 / 0.4 / 0.5 在真数据 8 个用例上都全对 (该留的 0.57~1.00, 该折的
#: 0.00~0.20, 中间空着), 取中间值。
_STRATEGIC_MIN_OVERLAP = 0.4

# ⚠ 试过、又拿掉的一条: "榜首要比中位数高 N 倍, 否则算没区分度"。
#
# 动机是真的: "高新技术企业认定" 前四名比值 1.00/0.99/0.98/0.93, 八篇全不沾边
# 却全被塞进 prompt (~1,400 token)。加了倍数检验之后那个 case 确实干净了,
# 8 个手标用例全过, 参数可行区间还很宽 (252 组组合全对), 看着很稳。
#
# 然后拿"本来就该匹配多篇"的 query 一试就塌了:
#
#     "catfish 的整体设计"   top=0.0590 med=0.0514 倍数 1.15 → 全折叠 ✗
#     "catfish"            top=0.0539 med=0.0500 倍数 1.08 → 全折叠 ✗
#     "高新技术企业认定"       top=0.0113 med=0.0089 倍数 1.27 → 全折叠 ✓
#
# 八篇战略 doc 全是讲 catfish 的, 员工问 catfish 怎么设计的时候把它们全藏起来,
# 比多花 1,400 token 糟得多。
#
# 根子上: "大家都强相关" 和 "大家都不相关" 在字符级 Jaccard 里长得一模一样 ——
# 都是"分布平坦"。区分它俩要语义, 不是再加一个阈值。硬凑第四个参数只会让
# 判据更贴合我手头这 8 个例子, 而不是更贴合真事。
#
# 所以只留绝对下限 + 相对下限。代价是 "高新技术企业认定" 那类 query 仍然会
# 多带 ~1,400 token —— 但那是**多给了上下文**, 不是漏了上下文, 失败方向是安全的。


def _jaccard_similarity(a: set, b: set) -> float:
    """Jaccard |a ∩ b| / |a ∪ b|. 真空返 0."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union > 0 else 0.0


def _overlap_coefficient(q: set, d: set) -> float:
    """|q ∩ d| / |q| —— "提问里的词, 有多少在这份材料里出现过".

    跟 Jaccard 只差一个分母, 但差别是决定性的 (8/17 鸿波问"换个环境会不会
    不匹配"时量出来的):

        Jaccard 分母是**并集** → 随材料的词汇量变化
        overlap 分母是 **query 自己** → 跟材料多长、多少份、什么语言都无关

    实测: 同一个 query、命中内容一字不变, 只往文档里加互不相同的词:

        正文  17 字 (22 token)    Jaccard 0.22727   overlap 1.00
        正文 317 字 (621 token)   Jaccard 0.00805   overlap 1.00
        正文 717 字 (1416 token)  Jaccard 0.00353   overlap 1.00
        正文 3017 字 (5989 token) Jaccard 0.00083   overlap 1.00

    **同一个完美命中, Jaccard 摆动 275 倍。** 所以任何"Jaccard ≥ 某个绝对值"
    的阈值都只在调它那台机器上成立 —— 换个员工, 文档写长一点、词汇丰富一点,
    相关材料就会被静默折叠掉。这正是 _STRATEGIC_MIN_SCORE=0.008 的病 (我
    8/17 上午写进去的, 下午拆掉)。

    overlap 还能读懂: 0.4 = 提问里至少 40% 的词在这份材料里出现过。

    ⚠ 它自己的偏好: 特别长、词汇特别杂的材料覆盖率天然偏高, 容易蒙混过关。
      但那个方向是**多给上下文**, 不是静默漏掉 —— 比 Jaccard 那个方向安全。
    """
    if not q:
        return 0.0
    return len(q & d) / len(q)
