"""BL-MM8 文书风格 fingerprint — 5/6 ship.

# 解决什么问题

央企痛点: 员工每次让鲶鱼写汇报 / 周报 / 立项材料, 鲶鱼**从零猜风格** —
没法照员工历史文档的语气来. 同一员工 5 次写出 5 个风格, 客户感觉"鲶鱼不懂我".

# 怎么做

抽员工历史文档 (~/Documents/work/, ~/.catfish/output/) 的统计特征 → 存
`~/.catfish/style_fingerprint.json` → 写新文档时 skill 在 system prompt 里注入
"该员工偏好: 平均句长 28 字 / 多用列表 / 高频词 ['资质', '风控', '合规']".

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

# 文档类型支持

- .md / .txt: 直接读
- .docx: python-docx 提取段落文本
- .csv: 跳过 (表格不算"文书风格")
- .pdf: 跳过 (二级抽取太麻烦, 5/22 后再加)
- 二进制 / 隐藏文件 / < 200 字: 跳过

# 使用流程

1. 启动后台脚本: `catfish_style_fingerprint_refresh` (扫文件夹 → 写 fingerprint.json)
2. skill 调用前: `catfish_style_fingerprint_get` → 拿 fingerprint → 注入 system prompt
3. 员工 Dashboard 看: 来源文档数 / 高频词 / 上次抽取时间
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


STYLE_FINGERPRINT_PATH = Path.home() / ".catfish" / "style_fingerprint.json"

# 默认扫描目录 (员工本机, 鲶鱼自己生成的输出 + 员工 Documents/work/)
DEFAULT_SOURCE_DIRS = [
    Path.home() / "Documents" / "work",
    Path.home() / ".catfish" / "output",
]

# BL-STYLE-FP-YAML-CONFIG (5/20 鸿波报"来源文档 0"): 员工真写文档的目录 (~/person_task/
# catfish/docs/, ~/work-reports/ 等) 不在 DEFAULT. 让 ~/.catfish/companion.yaml 加
# style_fingerprint.scan_dirs: [...] union 默认 (默认目录不存在也不挂, 见 _scan_dir).
COMPANION_YAML_PATH = Path.home() / ".catfish" / "companion.yaml"


def _load_scan_dirs_yaml() -> List[Path]:
    """读 ~/.catfish/companion.yaml 的 style_fingerprint.scan_dirs.

    yaml 不存在 / 解析失败 / 没 style_fingerprint 段 → 返空 list, 不抛.
    返的路径已 expanduser(), 但不验证存在 (_scan_dir 不存在自动跳).
    """
    if not COMPANION_YAML_PATH.exists():
        return []
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return []  # 没装 yaml 静默
    try:
        with open(COMPANION_YAML_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, Exception):  # noqa: BLE001
        return []
    if not isinstance(data, dict):
        return []
    cfg = data.get("style_fingerprint", {})
    if not isinstance(cfg, dict):
        return []
    dirs = cfg.get("scan_dirs", [])
    if not isinstance(dirs, list):
        return []
    return [Path(s).expanduser() for s in dirs if isinstance(s, str) and s.strip()]


def _resolve_scan_dirs(arg_dirs: Optional[List[str]] = None) -> List[Path]:
    """resolve 最终扫描目录列表.

    优先级: explicit args.source_dirs > yaml union 默认 > 默认.
    args 给了显式 list → 只走 args (调用方知道自己要啥).
    没给 → DEFAULT_SOURCE_DIRS + yaml 自定义 (去重保序).
    """
    if arg_dirs and isinstance(arg_dirs, list):
        return [Path(s).expanduser() for s in arg_dirs if isinstance(s, str)]
    # union default + yaml, 去重保序 (先 default, 再 yaml 新加的)
    seen: set = set()
    out: List[Path] = []
    for d in list(DEFAULT_SOURCE_DIRS) + _load_scan_dirs_yaml():
        key = str(d)
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out

# 边界
MIN_DOC_CHARS = 200  # 太短的不算, 没统计意义
MAX_DOCS_TO_SCAN = 500  # 防文件夹太大爆内存
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB 上限单文件
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


def _read_doc(path: Path) -> Optional[str]:
    """读一个文档. 不支持的类型 / 太大 / 读失败 → None."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > MAX_FILE_SIZE or size < 100:
        return None

    suffix = path.suffix.lower()
    try:
        if suffix in {".md", ".txt", ".markdown"}:
            return path.read_text(encoding="utf-8", errors="replace")
        if suffix == ".docx":
            try:
                from docx import Document  # noqa: PLC0415
            except ImportError:
                return None
            doc = Document(str(path))
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
        # 其他扩展名 (csv / pdf / xlsx) 跳过
        return None
    except Exception:
        return None


