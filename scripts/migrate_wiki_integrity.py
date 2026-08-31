#!/usr/bin/env python3
"""安全修复本机 wiki 的已确认问题。

默认只报告，不写文件。使用 ``--apply`` 时会先把每个改动文件复制到
``<catfish-home>/.wiki-integrity-backups/<timestamp>/``，再执行幂等修复。
不会删除 raw/sources、备份或废弃条目，也不会自动猜测缺失实体；只会根据已核实的
官方组织架构原始材料补齐一个缺失的部门实体。用户明确指定删除的孤立概念会先备份再删除，
“福富”重复实体会合并到正式公司实体后再删除。
"""

from __future__ import annotations

import argparse
import re
import shutil
from datetime import date, datetime
from pathlib import Path

EXPIRED_STATUS = "status: expired"
DATE_RE = re.compile(r"有效期至\s*[：:]?\s*(\d{4})[-年](\d{1,2})[-月](\d{1,2})日?")
LEGACY_BLOCK_RE = re.compile(
    r"\n<!-- legacy body[^>]*-->.*?(?:\n<!-- /legacy -->|\Z)", re.DOTALL
)

CONFIRMED_REWRITES = {
    "[[CMMI-5级评审项目]]": "[[CMMI-5]]",
    "[[CS4 信息系统建设及服务能力等级证书]]": "[[CS4-信息系统建设及服务能力等级证书]]",
    "[[通信工程施工总承包二级]]": "[[通信工程施工总承包(二级)]]",
    "[[raw/sources/1783318142-福富组织架构20260423]]":
        "raw/sources/1783325598-福富组织架构20260423",
    "[[raw/sources/1783325598-福富组织架构20260423]]":
        "raw/sources/1783325598-福富组织架构20260423",
    'sources: ["raw/sources/1783318142-福富组织架构20260423"]':
        'sources: ["raw/sources/1783325598-福富组织架构20260423"]',
    'aliases: ["企业发展与风控部（法律部）"]':
        'aliases: ["企业发展与风控部（法律部）", "法律部"]',
    "[[项目负责人资质拆分规则]]": "[[项目负责人资质拆分原则]]",
    "[[工作优先级排序原则]]": "[[工作优先级判断原则]]",
    "[[邮件安全分类规则]]": "[[邮件安全分类原则]]",
    "[[工作汇报确定性口吻原则]]": "",
    "concept_type: 分类类别": "concept_type: category",
    "concept_type: 统计口径": "concept_type: scope",
    "concept_type: 统计规则": "concept_type: rule",
    "concept_type: 分类框架": "concept_type: framework",
    "concept_type: 分类标准": "concept_type: standard",
    "“CMMI-5”的正式评估已定于2026年8月3日至10日进行。":
        "“CMMI-5”的正式评估已于2026年8月3日至10日完成，并已取得证书。",
}

CONFIRMED_NEW_RECORDS = {
    "wiki/entities/企业发展与风控部.md": (
        "---\n"
        "type: entity\n"
        "title: 企业发展与风控部\n"
        "entity_type: department\n"
        "created: 2026-08-28\n"
        "updated: 2026-08-28\n"
        "aliases: [\"企业发展与风控部（法律部）\", \"法律部\"]\n"
        "tags: [组织架构, 典型职能体系]\n"
        'related: [{name: "中电福富信息科技有限公司", rel: "隶属"}]\n'
        "sources: [\"raw/sources/1783325598-福富组织架构20260423\"]\n"
        "---\n\n"
        "# 企业发展与风控部\n\n"
        "福富典型职能体系一级部，别名法律部。官方组织架构原始材料记录其下属部门为战略规划中心、法律事务中心。\n\n"
        "## 来源\n\n"
        "- 来源文件：raw/sources/1783325598-福富组织架构20260423\n"
    ),
}

# 这些组织条目来自同一份已核实的组织架构原始材料，但历史导入时只写了正文，
# 没有 frontmatter。它们不是“无效节点”，不能删除；迁移时补齐最小元数据，并用
# 明确的“隶属”关系接到正式公司实体。名单按原始材料中的一级部/研究院逐项列出，
# 不从文件名推断其它业务属性。
LEGACY_ORGANIZATION_TITLES = {
    "业务软件事业部",
    "云业务事业部",
    "云网运营事业部",
    "产教融合中心",
    "产数集成交付中心",
    "人力资源部",
    "人工智能创新应用研究院",
    "低空与海洋创新应用研究院",
    "低空海洋与应急事业部",
    "保密管理办公室",
    "党群工作部",
    "北京分公司",
    "北部分公司",
    "南部分公司",
    "大数据应用事业部",
    "工业互联网事业部",
    "工业互联网研究院",
    "工会",
    "市场部",
    "总经理办公室",
    "数字政府事业部",
    "数字政府研究院",
    "数智化业务事业部",
    "智慧城市事业部",
    "智慧城市研究院",
    "智能网联事业部",
    "科创研发部",
    "网信安业务事业部",
    "西部分公司",
    "要客业务中心",
    "财务部",
    "采购中心",
    "集成能力中心",
}

