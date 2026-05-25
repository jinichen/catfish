"""扫描 catfish/skills/ 目录, 收集所有 SKILL.md 元数据.

# 为啥需要

鲶鱼里有越来越多的 skill (leadership-briefing / weekly-report / eis-export ...),
但**LLM 不知道这些 skill 存在** — 不会主动调. 表现是: 员工说"写汇报", LLM 退化
到"我给你写 Python 脚本你跑", 因为它看不到 catfish 暴露什么 skill.

修复: 启动时扫所有 SKILL.md, 把 skill 列表 (name + description + 路径) 注入到
LLM 的 system prompt + 暴露 `catfish_run_skill` 工具让 LLM 调.

# 跟 hermes-skill 的关系

hermes-skill 在 ~/.hermes/skills/ — 那是 LLM 自己写的运行时学习. 这个
catfish/skills/ 是**我们工程团队**写的合规 skill, 必跟客户 demo / 合同绑定的.

两套并存:
  - catfish/skills/         # 工程审定 skill, 客户跑必须合规 (公文 / 合同模板)
  - ~/.hermes/skills/       # LLM 自学 skill, 员工本机灵活 (员工自己 dogfood)

LLM 同时看到两套, 优先级 catfish/skills > ~/.hermes/skills (公文必须用合规版).

# 找 skills 目录的策略

  1. 如果 CATFISH_SKILLS_DIR env 设了 → 用它
  2. 否则从本文件位置向上找, 找含 'skills/' 的 catfish 项目 root
  3. 都失败 → 返回空列表 + warning, 不 fail (兼容老部署)
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("catfish.gateway.skills_loader")


@dataclass
class SkillMeta:
    """单个 skill 的元数据."""

    skill_path: str
    """相对路径, 例: 'department/leadership-briefing'. 用作 LLM 调 run_skill 的 key."""

    name: str
    """skill 名, 来自 SKILL.md frontmatter, 例: 'leadership-briefing'."""

    description: str
    """skill 描述, 来自 frontmatter description 字段."""

    skill_md_path: Path
    """绝对路径到 SKILL.md, 给 LLM 想看完整规范时回看."""

    script_py_path: Path | None
    """同目录下的 script.py (如果存在). tool-bridge run_skill 会执行它."""

    # ── 五一 sprint Day 2: Skill 全生命周期 4 步 ──

    version: str = "0.1.0"
    """SemVer 版本字符串, 例: '1.2.3'. 没填默认 '0.1.0'.

    用途:
    - audit log 记录调用时的版本, 便于追溯 (鸿波改 SKILL.md 后看哪个 session 用了旧版)
    - 客户 IT 审 skill 时按版本对照
    - Phase 2 Skills Hub 拉新 skill 时按版本兼容性检查
    """

    deprecated: bool = False
    """是否下线. 设 true 后:
    - format_skills_block 在 LLM 看到时显示 ⚠️ 警告 + 不推荐调用
    - catfish_run_skill 调用时 result 加 'deprecated_warning' 字段提示员工
    """

    deprecated_reason: str = ""
    """下线原因, 例: '改用 v2 的 leadership-briefing-strict' 或 '客户合规变更, 不再使用'.
    deprecated=true 时建议填, 让员工/LLM 知道为啥下线 + 替代方案."""

    # ── BL-SKILL-METADATA-DYNAMIC (5/15 鸿波 '半半的工作造成更大困恼') ──
    # skill 自己声明触发词 / 类型, skill_guard 不再硬编码每个 skill 的关键词.

    triggers: tuple[str, ...] = ()
    """触发关键词. 员工 user message 含任一 trigger → skill_guard 强制 agent 调本 skill.

    例: leadership-briefing 的 triggers = ("汇报", "请示", "立项", "上报", "呈报", ...)
        guizang-ppt-magazine 的 triggers = ("PPT", "幻灯片", "slides", "deck", "杂志风", ...)

    设计约束:
    - 至少 3 个词, 否则太容易漏判
    - 不要写"做" / "帮" 这种泛词, 会跟所有 skill 撞
    - 中英都列 (员工有时打英文)
    """

    namespace: str = "catfish"
    """BL-RBAC-DAY5 (5/17): skill 命名空间, RBAC 过滤的 key.

    取值:
    - `catfish`                              — catfish 自家工程审定 (CATFISH_SKILLS_DIR)
    - `hermes:bundled`                       — hermes 装机自带 (~/.hermes/hermes-agent/skills/)
    - `hermes:github:<owner>/<repo>`         — 员工 git clone GitHub skill 到 ~/.hermes/skills/
    - `hermes:hf:<owner>/<name>`             — hermes 0.14 #26219 huggingface tap 拉的
    - `hermes:local:<name>`                  — 员工本机自写, 既无 .git 也无 hf 标记

    `qualified_name()` 返完整 `{namespace}:{skill_path}` 给 RBAC fnmatch.
    """

    kind: str = "procedural"
    """skill 类型, 影响 skill_guard 注入的铁律强度:

    - "procedural"   — script.py 自己跑出文件 (leadership-briefing / weekly-report).
                       agent 调 catfish_run_skill 就完事, 拿 files 字段给员工.
    - "instructional" — script.py 只返指令 + 模板路径, 真活由 agent 接力
                        (guizang-ppt-magazine 杂志风 PPT). agent 必须按返回的
                        post_steps 真发 read_file / write_file, 不能嘴炮.
    """

    def qualified_name(self) -> str:
        """BL-RBAC-DAY5 (5/17): 完整 namespaced name 给 RBAC fnmatch.

        catfish 自家 → 'catfish:<skill_path>'  例: 'catfish:department/weekly-report'
        hermes:bundled → 'hermes:bundled:<skill_path>'
        hermes:github:owner/repo → 'hermes:github:owner/repo'  (整个 namespace 就是 name)
        hermes:hf:owner/name → 'hermes:hf:owner/name'
        hermes:local:name → 'hermes:local:name'

        gateway 端 user.can_use_skill(qualified_name()) 按 user.effective_allowed_skills
        的 glob 匹配.
        """
        # hermes:* 类已经在 namespace 里编码完整 path 了 (除了 bundled), 不重复拼
        if self.namespace.startswith("hermes:github:") or \
           self.namespace.startswith("hermes:hf:") or \
           self.namespace.startswith("hermes:local:"):
            return self.namespace
        if self.namespace == "hermes:bundled":
            return f"hermes:bundled:{self.skill_path}"
        # catfish 自家
        return f"catfish:{self.skill_path}"


# ── 找 skills root ──────────────────────────────────────────────


def _hermes_skills_root() -> Path:
    """BL-RBAC-DAY5: hermes user-level skills (~/.hermes/skills/).

    跟 _find_skills_root() 平行. catfish 自家是工程审定 skill,
    hermes 这边是员工自加 (git clone / hermes 0.14 huggingface tap / 本机自写).
    """
    return Path.home() / ".hermes" / "skills"


def _infer_hermes_skill_namespace(skill_dir: Path) -> str:
    """BL-RBAC-DAY5: 推 ~/.hermes/skills/<name>/ 这种 hermes skill 的 namespace.

    规则:
    - 有 .git/config 且 remote.origin.url 含 github.com → `hermes:github:<owner>/<repo>`
    - 有 .hf-skill 或 frontmatter 标 hf → `hermes:hf:<owner>/<name>` (hermes 0.14 #26219)
    - 都没有 → `hermes:local:<name>` (员工本机自写)
    """
    name = skill_dir.name

    # 推 GitHub
    git_config = skill_dir / ".git" / "config"
    if git_config.exists():
        try:
            text = git_config.read_text(encoding="utf-8", errors="ignore")
            # 找 remote.origin.url = https://github.com/owner/repo[.git]
            m = re.search(
                r"url\s*=\s*(?:https?://github\.com/|git@github\.com:)"
                r"([^/\s]+)/([^/\s]+?)(?:\.git)?(?:\s|$)",
                text,
            )
            if m:
                owner, repo = m.group(1), m.group(2)
                return f"hermes:github:{owner}/{repo}"
        except OSError:
            pass

    # 推 HuggingFace (hermes 0.14 huggingface tap 留的标记)
    # 实际 hermes 0.14 怎么标 hf-sourced skill 待 audit, 先按猜测
    hf_marker = skill_dir / ".hf-skill"
    if hf_marker.exists():
        try:
            content = hf_marker.read_text(encoding="utf-8").strip()
            # 期望格式 "owner/name"
            if "/" in content:
                return f"hermes:hf:{content}"
        except OSError:
            pass

    # fallback: 本机 local
    return f"hermes:local:{name}"


def _find_skills_root() -> Path | None:
    """
    1. 环境变量 CATFISH_SKILLS_DIR
    2. 从本文件向上搜 catfish 项目 root (含 skills/ 目录的)
    3. 失败返 None
    """
    env_path = os.environ.get("CATFISH_SKILLS_DIR")
    if env_path:
        p = Path(env_path).expanduser()
        if p.exists() and p.is_dir():
            return p
        logger.warning("CATFISH_SKILLS_DIR=%s 不存在或不是目录", env_path)

    # 从 __file__ 向上找 — gateway 装在 catfish/central/llm-gateway/src/
    # 上溯找含 catfish/skills/department/ 的位置
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "skills"
        if candidate.is_dir() and (candidate / "department").is_dir():
            return candidate
        # 也兼容 parent 自己就叫 catfish (开发模式)
        if (parent / "skills" / "department").is_dir():
            return parent / "skills"

    return None


# ── 解析 SKILL.md ───────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.+?)\n---\s*\n?", re.DOTALL)


def _parse_skill_md(skill_md: Path) -> dict[str, Any] | None:
    """从 SKILL.md frontmatter 拿 {name, description, version, deprecated, deprecated_reason}.

    拿不到 (没 frontmatter / 缺 name / yaml 失败) 返 None.

    五一 sprint Day 2: 加 version/deprecated/deprecated_reason 字段解析,
    老 skill 没填的字段走 SkillMeta 默认值 (version=0.1.0, deprecated=False).
    """
    try:
        text = skill_md.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("无法读取 %s: %s", skill_md, e)
        return None

    match = _FRONTMATTER_RE.match(text)
    if not match:
        logger.debug("%s 没有 yaml frontmatter, 跳过", skill_md)
        return None

    try:
        front = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as e:
        logger.warning("%s frontmatter yaml 解析失败: %s", skill_md, e)
        return None

    name = str(front.get("name", "")).strip()
    description = str(front.get("description", "")).strip()
    if not name or not description:
        logger.debug("%s frontmatter 缺 name 或 description", skill_md)
        return None

    # version: SemVer 字符串. 老 skill 没填默认 "0.1.0".
    version = str(front.get("version", "0.1.0")).strip() or "0.1.0"

    # deprecated: bool. 没填默认 False.
    deprecated_raw = front.get("deprecated", False)
    deprecated = bool(deprecated_raw) if deprecated_raw is not None else False

    deprecated_reason = str(front.get("deprecated_reason", "")).strip()

    # BL-SKILL-METADATA-DYNAMIC: triggers + kind
    # triggers 可以是 list 或 csv 字符串. 全部归一成 tuple[str, ...]
    raw_triggers = front.get("triggers", [])
    if isinstance(raw_triggers, str):
        # "PPT, 杂志风, slides" — 容错
        triggers = tuple(t.strip() for t in raw_triggers.split(",") if t.strip())
    elif isinstance(raw_triggers, list):
        triggers = tuple(str(t).strip() for t in raw_triggers if str(t).strip())
    else:
        triggers = ()

    kind = str(front.get("kind", "procedural")).strip().lower()
    if kind not in ("procedural", "instructional"):
        logger.warning(
            "%s frontmatter kind=%r 不合法, 回落 'procedural'", skill_md, kind,
        )
        kind = "procedural"

    return {
        "name": name,
        "description": description,
        "version": version,
        "deprecated": deprecated,
        "deprecated_reason": deprecated_reason,
        "triggers": triggers,
        "kind": kind,
    }


# ── 主入口 ──────────────────────────────────────────────────────


def discover_skills() -> list[SkillMeta]:
    """扫描 skills 目录, 返回所有合法 skill 的元数据列表.

    BL-RBAC-DAY5 (5/17): 扫两路 — catfish 自家 + hermes user-level (~/.hermes/skills/).
    每条 SkillMeta 含 namespace 字段, qualified_name() 给 RBAC fnmatch.

    catfish 自家 (CATFISH_SKILLS_DIR / 默认): namespace='catfish'
    hermes user-level: namespace='hermes:github:<o/r>' / 'hermes:hf:<o/n>' /
                       'hermes:local:<n>' (推导 _infer_hermes_skill_namespace)

    返按 qualified_name 字母序.
    """
    results: list[SkillMeta] = []

    # ── catfish 自家 skill ──
    catfish_root = _find_skills_root()
    if catfish_root is not None:
        for skill_md in sorted(catfish_root.rglob("SKILL.md")):
            try:
                rel = skill_md.relative_to(catfish_root).parent
            except ValueError:
                continue
            skill_path = str(rel).replace(os.sep, "/")

            parsed = _parse_skill_md(skill_md)
            if parsed is None:
                continue

            script_py = skill_md.parent / "script.py"
            results.append(
                SkillMeta(
                    skill_path=skill_path,
                    name=parsed["name"],
                    description=parsed["description"],
                    skill_md_path=skill_md,
                    script_py_path=script_py if script_py.exists() else None,
                    version=parsed["version"],
                    deprecated=parsed["deprecated"],
                    deprecated_reason=parsed["deprecated_reason"],
                    triggers=parsed["triggers"],
                    kind=parsed["kind"],
                    namespace="catfish",
                )
            )
    else:
        logger.info(
            "skills_loader: 没找到 catfish skills 目录 (CATFISH_SKILLS_DIR 没设, "
            "也没扫到默认路径). 跳过 catfish 自家 skill."
        )

    # ── hermes user-level skill (~/.hermes/skills/) ──
    # BL-RBAC-DAY5: 扫员工自加的 skill (git clone GitHub / hermes 0.14 hf tap / 本机自写)
    hermes_root = _hermes_skills_root()
    if hermes_root.is_dir():
        # ~/.hermes/skills/ 下每个**直接**子目录算一个 skill (SKILL.md 在根)
        # hermes 自带的 bundled skill 在 ~/.hermes/hermes-agent/skills/ 不在这里
        for entry in sorted(hermes_root.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                continue

            parsed = _parse_skill_md(skill_md)
            if parsed is None:
                continue

            namespace = _infer_hermes_skill_namespace(entry)
            script_py = entry / "script.py"
            results.append(
                SkillMeta(
                    skill_path=entry.name,
                    name=parsed["name"],
                    description=parsed["description"],
                    skill_md_path=skill_md,
                    script_py_path=script_py if script_py.exists() else None,
                    version=parsed["version"],
                    deprecated=parsed["deprecated"],
                    deprecated_reason=parsed["deprecated_reason"],
                    triggers=parsed["triggers"],
                    kind=parsed["kind"],
                    namespace=namespace,
                )
            )

    # 字母排序按 qualified_name (RBAC 角度便于看)
    results.sort(key=lambda s: s.qualified_name())

    logger.info(
        "skills_loader: 发现 %d 个 skill (catfish=%d, hermes:github=%d, "
        "hermes:hf=%d, hermes:local=%d)",
        len(results),
        sum(1 for s in results if s.namespace == "catfish"),
        sum(1 for s in results if s.namespace.startswith("hermes:github:")),
        sum(1 for s in results if s.namespace.startswith("hermes:hf:")),
        sum(1 for s in results if s.namespace.startswith("hermes:local:")),
    )
    return results


# BL-SKILLS-TIER1-SHRINK (5/25 鸿波, "skill 越来越多会爆"): Anthropic Progressive
# Disclosure 推荐单 skill metadata 30-60 tokens, 老格式 60-120 tokens (2x). 收紧策略:
#   1. description 截到 SKILL_DESC_CAP 字符 (Tier 1 摘要), 详情走 _help (Tier 2)
#   2. 每 skill 1 行 (老的 5 行), 5x 行数压缩
#   3. _help 提示挪 header 一次说, 不再每 skill 重复
# 100 skill 时从 ~12K → ~3K tokens. 详见 docs/SKILL-PROGRESSIVE-DISCLOSURE.md.
SKILL_DESC_CAP = 80


def _shorten_description(description: str, cap: int = SKILL_DESC_CAP) -> str:
    """单行 cap 截断 (Tier 1 metadata), 详细说明走 _help (Tier 2)."""
    flat = " ".join(description.split())  # 折叠换行 + 多空白
    if len(flat) <= cap:
        return flat
    return flat[:cap].rstrip() + "…"


# BL-SKILLS-TIER1-FOLD (5/25 鸿波): Phase 2 — 按 namespace 分组渲染, 让模型在
# 50+ skill 时一眼看清"哪些是工程审定 (优先调) / 哪些是员工自学". 5 个 namespace
# 按优先级排, catfish 排首位 (合规/审定/客户绑定 → 模型应优先选).
_NAMESPACE_ORDER = (
    "catfish",
    "hermes:bundled",
    "hermes:github",
    "hermes:hf",
    "hermes:local",
)

_NAMESPACE_LABELS: dict[str, tuple[str, str]] = {
    "catfish":         ("🎯", "工程审定, 客户合规, **优先调**"),
    "hermes:bundled":  ("🧰", "hermes 装机自带"),
    "hermes:github":   ("🐱", "git clone 装"),
    "hermes:hf":       ("🤗", "HuggingFace tap 装"),
    "hermes:local":    ("📝", "员工本机自写"),
}


def _namespace_group_key(skill: SkillMeta) -> str:
    """把 'hermes:github:owner/repo' 归一到 'hermes:github' 用作 group key.

    catfish 已经是裸 'catfish', hermes:bundled 也已经裸, 不动.
    带 owner/repo 的 hermes:github:* / hermes:hf:* / hermes:local:* 全归 ns 大类.
    """
    ns = skill.namespace
    for prefix in ("hermes:github", "hermes:hf", "hermes:local"):
        if ns == prefix or ns.startswith(prefix + ":"):
            return prefix
    return ns  # catfish / hermes:bundled / 未知 → 保原


def _group_skills_by_namespace(skills: list[SkillMeta]) -> dict[str, list[SkillMeta]]:
    """{namespace_label: [SkillMeta]}, 内部保 caller 给的相对顺序 (discover 已排过)."""
    groups: dict[str, list[SkillMeta]] = {}
    for s in skills:
        key = _namespace_group_key(s)
        groups.setdefault(key, []).append(s)
    return groups


def _render_skill_line(s: SkillMeta) -> str:
    """单 skill 渲染成 1 行 (Phase 1 收紧后的格式)."""
    version_tag = f" v{s.version}" if s.version != "0.1.0" else ""
    if s.deprecated:
        warn_text = s.deprecated_reason or "已下线, 不推荐调用"
        warn_short = _shorten_description(warn_text, cap=SKILL_DESC_CAP)
        return (
            f"- `{s.skill_path}` — {s.name}{version_tag} "
            f"⚠️ DEPRECATED: {warn_short}"
        )
    desc_short = _shorten_description(s.description)
    return f"- `{s.skill_path}` — {s.name}{version_tag}: {desc_short}"


# BL-SKILLS-RAG (5/25 鸿波): Phase 3 — BM25 retrieval. skill 数超 RAG_THRESHOLD 时,
# 注入只露 top-K 个最匹配 user query 的 + 其余按 namespace 折成 count. 关键: 模型
# 想找更多 → 调 catfish_search_skills(query) 工具 (tool-bridge 后续 ship).
RAG_THRESHOLD = 30
RAG_TOP_K = 15

# BL-SKILLS-VECTOR (5/25 鸿波 "开干"): retrieval backend 切换. env 控.
#   - "bm25"   (默认): 走 BM25Index, 零外部依赖, char-level CJK, 关键词精确匹配好
#   - "vector": 走 VectorIndex (bge-m3 embed), 语义相似好 ("汇报" 找到 "述职报告")
#   - "hybrid": vector + BM25 双跑, RRF 融合排序 (鲁棒性高)
# 任何 mode 失败 → 自动回退 BM25 (高可用). 详见 skills_vector.py + docs/SKILL-PROGRESSIVE-DISCLOSURE.md
SKILLS_RETRIEVAL_MODE_ENV = "CATFISH_SKILLS_RETRIEVAL"
SKILLS_RETRIEVAL_DEFAULT = "bm25"

# 缓存 — vector backend 启动一次 embed 全 skill (50ms-5s 看规模), 不该每次 rank 重建.
# fingerprint 跟 skills mtime 一起算 (skills 变 → 重建).
_vector_cache: dict = {"fingerprint": None, "index": None, "backend": None}


def format_skills_block(
    skills: list[SkillMeta],
    user_query: str | None = None,
    rag_threshold: int = RAG_THRESHOLD,
    top_k: int = RAG_TOP_K,
) -> str:
    """渲染成 system prompt skill catalog (Tier 1 metadata).

    BL-SKILLS-TIER1-SHRINK (5/25): Anthropic Progressive Disclosure 模式 —
    Tier 1 只露 name + 一句话 desc, Tier 2 (完整 SKILL.md) 模型决定调时调 _help 拿,
    Tier 3 (参考文件) skill 执行中按需 read_file.

    BL-SKILLS-TIER1-FOLD (5/25): Phase 2 — 按 namespace 分组渲染.
    catfish 排首位 (工程审定, 优先调), hermes:* 各成段. 模型看分组优先级 +
    一行 metadata, 选 skill 后 _help 拿详情.

    BL-SKILLS-RAG (5/25): Phase 3 — 当 skill 数 > rag_threshold (30) 且有 user_query
    时, 用 BM25 算 top_k (15) 个最相关 skill 注入, 其余折成 namespace count 行.
    模型想找更多 → catfish_search_skills(query) 工具.

    输出长度:
      - skills <= 30: 全部 grouped 渲染 (Phase 1+2 行为)
      - skills >  30: top_K grouped + 折叠 footer "还有 N 个 skill, 调 catfish_search_skills"

    详见 docs/SKILL-PROGRESSIVE-DISCLOSURE.md.
    """
    if not skills:
        return ""

    lines = [
        "",
        "## 🔧 catfish 工程审定 skill (铁律!! 用 catfish_run_skill 工具调用)",
        "",
        "### ⚠ 不可违反的硬规则",
        "",
        "1. **员工要求生成 .docx / .xlsx / .pptx / .pdf 文档**, 你**必须**先扫下方 "
        "skill 列表 — 任何一个 skill 的描述能 cover 该需求, **必须**调用 "
        "`catfish_run_skill`.",
        "2. **绝对禁止**回答\"我无法访问您的文件系统 / 我给您写个 Python 脚本 / "
        "请您复制并运行\". 你**有** `catfish_run_skill` 工具, 它就是用来生成文件的.",
        "3. **关键词模糊匹配**: 员工说\"汇报\" / \"汇报材料\" / \"请示\" / \"立项\" / "
        "\"决策事项\" / \"上报\" / \"呈报\" / \"工作总结\"等任何向**上级**提交的 .docx, "
        "都走 leadership-briefing skill — 即使员工没说\"用 skill\" / \"用模板\".",
        "4. **不知道参数先 _help (重要!)**: 下面 skill 列表是 Tier 1 摘要 "
        f"(每条 ≤ {SKILL_DESC_CAP} 字), 详细参数 schema 一定用 "
        "`catfish_run_skill(skill_path='...', params={'_help': True})` 拿. "
        "**别基于摘要瞎传参**.",
        "5. **跟员工对话补全参数**: skill 需要的字段员工没全部说? "
        "主动**反问**他, 一次问 1-2 个, 不要等员工一次性给齐.",
        "6. **多个 namespace 时优先 catfish**: 同样能搞定时, 优先选 catfish 的 "
        "(工程审定 + 客户合规), 不要选 hermes:local (员工本机自学未审定).",
        "",
        "### 调用方式",
        "",
        "```",
        "catfish_run_skill(skill_path='<path>', params={'_help': True})  # 先看 schema",
        "catfish_run_skill(skill_path='<path>', params={...真参数...})    # 再真跑",
        "```",
        "",
        "返回 `{ok, files, summary}`. files 字段里的路径 Companion 会自动渲染成 "
        "可点击 pill 给员工.",
        "",
        f"### 当前可用 skill ({len(skills)} 个, 详情用 _help 拿)",
        "",
    ]

    # BL-SKILLS-RAG (Phase 3): skill 多 + 有 user_query → 只渲染 top-K 个最相关的.
    # rendered_skills 是要 inline 展示的 (catfish 全留 + 其余 ns 留 top-K), folded 是
    # 折成 namespace count 的 (catfish 之外的, 没进 top-K 的 hermes skill).
    rendered_skills, folded_namespaces = _select_skills_for_render(
        skills, user_query, rag_threshold, top_k
    )

    # BL-SKILLS-TIER1-FOLD: 按 namespace 分组. catfish 排首, hermes:* 各成段.
    groups = _group_skills_by_namespace(rendered_skills)
    for ns_key in _NAMESPACE_ORDER:
        ns_skills = groups.get(ns_key)
        if not ns_skills:
            continue
        emoji, hint = _NAMESPACE_LABELS[ns_key]
        lines.append(f"#### {emoji} {ns_key} ({len(ns_skills)} 个 — {hint})")
        lines.append("")
        for s in ns_skills:
            lines.append(_render_skill_line(s))
        lines.append("")

    # 兜底: 未知 namespace (理论不该出现, 但写入纪律强一点 — 不丢)
    unknown_keys = sorted(set(groups.keys()) - set(_NAMESPACE_ORDER))
    for ns_key in unknown_keys:
        ns_skills = groups[ns_key]
        lines.append(f"#### ❓ {ns_key} ({len(ns_skills)} 个 — 未知 namespace)")
        lines.append("")
        for s in ns_skills:
            lines.append(_render_skill_line(s))
        lines.append("")

    # BL-SKILLS-RAG: 折叠区 — 没进 top-K 的按 namespace 报 count + 提示用工具搜.
    if folded_namespaces:
        lines.append("#### 🗂 折叠区 (按相关性筛掉, 想用调 catfish_search_skills)")
        lines.append("")
        for ns_key, count in folded_namespaces:
            emoji, hint = _NAMESPACE_LABELS.get(ns_key, ("❓", "未知 ns"))
            lines.append(f"- {emoji} `{ns_key}`: 还有 {count} 个 skill ({hint})")
        lines.append("")
        lines.append(
            "**找不到合适的? 调 `catfish_search_skills(query='...')` 在全部 skill 里搜.**"
        )
        lines.append("")

    return "\n".join(lines)


def _retrieval_mode() -> str:
    """env CATFISH_SKILLS_RETRIEVAL → mode. 未知值 → 默认 bm25."""
    mode = os.environ.get(SKILLS_RETRIEVAL_MODE_ENV, SKILLS_RETRIEVAL_DEFAULT).lower()
    if mode not in ("bm25", "vector", "hybrid"):
        logger.warning(
            "%s=%r 不合法, 回 'bm25'. 合法值: bm25 / vector / hybrid",
            SKILLS_RETRIEVAL_MODE_ENV, mode,
        )
        return SKILLS_RETRIEVAL_DEFAULT
    return mode


def _skills_fingerprint(skills: list[SkillMeta]) -> str:
    """skill 集合 fingerprint, 用 skill_path + mtime."""
    parts = []
    for s in skills:
        try:
            mt = s.skill_md_path.stat().st_mtime
        except OSError:
            mt = 0
        parts.append(f"{s.skill_path}:{mt}")
    return "|".join(parts)


def _build_vector_index(other_skills: list[SkillMeta]):
    """启动一次 embed 全 skill, 缓 _vector_cache. 返 VectorIndex 或 None (失败).

    设计:
      - skills 集合不变 → cache hit, 直接返
      - 集合变 (新装/删/修 SKILL.md mtime) → 重建
      - embed_fn 调用挂 (bge-m3 不可达 / API 错) → 返 None, caller fallback BM25
    """
    fp = _skills_fingerprint(other_skills)
    if (_vector_cache["fingerprint"] == fp
            and _vector_cache["backend"] == "vector"
            and _vector_cache["index"] is not None):
        return _vector_cache["index"]

    # 懒 import — skills_vector 依赖 numpy, 不该在模块顶部 import (有的部署可能没 numpy)
    try:
        from .skills_vector import VectorIndex, make_litellm_embed_fn  # noqa: PLC0415
    except ImportError as e:
        logger.warning("BL-SKILLS-VECTOR: skills_vector 模块 import 失败 (%s), fallback BM25", e)
        return None

    # 找 embed 配置 — env 优先, 缺省取 catfish-private-embed
    embed_model = os.environ.get("CATFISH_SKILLS_EMBED_MODEL", "openai/bge-m3")
    embed_api_base = os.environ.get("CATFISH_SKILLS_EMBED_BASE", "") or None
    embed_api_key = os.environ.get("CATFISH_SKILLS_EMBED_KEY", "") or None

    docs = [
        f"{s.skill_path} {s.name} {s.description}"
        for s in other_skills
    ]
    try:
        embed_fn = make_litellm_embed_fn(
            model_name=embed_model,
            api_base=embed_api_base,
            api_key=embed_api_key,
        )
        idx = VectorIndex(docs, embed_fn=embed_fn)
    except Exception as e:
        logger.warning("BL-SKILLS-VECTOR: 建 VectorIndex 挂 (%s), fallback BM25", e)
        return None

    _vector_cache["fingerprint"] = fp
    _vector_cache["index"] = idx
    _vector_cache["backend"] = "vector"
    logger.info(
        "BL-SKILLS-VECTOR: VectorIndex 建好, %d skill, dim=%s, model=%s",
        len(docs), getattr(idx, "_dim", "?"), embed_model,
    )
    return idx


def _rrf_fuse(
    bm25_ranked: list[tuple[int, float]],
    vector_ranked: list[tuple[int, float]],
    k: int = 60,
    top_k: int = 15,
) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion — hybrid mode 用. k=60 是 TREC 默认.

    score(doc) = sum over rankers: 1 / (k + rank_of_doc_in_ranker)

    比简单 cosine + BM25 score 加权更鲁棒 (两个 ranker 的 score 量纲完全不同).
    """
    rrf_scores: dict[int, float] = {}
    for ranked in (bm25_ranked, vector_ranked):
        for rank, (doc_idx, _score) in enumerate(ranked):
            rrf_scores[doc_idx] = rrf_scores.get(doc_idx, 0.0) + 1.0 / (k + rank + 1)
    fused = sorted(rrf_scores.items(), key=lambda x: -x[1])
    return fused[:top_k]


def _select_skills_for_render(
    skills: list[SkillMeta],
    user_query: str | None,
    rag_threshold: int,
    top_k: int,
) -> tuple[list[SkillMeta], list[tuple[str, int]]]:
    """BL-SKILLS-RAG: 决定哪些 skill inline 渲染, 哪些折叠.

    规则:
      1. catfish skill 永远全 inline (工程审定, 关键, 总展示)
      2. skill 总数 <= rag_threshold OR 没 user_query → 全 inline, 不折叠
      3. skill 总数 > threshold + 有 query → 走 retrieval (BM25/vector/hybrid),
         catfish 全留, hermes skill 只留前 top_k, 其余按 ns 折叠成 count

    BL-SKILLS-VECTOR (5/25): retrieval backend env CATFISH_SKILLS_RETRIEVAL 切.
    vector / hybrid 失败 → 自动回退 BM25 (高可用).
    """
    # 总 skill 数低 OR 没 query → 全 inline
    if len(skills) <= rag_threshold or not user_query or not user_query.strip():
        return skills, []

    # 分: catfish 留, 其他走 retrieval
    catfish_skills = [s for s in skills if s.namespace == "catfish"]
    other_skills = [s for s in skills if s.namespace != "catfish"]

    if not other_skills:
        return skills, []

    # 算 ranked = list of (doc_idx_in_other_skills, score). retrieval mode 切 backend.
    from .skills_retrieval import BM25Index, tokenize  # noqa: PLC0415
    bm25_docs = [
        tokenize(f"{s.skill_path} {s.name} {s.description}")
        for s in other_skills
    ]
    bm25_idx = BM25Index(bm25_docs)
    bm25_ranked = bm25_idx.rank(user_query, top_k=top_k)

    mode = _retrieval_mode()
    if mode == "bm25":
        ranked = bm25_ranked
    else:
        # vector / hybrid 都要建 vector index, 失败 → fallback bm25
        v_idx = _build_vector_index(other_skills)
        if v_idx is None:
            logger.info("BL-SKILLS-VECTOR: vector index 不可用, 这轮回退 BM25")
            ranked = bm25_ranked
        else:
            vector_ranked = v_idx.rank(user_query, top_k=top_k)
            if mode == "vector":
                ranked = vector_ranked
            else:  # hybrid
                ranked = _rrf_fuse(bm25_ranked, vector_ranked, top_k=top_k)

    kept_indices = {i for i, _score in ranked}
    kept_others = [s for i, s in enumerate(other_skills) if i in kept_indices]
    folded_others = [s for i, s in enumerate(other_skills) if i not in kept_indices]

    # folded 按 ns 数 count
    fold_counts: dict[str, int] = {}
    for s in folded_others:
        key = _namespace_group_key(s)
        fold_counts[key] = fold_counts.get(key, 0) + 1
    folded_list = sorted(fold_counts.items(), key=lambda x: -x[1])

    return catfish_skills + kept_others, folded_list


# ── 单元测试 hook ───────────────────────────────────────────────


def _reset_for_tests() -> None:
    """让测试可以替换 skills_root 环境."""
    pass


__all__ = [
    "SkillMeta",
    "discover_skills",
    "format_skills_block",
]