def _scan_dir(d: Path, max_docs: int) -> List[Tuple[Path, float, str]]:
    """扫一个目录, 返回 [(path, mtime, content), ...] 列表."""
    out: List[Tuple[Path, float, str]] = []
    if not d.exists() or not d.is_dir():
        return out
    for p in d.rglob("*"):
        if len(out) >= max_docs:
            break
        if not p.is_file() or p.name.startswith("."):
            continue
        # 跳过隐藏目录
        if any(part.startswith(".") for part in p.parts):
            continue
        content = _read_doc(p)
        if content is None or len(content) < MIN_DOC_CHARS:
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        out.append((p, mtime, content))
    return out


# ============================================================
# 特征抽取
# ============================================================


_SENT_SPLIT_RE = re.compile(r"[。！？!?\.;；\n]+")
_CN_CHAR_RE = re.compile(r"[一-鿿]+")


def _split_sentences(text: str) -> List[str]:
    """中文 / 英文兼容句子切分."""
    return [s.strip() for s in _SENT_SPLIT_RE.split(text) if s.strip()]


def _word_freq(text: str) -> Dict[str, int]:
    """词频. 优先 jieba (中文), 退 char-level n-gram (2-字).

    返 {word: count}, 已过滤 stopword + 短于 2 字 + 纯标点.
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
            if not _CN_CHAR_RE.search(w) and not w.isalpha():
                continue  # 跳标点 / 数字
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
    """tool: 重新扫描员工文档目录, 重建 fingerprint.

    args:
      source_dirs: list of str, 默认 ['~/Documents/work', '~/.catfish/output']
                   员工想加目录就显式传

    返:
      {type: result, result: {scanned_dirs, total_docs, fingerprint_path}}
    """
    # BL-STYLE-FP-YAML-CONFIG (5/20): args 没给 → resolve 默认 + yaml union
    dirs = _resolve_scan_dirs(args.get("source_dirs"))

    all_docs: List[Tuple[Path, float, str]] = []
    scanned_dirs: List[str] = []
    skipped_dirs: List[str] = []  # 不存在 / 不是目录 / 没读出内容
    for d in dirs:
        scanned_dirs.append(str(d))
        if not d.exists() or not d.is_dir():
            skipped_dirs.append(str(d))
            continue
        docs = _scan_dir(d, MAX_DOCS_TO_SCAN - len(all_docs))
        all_docs.extend(docs)
        if len(all_docs) >= MAX_DOCS_TO_SCAN:
            break

    fp = _build_fingerprint(all_docs)
    _write_fingerprint(fp)

    return {
        "type": "result",
        "result": {
            "scanned_dirs": scanned_dirs,
            "skipped_dirs": skipped_dirs,  # 5/20: 显式列不存在目录, 帮员工调试
            "total_docs": len(all_docs),
            "fingerprint_path": str(STYLE_FINGERPRINT_PATH),
            "had_jieba": fp.get("had_jieba", False),
            "top_3_words": [w["word"] for w in fp.get("top_words", [])[:3]],
            # 5/20: 提示员工 yaml 配置位置, 方便加 scan_dirs
            "yaml_config_hint": (
                f"加扫描目录: 编辑 {COMPANION_YAML_PATH}, 加段:\n"
                "style_fingerprint:\n  scan_dirs:\n    - ~/your-doc-dir"
            ) if len(all_docs) == 0 else None,
        },
    }


def style_fingerprint_clear(args: Dict[str, Any]) -> Dict[str, Any]:
    """tool: 清掉 fingerprint (员工想 reset 时)."""
    if STYLE_FINGERPRINT_PATH.exists():
        STYLE_FINGERPRINT_PATH.unlink()
    return {"type": "result", "result": "已清掉 style_fingerprint"}
