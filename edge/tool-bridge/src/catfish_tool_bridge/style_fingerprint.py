"""BL-MM8 文书风格 fingerprint — 5/6 ship.

# 解决什么问题

央企痛点: 员工每次让鲶鱼写汇报 / 周报 / 立项材料, 鲶鱼**从零猜风格** —
没法照员工历史文档的语气来. 同一员工 5 次写出 5 个风格, 客户感觉"鲶鱼不懂我".

# 怎么做

从 **local_search 索引** (`~/.catfish/search.db`) 取员工历史文档 → 抽统计特征 → 存
`~/.catfish/style_fingerprint.json` → 写新文档时 skill 在 system prompt 里注入
"该员工偏好: 平均句长 28 字 / 多用列表 / 高频词 ['资质', '风控', '合规']".

# BL-STYLE-FP-USE-INDEX (7/27 鸿波实盘 "完全不抽了") — 为什么换数据源

老实现自己 os.walk 两个写死的目录 (`~/Documents/work` + `~/.catfish/output`).
两个都废:

1. `~/Documents/work` 鸿波机器上根本不存在.
2. `~/.catfish/output` **结构性扫不到** —— 老 `_scan_dir` 里那句
   `any(part.startswith(".") for part in p.parts)` 用的是**绝对路径**的全部片段,
   `.catfish` 自己就命中. 本意是跳过扫描根**里面**的 `.git/` `.venv/`,
   写成绝对路径就把扫描根自己毙了. 于是第 66 行声明要扫它, 第 230 行保证一条
   都出不来 —— 同一个文件里两行互相打架, 5/20 到 7/27 一直 0 文档.
   (鸿波那目录里躺着几十份周报/汇报/通报/对标报告, 正是最该学的语料.)

隔壁 local_search 早就把"扫哪些目录 + 抽文本 + 增量"这套做对了, 而且没犯这个 bug
(`indexer.py:_iter_files` 只按 exclude 白名单排除, 不搞绝对路径隐藏判定).
与其把 `_scan_dir` 修好, 不如整个删掉改成查它的索引:

- 目录范围: 员工在 Companion "📂 搜索范围" 卡里配的 `search-scope.yaml`, **一处配置**
  (老实现另有一份 `companion.yaml style_fingerprint.scan_dirs`, 两套割裂, 一并删)
- 文本抽取: markitdown 已经跑过, 白送 pdf / pptx 覆盖 (老实现只有 md/txt/docx)
- 时间衰减: `file_meta.mtime` 现成
- 增量: local_search 有 watcher, 索引一直是新的; fingerprint 不用自己重扫磁盘
- 零硬编码目录

# 跟 BL-MM7 区分

- MM7 = 显式 trait (员工 confirm 后落盘的画像: tone='直接' 等)
- MM8 = 隐式特征 (从历史文档自动抽, 员工不感知, 鲶鱼自动用)
- 互补: MM7 给 LLM 大方向, MM8 给细节模仿

# 抽什么

1. **基础统计**: 总文档数 / 总字符数 / 平均段落数
2. **句子层面**: 平均句长 (中文 。?! / 英文 .?!) / 句长分布 (短/中/长比例)
3. **词频** (jieba 中文分词, 退 char-level n-gram 兜底): top 20 高频实词 + tf-idf
4. **标点偏好**: 逗号 / 分号 / 破折号 / 顿号比例
5. **结构偏好**: 段落里列表项比例 (• - * / 1. 2. / 一、二、) / 表格行比例 (| 分隔) / 散文比例
6. **样本句**: 3-5 句典型样本 (LLM 直接模仿用)

# 时间衰减

近期文档权重高 (员工风格随时间变):
- 30 天内: 1.0
- 90 天内: 0.5
- 180 天内: 0.25
- 更老: 0.1

# 收哪些文档 (从索引里筛)

索引里 82% 是代码 (鸿波库 60332 条里 33206 条 .py / 10685 条 .h), 直接全用会把
公文风格喂成技术文档味 —— 6/03 就栽过一次, Top 高频词变成 `https/the/com/github`.
两道闸:

1. **扩展名白名单** `.md/.txt/.docx/.pdf/.pptx` —— 挡掉代码 / 配置 / 日志.
   `.xlsx/.csv` 不收: 表格不算"文书风格", 而且 markitdown 转出来满屏 `|`
   会把 `_structure_pref` 的 table_ratio 冲爆.
   `.pptx` 收但要留意: 幻灯片是碎句, 会拉低 avg_sentence_length. 真汇报材料
   (鸿波那批 `*汇报*.pptx`) 用词是准的, 先收着, 数据难看再摘。
2. **中文占比闸** `MIN_CN_RATIO` —— 去 noise 后中文字符占比不够的整篇不要.
   挡英文 README / CHANGELOG / 技术笔记. 跟 `_word_freq` 只留中文词是同一立场,
   只是把判断提到**文档级** —— 不然英文文档虽然贡献不了高频词, 照样污染
   句长 / 标点 / 结构比例 / 样本句这四项.

再加原有的 `< MIN_DOC_CHARS` 跳过.

# 使用流程

1. 前置: local_search 得先建过索引 (Companion "📂 搜索范围" 卡 → 建索引).
   没索引时 refresh **明确报错**, 不静默返 0 —— 老实现返 0 让鸿波以为是
   "没文档", 实际是数据源根本没通.
2. `catfish_style_fingerprint_refresh` (查索引 → 写 fingerprint.json)
3. skill 调用前: `catfish_style_fingerprint_get` → 拿 fingerprint → 注入 system prompt
4. 员工 Dashboard 看: 来源文档数 / 高频词 / 上次抽取时间
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple


STYLE_FINGERPRINT_PATH = Path.home() / ".catfish" / "style_fingerprint.json"

# BL-STYLE-FP-USE-INDEX (7/27): 数据源 = local_search 索引库.
# 路径跟 catfish_search/config.py:DB_FILE 对齐. 这里不 import catfish_search ——
# tool-bridge 主路径是 stdlib only (见 pyproject 注释), 而 search.db 的 schema
# 是稳定的落盘契约 (indexer.py:SCHEMA), 直接用 stdlib sqlite3 读就够, 不为一个
# 只读查询引进一个包依赖.
SEARCH_DB_PATH = Path.home() / ".catfish" / "search.db"

# 撞锁时最多等这么久 (秒). 跟 catfish_search.indexer.DB_BUSY_TIMEOUT_SEC 对齐.
# 全量索引一跑几分钟, sqlite 默认 busy_timeout=0 会让这边当场抛 "database is locked".
DB_BUSY_TIMEOUT_SEC = 30.0

# 算"文书"的扩展名 (存进索引时是带点小写的, indexer.py:139 path.suffix.lower()).
# 为什么是这几个 / 为什么没有 .xlsx: 见模块 docstring "收哪些文档".
DOC_FILE_TYPES = (".md", ".txt", ".docx", ".pdf", ".pptx")

# 边界
MIN_DOC_CHARS = 200  # 太短的不算, 没统计意义
MAX_DOCS_TO_SCAN = 500  # 防语料太大爆内存 (取 mtime 最新的 500 篇)
MAX_DOC_CHARS = 100_000  # 单篇截断. 风格统计用不了这么多, 防个别超大文档吃内存
MIN_CN_RATIO = 0.30  # 去 noise 后中文字符占比下限, 低于此判定为技术文档/英文, 整篇不要
TOP_WORDS_K = 20

# 时间衰减档位 (单位: 秒)
DAY = 86400
DECAY_BUCKETS = [
    (30 * DAY, 1.0),
    (90 * DAY, 0.5),
    (180 * DAY, 0.25),
]
DECAY_DEFAULT = 0.1  # 老于 180 天

# 中文常见 stopwords (top freq 实词时过滤)
STOPWORDS_CN = set(
    "的 一 是 在 不 了 有 和 人 这 中 大 为 上 个 国 我 以 要 他 时 来 用 们 生 到 作 地 于 出 就 分 对 成 会 可 主 发 年 动 同 工 也 能 下 过 子 说 产 种 面 而 方 后 多 定 行 学 法 所 民 得 经 十 三 之 进 着 等 部 度 家 电 力 里 如 水 化 高 自 二 理 起 小 物 现 实 加 量 都 两 体 制 机 当 使 点 从 业 本 去 把 性 好 应 开 它 合 还 因 由 其 些 然 前 外 天 政 四 日 那 社 义 事 平 形 相 全 表 间 样 与 关 各 重 新 线 内 数 正 心 反 你 明 看 原 又 么 利 比 或 但 质 气 第 向 道 命 此 变 条 只 没 结 解 问 意 建 月 公 无 系 军 很 情 者 最 立 代 想 已 通 并 提 直 题 党 程 展 五 果 料 象 员 革 位 入 常 文 总 次 品 式 活 设 及 管 特 件 长 求 老 头 基 资 边 流 路 级 少 图 山 统 接 知 较 将 组 见 计 别 她 手 角 期 根 论 运 农 指 几 九 区 强 放 决 西 被 干 做 必 战 先 回 则 任 取 据 处 队 南 给 色 光 门 即 保 治 北 造 百 规 热 领 七 海 口 东 导 器 压 志 世 金 增 争 济 阶 油 思 术 极 交 受 联 什 认 六 共 权 收 证 改 清 美 再 采 转 更 单 风 切 打 白 教 速 花 带 安 场 身 车 例 真 务 具 万 每 目 至 达 走 积 示 议 声 报 斗 完 类 八 离 华 名 确 才 科 张 信 马 节 话 米 整 空 元 况 今 集 温 传 土 许 步 群 广 石 记 需 段 研 界 拉 林 律 叫 且 究 观 越 织 装 影 算 低 持 音 众 书 布 复 容 儿 须 际 商 非 验 连 断 深 难 近 矿 千 周 委 素 技 备 半 办 青 省 列 习 响 约 支 般 史 感 劳 便 团 往 酸 历 市 克 何 除 消 构 府 称 太 准 精 值 号 率 族 维 划 选 标 写 存 候 毛 亲 快 效 斯 院 查 江 型 眼 王 按 格 养 易 置 派 层 片 始 却 专 状 育 厂 京 识 适 属 圆 包 火 住 调 满 县 局 照 参 红 细 引 听 该 铁 价 严 龙 飞".split()
)


# ============================================================
# 文档读取
# ============================================================


# BL-STYLE-FP-NOISE-FILTER (2026-06-03): 真去 markdown noise 真不影响员工公文风格抽取.
#
# 真生产 bug: 真鸿波 Dashboard 真 Top 高频词 'https/the/com/to/github/catfish/of/and/is/hermes'
# — 真扫到 catfish 项目 docs (BACKLOG/PITFALLS/SKILL.md) 真 URL + code blocks + 英文标识符.
# 真不该 作为员工公文 sample. 真 LLM 真按这 fingerprint 真模仿员工写周报 → 真公文味丢.
#
# 真过滤策略 (保留结构 markers 给 _structure_pref 用, 只清 noise source):
# 1. ``` code blocks ``` → 真整段去 (Python/bash 真英文 alpha 真大量)
# 2. `inline code` → 真去 (函数名/变量名)
# 3. https?://URLs → 真去 (https/com/github noise 主源)
# 4. ![img](url) / [text](url) → 真去 (markdown 真 link 语法, 留 text)
# 5. <html tags> → 真去
# 6. **保留** # / - / * / 数字. (真 _structure_pref 真靠这些识别 list/heading)
_RE_CODE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_RE_INLINE_CODE = re.compile(r"`[^`\n]+`")
_RE_URL = re.compile(r"https?://\S+")
_RE_MD_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_RE_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_RE_HTML_TAG = re.compile(r"<[^>]+>")
# 中文字符 run. 7/27 从下面"特征抽取"段挪上来 —— _cn_ratio (文档级中文占比闸)
# 也要用, 而它跟 _filter_noise 是同一道去噪工序, 放一起看得清.
_CN_CHAR_RE = re.compile(r"[一-鿿]+")


def _filter_noise(text: str) -> str:
    """真去 markdown 代码/URL/HTML noise, 保留中英文自然语言 + 结构 markers.

    真适合员工公文风格抽取 — 真排除 catfish 项目 docs 真 noise.
    真保留 # / - / * / 数字. (真 _structure_pref 真识别 list/heading).
    """
    if not text:
        return ""
    # 真先去整块 noise (code blocks 真先, 真免内层 inline 错处理)
    text = _RE_CODE_BLOCK.sub(" ", text)
    text = _RE_MD_IMG.sub(" ", text)
    text = _RE_MD_LINK.sub(r"\1", text)  # 真保留 link 真文字 (一般是中文标题)
    text = _RE_URL.sub(" ", text)
    text = _RE_INLINE_CODE.sub(" ", text)
    text = _RE_HTML_TAG.sub(" ", text)
    return text


def _cn_ratio(text: str) -> float:
    """中文字符占比. 空串返 0.0.

    用来把员工公文跟技术文档/英文笔记分开 —— 见模块 docstring "收哪些文档".
    分母用全长 (含标点/空白/英文), 所以中英混排的公文大概落在 0.4-0.7,
    英文 README 落在 0.0-0.1, 阈值 MIN_CN_RATIO=0.30 分得很开.
    """
    if not text:
        return 0.0
    cn = sum(len(m) for m in _CN_CHAR_RE.findall(text))
    return cn / len(text)


class IndexUnavailable(Exception):
    """local_search 索引不可用 (库不存在 / 打不开 / 表缺失).

    军规 fail-loud: 不能跟"索引里没有符合条件的文档"混成同一个 0 ——
    前者是数据源没通 (员工要去建索引), 后者是语料确实不够 (员工要加目录).
    两种情况给员工的下一步动作完全不同.
    """


def _load_docs_from_index() -> Tuple[List[Tuple[Path, float, str]], Dict[str, int]]:
    """从 local_search 索引取文书语料.

    返 ([(path, mtime, 去噪后正文), ...], 漏斗计数).
    按 mtime 倒序取最新 MAX_DOCS_TO_SCAN 篇 —— 跟时间衰减同向 (老于 180 天的
    权重只有 0.1, 挤掉近期文档不划算).

    索引不可用 → raise IndexUnavailable.

    # 为什么在 Python 里 join mtime 而不写 SQL JOIN

    `documents` 是 FTS5 表, `path` 声明成 UNINDEXED —— 它不在 FTS 索引里, 按
    path 等值查是线性扫. 拿 file_meta 去 JOIN documents 会退化成 O(n²)
    (鸿波库 6 万条). 改成各扫一遍 + 在 Python 里用 dict 拼: 两次线性, 内存
    只多一个 {path: mtime} 的 dict.
    """
    if not SEARCH_DB_PATH.exists():
        raise IndexUnavailable(
            f"local_search 索引库不存在 ({SEARCH_DB_PATH}). "
            "去 Companion → Dashboard → 📂 搜索范围 建一次索引, 或命令行跑 catfish-search index."
        )

    # 优先只读打开: 不给 watcher 上锁, 也不会在库损坏时被 sqlite 悄悄重建成空库.
    #
    # BL-SEARCH-DB-LOCKED (7/27): 索引库开了 WAL 之后, **只读**连接需要能访问
    # -shm 共享内存索引文件. 没有活跃写者时 -shm 不存在, mode=ro 会直接
    # "unable to open database file". 这时退回普通打开 —— 库文件上面已经
    # exists() 过了, 不存在被"悄悄建成空库"的风险; 本函数只发 SELECT, 不写.
    # timeout 跟索引器那边对齐, 全量索引跑着的时候等而不是当场抛.
    try:
        conn = sqlite3.connect(
            f"file:{SEARCH_DB_PATH}?mode=ro", uri=True, timeout=DB_BUSY_TIMEOUT_SEC
        )
    except sqlite3.Error:
        try:
            conn = sqlite3.connect(SEARCH_DB_PATH, timeout=DB_BUSY_TIMEOUT_SEC)
        except sqlite3.Error as e:
            raise IndexUnavailable(f"打不开索引库 {SEARCH_DB_PATH}: {e}") from e

    funnel = {"indexed_doc_type": 0, "too_short": 0, "not_chinese": 0, "kept": 0}
    candidates: List[Tuple[Path, float, str]] = []
    placeholders = ",".join("?" * len(DOC_FILE_TYPES))
    try:
        try:
            mtimes: Dict[str, float] = {
                row[0]: row[1]
                for row in conn.execute("SELECT path, mtime FROM file_meta")
            }
            # 流式迭代 cursor, 不 fetchall() —— 命中的行可能有上千条, 每条 content
            # 最大 MAX_DOC_CHARS, 一次性拉进内存峰值不可控. 边读边筛, 只留通过的.
            cur = conn.execute(
                f"""
                SELECT path, substr(content, 1, ?)
                FROM documents
                WHERE file_type IN ({placeholders})
                """,  # noqa: S608 - placeholders 由常量元组生成, 无外部输入
                (MAX_DOC_CHARS, *DOC_FILE_TYPES),
            )
            for path_str, raw in cur:
                funnel["indexed_doc_type"] += 1
                content = _filter_noise(raw or "")
                if len(content) < MIN_DOC_CHARS:
                    funnel["too_short"] += 1
                    continue
                if _cn_ratio(content) < MIN_CN_RATIO:
                    funnel["not_chinese"] += 1
                    continue
                candidates.append(
                    (Path(path_str), float(mtimes.get(path_str, 0.0)), content)
                )
        except sqlite3.Error as e:
            raise IndexUnavailable(
                f"索引库 schema 不对或已损坏 ({e}). 删掉 {SEARCH_DB_PATH} 重建一次索引."
            ) from e
    finally:
        conn.close()

    candidates.sort(key=lambda t: -t[1])  # mtime 新的在前
    kept = candidates[:MAX_DOCS_TO_SCAN]
    funnel["kept"] = len(kept)
    return kept, funnel


# ============================================================
# 特征抽取
# ============================================================


_SENT_SPLIT_RE = re.compile(r"[。！？!?\.;；\n]+")


def _split_sentences(text: str) -> List[str]:
    """中文 / 英文兼容句子切分."""
    return [s.strip() for s in _SENT_SPLIT_RE.split(text) if s.strip()]


def _word_freq(text: str) -> Dict[str, int]:
    """词频. 优先 jieba (中文), 退 char-level n-gram (2-字).

    返 {word: count}, 已过滤 stopword + 短于 2 字 + 纯标点.

    BL-STYLE-FP-NOISE-FILTER (2026-06-03): **只**留含中文 word.
    真生产员工写公文中文为主, 真英文术语 (catfish/hermes/skill) 只 noise
    充 Top 高频词 (https/the/com/to/and/of/is). 禁英文 双保险配
    _filter_noise (清 URL/code).
    """
    freq: Dict[str, int] = {}
    try:
        import jieba  # noqa: PLC0415
        jieba.setLogLevel(60)  # 关 noisy log
        for w in jieba.cut(text):
            w = w.strip()
            if len(w) < 2:
                continue
            if w in STOPWORDS_CN:
                continue
            # BL-STYLE-FP-NOISE-FILTER: **只**留含中文 word, 禁纯英文 (the/com/of/and 等)
            if not _CN_CHAR_RE.search(w):
                continue
            freq[w] = freq.get(w, 0) + 1
    except ImportError:
        # fallback: char-level 2-gram (无 jieba 时只能粗糙的 freq)
        chars = _CN_CHAR_RE.findall(text)
        for run in chars:
            for i in range(len(run) - 1):
                bigram = run[i : i + 2]
                if bigram[0] in STOPWORDS_CN or bigram[1] in STOPWORDS_CN:
                    continue
                freq[bigram] = freq.get(bigram, 0) + 1
    return freq


def _punctuation_pref(text: str) -> Dict[str, int]:
    """常见标点出现次数."""
    out = {
        "comma_cn": text.count("，"),
        "comma_en": text.count(","),
        "semicolon_cn": text.count("；"),
        "semicolon_en": text.count(";"),
        "dash": text.count("——") + text.count("--"),
        "顿号": text.count("、"),
        "ellipsis": text.count("……") + text.count("..."),
        "exclamation": text.count("！") + text.count("!"),
        "question": text.count("？") + text.count("?"),
    }
    return out


def _structure_pref(text: str) -> Dict[str, float]:
    """段落里列表 / 表格 / 散文比例."""
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    if not paras:
        return {"list_ratio": 0.0, "table_ratio": 0.0, "prose_ratio": 0.0}
    list_count = 0
    table_count = 0
    for p in paras:
        # 列表: 以 • / - / * / 数字. / 一、 二、 三、 开头
        if re.match(r"^([•\-\*]|\d+[.\)、]|[一二三四五六七八九十]+[、.])\s", p):
            list_count += 1
        elif "|" in p and p.count("|") >= 2:
            table_count += 1
    total = len(paras)
    return {
        "list_ratio": round(list_count / total, 3),
        "table_ratio": round(table_count / total, 3),
        "prose_ratio": round((total - list_count - table_count) / total, 3),
    }


def _decay_weight(mtime: float, now: float) -> float:
    """时间衰减权重: 越近权重越高."""
    age = max(0.0, now - mtime)
    for threshold, w in DECAY_BUCKETS:
        if age <= threshold:
            return w
    return DECAY_DEFAULT


def _sample_sentences(docs: List[Tuple[Path, float, str]], n: int = 5) -> List[str]:
    """挑 n 句典型样本句 — 优先取近期文档的中等长度句子."""
    sentences: List[Tuple[str, float, int]] = []  # (text, weight, len)
    now = time.time()
    for _, mtime, content in docs:
        w = _decay_weight(mtime, now)
        for s in _split_sentences(content):
            sl = len(s)
            if 15 <= sl <= 80:  # 中等长度句更代表风格
                sentences.append((s, w, sl))
    if not sentences:
        return []
    # 排序: 权重降序 + 中等长度优先
    sentences.sort(key=lambda x: (-x[1], abs(x[2] - 40)))
    out: List[str] = []
    seen = set()
    for s, _, _ in sentences:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= n:
            break
    return out


# ============================================================
# 主入口
# ============================================================


def _build_fingerprint(docs: List[Tuple[Path, float, str]]) -> Dict[str, Any]:
    """从 [(path, mtime, content), ...] 构建 fingerprint dict."""
    if not docs:
        # 5/20 BL-STYLE-FP-NAN-FIX: 空 docs 仍返完整 structure_pref shape (0/0/0)
        # 而非 {}. 前端拿到 None/undefined ratio 显 NaN%, 给 0 就显 0%.
        return {
            "sources": [],
            "stats": {
                "total_docs": 0,
                "total_chars": 0,
                "avg_sentence_length": 0.0,
                "sentence_count": 0,
            },
            "top_words": [],
            "punctuation_pref": {},
            "structure_pref": {
                "list_ratio": 0.0,
                "table_ratio": 0.0,
                "prose_ratio": 0.0,
            },
            "sample_sentences": [],
            "last_refreshed": time.time(),
            "had_jieba": _has_jieba(),
        }

    now = time.time()
    weighted_word_freq: Dict[str, float] = {}
    weighted_punct: Dict[str, float] = {}
    sentence_lens: List[Tuple[float, int]] = []  # (weight, len)
    structure_acc = {"list_ratio": 0.0, "table_ratio": 0.0, "prose_ratio": 0.0}
    total_weight = 0.0
    total_chars = 0

    for path, mtime, content in docs:
        w = _decay_weight(mtime, now)
        total_weight += w
        total_chars += len(content)

        # 词频加权累计
        for word, c in _word_freq(content).items():
            weighted_word_freq[word] = weighted_word_freq.get(word, 0.0) + c * w

        # 标点
        for k, v in _punctuation_pref(content).items():
            weighted_punct[k] = weighted_punct.get(k, 0.0) + v * w

        # 句长加权
        for s in _split_sentences(content):
            sentence_lens.append((w, len(s)))

        # 结构 (按权重平均)
        struct = _structure_pref(content)
        for k, v in struct.items():
            structure_acc[k] += v * w

    avg_sent_len = (
        sum(w * sl for w, sl in sentence_lens) / sum(w for w, _ in sentence_lens)
        if sentence_lens else 0
    )

    # 标准化结构 ratio
    if total_weight > 0:
        for k in structure_acc:
            structure_acc[k] = round(structure_acc[k] / total_weight, 3)

    # top words
    top_words = sorted(weighted_word_freq.items(), key=lambda x: -x[1])[:TOP_WORDS_K]
    top_words_out = [{"word": w, "weight": round(c, 1)} for w, c in top_words]

    return {
        "sources": [
            {"path": str(p), "mtime": m, "kind": p.suffix.lstrip(".") or "txt"}
            for p, m, _ in docs
        ],
        "stats": {
            "total_docs": len(docs),
            "total_chars": total_chars,
            "avg_sentence_length": round(avg_sent_len, 1),
            "sentence_count": len(sentence_lens),
        },
        "top_words": top_words_out,
        "punctuation_pref": {k: round(v, 1) for k, v in weighted_punct.items()},
        "structure_pref": structure_acc,
        "sample_sentences": _sample_sentences(docs, n=5),
        "last_refreshed": time.time(),
        "had_jieba": _has_jieba(),
    }


def _has_jieba() -> bool:
    try:
        import jieba  # noqa: F401, PLC0415
        return True
    except ImportError:
        return False


def _read_fingerprint() -> Dict[str, Any]:
    """读 fingerprint.json. 不存在或损坏返空 dict."""
    if not STYLE_FINGERPRINT_PATH.exists():
        return {}
    try:
        with open(STYLE_FINGERPRINT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_fingerprint(fp: Dict[str, Any]) -> None:
    """原子写."""
    STYLE_FINGERPRINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STYLE_FINGERPRINT_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(fp, f, ensure_ascii=False, indent=2)
    tmp.replace(STYLE_FINGERPRINT_PATH)


# ============================================================
# 工具入口
# ============================================================


def style_fingerprint_get(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: 读当前 fingerprint. 返精简形式 (省 tokens, 不返完整 sources path 列表).

    LLM 用法: leadership-briefing / weekly-report / project-approval skill 在 render
    前调一次, 拿到风格描述拼到 system prompt. 没 fingerprint (员工首次用) 返空.
    """
    fp = _read_fingerprint()
    if not fp:
        return {
            "type": "result",
            "result": {
                "exists": False,
                "hint": "fingerprint 还没生成, 调 catfish_style_fingerprint_refresh 扫一次员工历史文档",
            },
        }
    # 精简输出 — 不返完整 sources path
    return {
        "type": "result",
        "result": {
            "exists": True,
            "stats": fp.get("stats", {}),
            "top_words": fp.get("top_words", [])[:10],  # 只返 top 10
            "punctuation_pref": fp.get("punctuation_pref", {}),
            "structure_pref": fp.get("structure_pref", {}),
            "sample_sentences": fp.get("sample_sentences", [])[:3],  # 只返 3 句
            "last_refreshed": fp.get("last_refreshed", 0),
            "source_count": len(fp.get("sources", [])),
            "had_jieba": fp.get("had_jieba", False),
        },
    }


