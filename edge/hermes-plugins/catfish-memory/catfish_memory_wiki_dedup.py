"""wiki 写入去重 —— 同一个东西不建第二份, 同一个名字不被两个条目认领。

从 catfish_memory_wiki.py 拆出 (9/24, 那个文件到 800 行上限了)。逻辑:
  _redirect_to_existing_equivalent  新条目跟已有的是同一个 → 写入路径指回那一个
  _drop_claimed_aliases             别名被别的条目认领了 → 从新内容里去掉
导入方式跟 catfish_memory_wiki 一样两种都要活 (见那边文件头注释)。
"""
from __future__ import annotations

from pathlib import Path
import re

try:
    from .catfish_memory_helpers import logger
    from .catfish_memory_fm import _split_frontmatter_body, _split_top_level
    from .wiki_resolve import load_nodes, name_owners
except ImportError:  # 独立脚本模式 (无父包)
    from catfish_memory_helpers import logger
    from catfish_memory_fm import _split_frontmatter_body, _split_top_level
    from wiki_resolve import load_nodes, name_owners


def _normalize_slug_for_dedup(stem: str) -> str:
    r"""跟 Companion wiki_write.rs:54 normalize_slug 同一套口径。

    去掉空白 / - / _ / . / 全角空格再小写。`zhongdian-fufu` 和 `zhongdianfufu`
    和 `zhong-dian-fu-fu` 规范化后都是 `zhongdianfufu`。
    """
    return "".join(
        c for c in stem
        if not c.isspace() and c not in ("-", "_", ".", "\u3000")
    ).lower()


def _read_title_of(path: Path) -> str:
    """拿这个文件**在 UI 上显示的名字**。

    跟读侧 (wiki_read.rs build_file_info) 同一套 fallback: frontmatter title
    取不到就用文件名。8/4 实测有条目是纯 markdown 完全没 frontmatter
    (信息安全中心.md, 232 字节), 只按 frontmatter title 分组会漏掉它 ——
    而 UI 上它显示的就是"信息安全中心", 跟另一条一模一样。
    """
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:600]
    except OSError:
        return ""
    fm, _ = _split_frontmatter_body(head)
    if fm:
        for line in fm.splitlines():
            line = line.strip()
            if line.startswith("title:"):
                t = line.split("title:", 1)[1].strip()
                if t:
                    return t
    return path.stem          # ← 跟读侧 fallback 一致


def _title_of_content(text: str) -> str:
    """从**还没写盘的内容**里拿 title。

    ⚠ 这个函数存在的原因: _redirect_to_existing_equivalent 拿到的 rel_path 指向
    一个还不存在的文件 (那正是它要判断该不该新建的东西)。第一版拿 Path 去读它,
    永远读到空 → title 判据一次都不会触发, 而且**不报错**。
    又是今天查了一整天的那种形状: 规则写了, 静默不生效。
    """
    fm, _ = _split_frontmatter_body(text[:600])
    if not fm:
        return ""
    for line in fm.splitlines():
        line = line.strip()
        if line.startswith("title:"):
            return line.split("title:", 1)[1].strip()
    return ""