LEGACY_ORGANIZATION_ALIASES = {
    "人工智能创新应用研究院": ["中国电信（福建）人工智能创新应用研究院"],
    "低空与海洋创新应用研究院": ["中国电信（福建）低空与海洋创新应用研究院"],
    "工业互联网研究院": ["中国电信（福建）工业互联网研究院"],
    "数字政府研究院": ["中国电信（福建）数字政府研究院"],
    "智慧城市研究院": ["中国电信（福建）智慧城市研究院"],
    "总经理办公室": ["总经理办公室（董事会办公室）（安全保卫部）"],
    "党群工作部": ["党群工作部（党委办公室）（统战工作部）"],
}


def _legacy_entity_frontmatter(title: str, *, today: date) -> str:
    """为有明确来源的旧组织正文生成最小、可审计的 frontmatter。"""
    aliases = LEGACY_ORGANIZATION_ALIASES.get(title, [])
    aliases_yaml = "[" + ", ".join(f'"{item}"' for item in aliases) + "]"
    return (
        "---\n"
        "type: entity\n"
        f"title: {title}\n"
        "entity_type: department\n"
        "created: 2026-04-23\n"
        f"updated: {today.isoformat()}\n"
        f"aliases: {aliases_yaml}\n"
        "tags: [组织架构]\n"
        'related: [{name: "中电福富信息科技有限公司", rel: "隶属"}]\n'
        'sources: ["raw/sources/1783325598-福富组织架构20260423"]\n'
        "---\n\n"
    )


def _repair_legacy_ontology_file(path: Path, text: str, *, today: date) -> tuple[str, str]:
    """只修复名单内的无 frontmatter 条目，未知旧文件保持不动。"""
    if path.parent.name != "entities":
        return text, ""
    title = path.stem
    if title == "企业发展与风控部" and _frontmatter(text) and "related:" not in _frontmatter(text):
        marker = re.search(r"^sources:\s*", text, re.MULTILINE)
        if marker:
            updated = (
                text[:marker.start()]
                + 'related: [{name: "中电福富信息科技有限公司", rel: "隶属"}]\n'
                + text[marker.start():]
            )
            return updated, "补齐企业发展与风控部的已确认隶属关系"
    if _frontmatter(text):
        return text, ""
    if title in LEGACY_ORGANIZATION_TITLES:
        return _legacy_entity_frontmatter(title, today=today) + text.lstrip(), "补齐组织实体元数据并接入正式公司实体"
    if title == "csmm-4":
        prefix = (
            "---\n"
            "type: entity\n"
            "title: CSMM-4\n"
            "entity_type: cert\n"
            "created: 2026-08-03\n"
            f"updated: {today.isoformat()}\n"
            "aliases: []\n"
            "tags: [资质, 软件能力成熟度]\n"
            'related: [{name: "中电福富信息科技有限公司", rel: "持有"}, '
            '{name: "CSMM-4复审答辩准备流程", rel: "对应流程"}]\n'
            "sources: []\n"
            "---\n\n"
        )
        return prefix + text.lstrip(), "补齐 CSMM-4 证书实体元数据并接入已确认关系"
    if title == "中电系资质对标对齐矩阵":
        prefix = (
            "---\n"
            "type: entity\n"
            "title: 中电系资质对标对齐矩阵\n"
            "entity_type: doc\n"
            "created: 2026-08-15\n"
            f"updated: {today.isoformat()}\n"
            "aliases: []\n"
            "tags: [资质管理, 对标分析]\n"
            'related: [{name: "对标矩阵v0731口径", rel: "采用口径"}]\n'
            'sources: ["raw/sources/1785759315-中电系资质对标对齐矩阵"]\n'
            "---\n\n"
        )
        return prefix + text.lstrip(), "补齐对标矩阵文档元数据并接入统计口径"
    return text, ""

INVALID_ONTOLOGY_FILES_TO_REMOVE = (
    "wiki/concepts/raw-sources-1783325598-福富组织架构20260423.md",
)