def style_fingerprint_refresh(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: 从 local_search 索引重建 fingerprint. 无参数.

    BL-STYLE-FP-USE-INDEX (7/27): 删了 args.source_dirs —— 目录范围现在唯一由
    员工的 search-scope.yaml 决定 (Companion "📂 搜索范围" 卡). 留一个绕过口
    只会让"我到底扫了哪"重新变成两个答案.

    返:
      成功 {type: result, result: {total_docs, funnel, top_3_words, ...}}
      索引没通 {type: result, result: {error, hint, total_docs: 0}} —— 不写盘,
      保住上一次的 fingerprint (总比覆盖成空好).
    """
    try:
        docs, funnel = _load_docs_from_index()
    except IndexUnavailable as e:
        # 军规 fail-loud: 数据源没通 ≠ 没文档. 明说, 且**不覆盖**已有 fingerprint.
        return {
            "type": "result",
            "result": {
                "error": "index_unavailable",
                "hint": str(e),
                "total_docs": 0,
                "source_db": str(SEARCH_DB_PATH),
            },
        }

    fp = _build_fingerprint(docs)
    _write_fingerprint(fp)

    result: Dict[str, Any] = {
        "total_docs": len(docs),
        "fingerprint_path": str(STYLE_FINGERPRINT_PATH),
        "source_db": str(SEARCH_DB_PATH),
        # 漏斗 —— 0 篇时能一眼看出卡在哪层, 不用猜
        # (索引里的文书类 → 去噪后太短 → 中文占比不够 → 最终留下)
        "funnel": funnel,
        "had_jieba": fp.get("had_jieba", False),
        "top_3_words": [w["word"] for w in fp.get("top_words", [])[:3]],
    }
    if not docs:
        if funnel["indexed_doc_type"] == 0:
            result["hint"] = (
                f"索引里一篇 {'/'.join(DOC_FILE_TYPES)} 都没有. "
                "去 Companion → Dashboard → 📂 搜索范围 把放文档的目录加进去, 再建一次索引."
            )
        else:
            result["hint"] = (
                f"索引里有 {funnel['indexed_doc_type']} 篇文书类文档, 但全被筛掉了 "
                f"(太短 {funnel['too_short']} 篇 / 中文占比不足 {funnel['not_chinese']} 篇). "
                "多半是索引到的都是英文技术文档 — 把你写公文的目录加进搜索范围."
            )
    return {"type": "result", "result": result}


def style_fingerprint_clear(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: 清掉 fingerprint (员工想 reset 时)."""
    if STYLE_FINGERPRINT_PATH.exists():
        STYLE_FINGERPRINT_PATH.unlink()
    return {"type": "result", "result": "已清掉 style_fingerprint"}