def _redirect_to_existing_equivalent(
    catfish_home: Path, rel_path: str, content: str = "",
) -> str:
    r"""同一实体的 slug 变体 → 指回已有的那个文件, 走 merge 而不是新建。

    # 为什么会有变体 (8/4 鸿波 "为什么会被拆成多个")

    实测鸿波机器 255 条里, **21 组同 title 多文件, 共 50 个文件 (20%)**:

        「高新技术企业认定」 × 5   gaoxinjishu-qiye-rending / gaoxinjishu-qiye-ren-ding
                                  / gaoxin-jishu-qiye-rending / gaoxinjishuqiyerending
                                  / gao-xin-ji-shu-qi-ye-ren-ding
        「中电福富」        × 3   zhongdianfufu / zhongdian-fufu / zhong-dian-fu-fu

    三个原因叠在一起:

    1. Generation prompt 的 slug 规则**自相矛盾** (:335):
         "slug = 中文转拼音**首字母** (e.g. 陈鸿波 → chenhongbo, 中电福富 → zdff)"
       说首字母, 举的例子一个是首字母 (zdff) 一个是全拼 (chenhongbo)。同一句话
       两套规则, LLM 只能猜 —— 实测产出的是第三种 (全拼, 分词每次不同)。

    2. _call_generation_llm 只喂 analysis, **LLM 不知道已有哪些条目**, 每次从零造。

    3. _write_wiki_files 只做 `target.exists()` **精确路径匹配** —— 变体路径不同,
       于是每次都建新文件。

    而 Companion UI 建条目那条路 (wiki_write.rs::find_normalized_collision) 有
    规范化重名检测, 13/21 组它本来就能拦住。**又是两条产线只有一条设防** ——
    跟 8/3 mac/Windows 打包那次同一个形状。

    # 这个函数做什么

    写盘前查同目录里有没有规范化等价的文件。有就把写入路径**指回那一个**, 于是
    落进已有的 merge 逻辑 (P18/P19) 而不是新建。

    只认规范化等价 (差连字符/下划线/大小写), **不做模糊匹配** —— "中电福富" 和
    "中电福富信息科技有限公司" 规范化后不同, 不会被误合。那种要靠语义判断, 不是
    这里该干的事, 硬合会把两个真实体揉成一个, 比重复更糟。
    """
    d = catfish_home / rel_path
    if d.exists():
        return rel_path                      # 精确命中, 原逻辑已能处理
    parent = d.parent
    if not parent.is_dir():
        return rel_path
    want = _normalize_slug_for_dedup(d.stem)
    # 判据用**员工看得见的名字**: frontmatter title 取不到就 fallback 文件名,
    # 跟读侧 build_file_info 同一套规则。
    new_title = _title_of_content(content) or d.stem
    if not want and not new_title:
        return rel_path
    try:
        for q in sorted(parent.iterdir()):
            if q.suffix != ".md" or q.name == d.name:
                continue
            # 判据 ①: title 完全相同 —— 比 slug 规范化更强。
            #
            # 8/4 实测: 规范化等价只覆盖 13/21 组, 剩下 8 组是"中文名 vs 拼音名"
            # (高新资质申报 vs gaoxin-zizhi-shenbao / 陈秀平 vs chenxiuping) 或
            # 拼音转写不同 (zizhi-duibiao-fenxi-yuanze vs zizhibiaofenxifenze),
            # slug 规范化后仍然不等 —— 但 title 一模一样。
            #
            # title 是员工在 UI 上**唯一看得见的东西**。两条 title 完全相同的
            # 条目, 员工根本分不出是两个东西 —— 那就该是一个。
            if new_title and new_title == _read_title_of(q):
                new_rel = str(Path(rel_path).parent / q.name)
                logger.info(
                    "catfish-memory wiki 去重: %s 跟已有的 %s **title 相同**, "
                    "改成更新那一个",
                    rel_path, new_rel,
                )
                return new_rel
            # 判据 ②: slug 规范化等价 (差连字符/下划线/大小写)
            if _normalize_slug_for_dedup(q.stem) == want:
                new_rel = str(Path(rel_path).parent / q.name)
                logger.info(
                    "catfish-memory wiki 去重: %s 跟已有的 %s 规范化等价, "
                    "改成更新那一个 (LLM 每次造的拼音变体不同, 见函数注释)",
                    rel_path, new_rel,
                )
                return new_rel
    except OSError:
        pass
    # 判据 ③ (9/24): 新标题是已有条目的**别名** —— 「福富」vs 别名里写着「福富」的
    # 「中电福富信息科技有限公司」。别名是写下来的"这两个名字是同一个东西",
    # 新建等于把一个东西拆成两份, 还会让指向这个名字的关系变成"指向不明"。
    if new_title:
        sub = str(Path(rel_path).parent).replace("\\", "/")
        owners = [
            node for node in name_owners(load_nodes(catfish_home), new_title)
            if node.rel_path.rsplit("/", 1)[0] == sub
        ]
        if len(owners) == 1:
            logger.info(
                "catfish-memory wiki 去重: %s 的标题「%s」是已有 %s 的别名, 改成更新那一个",
                rel_path, new_title, owners[0].rel_path,
            )
            return owners[0].rel_path
    return rel_path


def _drop_claimed_aliases(catfish_home: Path, rel_path: str, content: str) -> str:
    """别名已被别的条目认领 (title 或 aliases) → 从这条里去掉, 并记日志。

    9/24: 两个条目认领同一个别名, 所有写这个名字的关系都会解析成"指向不明"
    (实测一个撞名让 24 条关系同时报异常)。只去掉撞的那个, 不动其它别名、不拒写。
    """
    fm, body = _split_frontmatter_body(content)
    if not fm:
        return content
    m = re.search(r"^aliases:[ \t]*\[(.*)\][ \t]*$", fm, re.MULTILINE)
    if not m:
        return content
    aliases = [a.strip().strip('"').strip("'") for a in _split_top_level(m.group(1)) if a.strip()]
    if not aliases:
        return content
    nodes = load_nodes(catfish_home)
    keep: list[str] = []
    for alias in aliases:
        owners = name_owners(nodes, alias, exclude=rel_path)
        if owners:
            logger.warning(
                "catfish-memory wiki: %s 的别名「%s」已被 %s 认领, 去掉 (两个条目共用一个别名会让关系指向不明)",
                rel_path, alias, owners[0].rel_path,
            )
            continue
        keep.append(alias)
    if len(keep) == len(aliases):
        return content
    line = "aliases: [" + ", ".join('"' + a.replace('"', "") + '"' for a in keep) + "]"
    new_fm = fm[: m.start()] + line + fm[m.end():]
    return content.replace(fm, new_fm, 1)