ORPHAN_CONCEPTS_TO_REMOVE = (
    "wiki/concepts/gongzuo-youxianji-paixu-yuanze.md",
    "wiki/concepts/youjian-fenlei-yu-youxianji-pingding-liucheng.md",
    "wiki/concepts/jie-gou-hua-shu-chu-pian-hao.md",
    "wiki/concepts/youjian-anquan-fenlei-guize.md",
    "wiki/concepts/增值服务分类标准.md",
    "wiki/concepts/zhaobiao-pingfen-guize-dinggao-yuanze.md",
    "wiki/concepts/组织架构.md",
    "wiki/concepts/zizhi-duibiao-yu-shenbao-liucheng.md",
    "wiki/concepts/zhao-tou-biao-lian-he-ti-fen-qian-fen-fu-mo-shi.md",
    "wiki/concepts/xiangmu-fuzeren-zizhi-chaifen-guize.md",
    "wiki/concepts/dai-ban-qingdan-weihu-guifan.md",
    "wiki/concepts/gongzuo-huibao-queidingxing-kouwen-yuanze.md",
    "wiki/concepts/zizhi-kenengxing-pinggu-liucheng.md",
)

FUFU_MERGED_BLOCK = """\n## 资质统计口径（2026-08-15）

“福富”原条目记录：按“知识库全量口径”统计为 56 项，其中低空经济 17 项、信通院 2 项；该口径包含低空经济及信通院资质，并剔除荣誉类 3 项及入网证类 5 项。这个数字是特定统计口径下的历史记录，不能与“对标矩阵v0731”口径下的数量直接比较。

该统计记录的来源为 `journal:2026-08-15`。\n"""


def _active_files(root: Path) -> list[Path]:
    wiki = root / "wiki"
    return sorted(
        p
        for sub in ("entities", "concepts", "queries")
        for p in (wiki / sub).glob("*.md")
        if p.is_file()
    )


def _frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---", 4)
    return text[4:end] if end >= 0 else ""


def _is_deprecated(text: str) -> bool:
    return bool(re.search(r"^deprecated:\s*(?:true|yes|1)\s*$", _frontmatter(text), re.M | re.I))


def _insert_expired_metadata(text: str, expiry: date, today: date) -> str:
    if re.search(r"^status:\s*", _frontmatter(text), re.M):
        return text
    marker = re.search(r"^updated:\s*.*$", text, re.M)
    if not marker:
        return text
    metadata = f"\n{EXPIRED_STATUS}\nexpired_at: {expiry.isoformat()}"
    return text[: marker.end()] + metadata + text[marker.end() :]


def _rewrite_file(path: Path, today: date) -> tuple[str, list[str]]:
    original = path.read_text(encoding="utf-8", errors="replace")
    updated = original
    reasons: list[str] = []

    if path.name == "zizhi-duibiao-fenxi-yuanze.md":
        cleaned = LEGACY_BLOCK_RE.sub("", updated)
        if cleaned != updated:
            updated = cleaned.rstrip() + "\n"
            reasons.append("移除已标记的重复 legacy 正文，保留当前正文")

    for old, new in CONFIRMED_REWRITES.items():
        if old in updated:
            updated = updated.replace(old, new)
            reasons.append(f"修正已确认引用/状态: {old}")

    if path.name == "youjian-anquan-fenlei-guize.md":
        if "created: 2026-07-13" in updated and "updated: 2026-07-10" in updated:
            updated = updated.replace("updated: 2026-07-10", f"updated: {today.isoformat()}", 1)
            reasons.append("修正 updated 早于 created 的元数据")

    match = DATE_RE.search(updated)
    if match and not _is_deprecated(updated):
        expiry = date(*(int(x) for x in match.groups()))
        if expiry < today and EXPIRED_STATUS not in _frontmatter(updated):
            candidate = _insert_expired_metadata(updated, expiry, today)
            if candidate != updated:
                updated = candidate
                reasons.append(f"标记已过期事实: {expiry.isoformat()}")

    return updated, reasons


def _narrow_search_scope(path: Path) -> tuple[str, list[str]]:
    if not path.is_file():
        return "", []
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    changed: list[str] = []
    out: list[str] = []
    in_include = False
    for line in lines:
        stripped = line.strip()
        if stripped == "include:":
            in_include = True
        elif (
            line
            and not line[0].isspace()
            and not stripped.startswith(("#", "-"))
        ):
            in_include = False
        if in_include and stripped == "- ~/.catfish":
            changed.append("移除会把整个 ~/.catfish 纳入文档检索的宽范围 include")
            continue
        out.append(line)
    updated = "".join(out)
    if "**/.wiki-integrity-backups" not in updated:
        scope_lines = updated.splitlines(keepends=True)
        exclude_index = next(
            (i for i, line in enumerate(scope_lines) if line.strip() == "exclude:"),
            None,
        )
        if exclude_index is not None:
            insert_at = exclude_index + 1
            while insert_at < len(scope_lines):
                line = scope_lines[insert_at]
                stripped = line.strip()
                if line and not line[0].isspace() and not stripped.startswith(("#", "-")):
                    break
                insert_at += 1
            scope_lines.insert(
                insert_at,
                "# 知识库原始材料、历史备份不作为默认回答来源\n- '**/.wiki-integrity-backups'\n",
            )
            updated = "".join(scope_lines)
        else:
            updated += "\nexclude:\n- '**/.wiki-integrity-backups'\n"
        changed.append("排除知识库迁移备份目录")
    return updated, changed


def migrate(root: Path, *, apply: bool, today: date | None = None) -> int:
    today = today or date.today()
    changes: list[tuple[Path, str, str]] = []
    deletions: list[tuple[Path, str]] = []
    for path in _active_files(root):
        original = path.read_text(encoding="utf-8", errors="replace")
        updated, reasons = _rewrite_file(path, today)
        updated, legacy_reason = _repair_legacy_ontology_file(
            path, updated, today=today
        )
        if legacy_reason:
            reasons.append(legacy_reason)
        if updated != original:
            changes.append((path, updated, "; ".join(reasons)))

    canonical = root / "wiki/entities/zhong-dian-fu-fu-xin-xi-ke-ji-you-xian-gong-si.md"
    duplicate = root / "wiki/entities/福富.md"
    if canonical.is_file() and duplicate.is_file():
        canonical_text = canonical.read_text(encoding="utf-8", errors="replace")
        merged = canonical_text
        if FUFU_MERGED_BLOCK.strip() not in merged:
            merged = merged.rstrip() + FUFU_MERGED_BLOCK
        if "journal:2026-08-15" not in merged:
            merged = merged.replace(
                'sources: ["employee_journal", "file:4607878_20260428175329936.pdf"]',
                'sources: ["employee_journal", "file:4607878_20260428175329936.pdf", "journal:2026-08-15"]',
                1,
            )
        if "updated: 2026-08-28" not in merged:
            merged = merged.replace("updated: 2026-07-27", f"updated: {today.isoformat()}", 1)
        if merged != canonical_text:
            changes.append((canonical, merged, "合并同一实体“福富”的明确统计口径到正式公司实体"))
        deletions.append((duplicate, "重复实体已合并到中电福富信息科技有限公司"))

    for relative in ORPHAN_CONCEPTS_TO_REMOVE:
        path = root / relative
        if path.is_file():
            deletions.append((path, "按用户明确要求删除孤立概念"))

    for relative in INVALID_ONTOLOGY_FILES_TO_REMOVE:
        path = root / relative
        if path.is_file():
            deletions.append((path, "原始资料路径误存为概念，移入 .trash 保留恢复路径"))

    for relative, content in CONFIRMED_NEW_RECORDS.items():
        path = root / relative
        if not path.exists():
            changes.append(
                (
                    path,
                    content,
                    "根据官方组织架构原始材料补齐缺失的部门实体",
                )
            )

    scope = root / "search-scope.yaml"
    scope_updated, scope_reasons = _narrow_search_scope(scope)
    if scope_updated and scope_updated != scope.read_text(encoding="utf-8"):
        changes.append((scope, scope_updated, "; ".join(scope_reasons)))

    if not changes and not deletions:
        print("没有发现可自动修复的已确认问题。")
        return 0

    backup_root = root / ".wiki-integrity-backups" / datetime.now().strftime("%Y%m%d-%H%M%S")
    for path, content, reason in changes:
        rel = path.relative_to(root)
        print(f"{'修复' if apply else '待修复'} {rel}: {reason}")
        if not apply:
            continue
        backup = backup_root / rel
        if path.exists():
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for path, reason in deletions:
        rel = path.relative_to(root)
        print(f"{'删除' if apply else '待删除'} {rel}: {reason}")
        if not apply:
            continue
        backup = backup_root / rel
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        trash_dir = root / "wiki" / ".trash"
        trash_dir.mkdir(parents=True, exist_ok=True)
        trash_name = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{path.name}"
        trash_path = trash_dir / trash_name
        counter = 1
        while trash_path.exists():
            trash_path = trash_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{counter}-{path.name}"
            counter += 1
        shutil.move(str(path), str(trash_path))
        print(f"  → wiki/.trash/{trash_path.name}")
    if apply:
        print(f"已完成 {len(changes)} 个修改、{len(deletions)} 个删除，备份位于 {backup_root}")
    else:
        print(f"共 {len(changes)} 个修改、{len(deletions)} 个删除待执行；确认目标无误后加 --apply")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / ".catfish")
    parser.add_argument("--apply", action="store_true", help="先备份后写入")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"知识库目录不存在: {root}")
    return migrate(root, apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
