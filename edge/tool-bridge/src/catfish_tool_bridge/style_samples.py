"""文书风格 = 员工自己指定的范文 + 小鲶从范文里总结的几条写法特点 (11/12, 替掉 style_fingerprint.py)。

# 为什么换 (鸿波 11/12「文书风格合理吗」→「按指定范文做」)

老的「自动学」从本机搜索索引里取最近 500 篇文档抽统计特征。实盘查出来:
  · 搜索范围只有 ~/.catfish/outputs (小鲶自己生成的) 和 uploads (员工发来让小鲶读的附件),
    500 篇里 395 篇是小鲶的产出 —— 学的是小鲶自己, 不是员工;
  · 抽出来的是主题词 (服务 / 公司 / 审核) 和碎片 (一条待办勾选项、一行带手机号的联系人) 当「样本句」;
  · 模型 30 天一次没查过。
谁写的只有员工自己知道, 所以改成员工挑: 3–5 篇自己写得满意的周报 / 汇报放进来。

# 存什么 (~/.catfish/style_samples/, 只在本机)

  index.json  {"samples": [{id, name, chars, added_at}], "traits": [str], "traits_at": ts, "traits_from": [id]}
  <id>.txt    范文正文 (抽好的文字, 最多 MAX_SAMPLE_CHARS)

# 谁用

  · Companion「你的文书风格」页: 加 / 删范文, 让小鲶总结写法特点 (summarize, 会把范文发给模型, 页面上写明), 改特点
    —— JSON-RPC style/* (server.py), 总结那一步 Rust 注入员工令牌;
  · 模型: catfish-memory 插件在员工让写正式文书的那一轮, 把特点 + 范文片段带进上下文
    (hermes-plugins/catfish-memory/catfish_memory_profile.py render_style);
    工具 catfish_style_fingerprint_get 名字不变 (中心端白名单 / 三个文书技能都引用它), 返回同一份东西。
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Any

from .doc_text import extract_text

STYLE_DIR = Path.home() / ".catfish" / "style_samples"
LEGACY_FINGERPRINT = Path.home() / ".catfish" / "style_fingerprint.json"

MAX_SAMPLES = 5
MIN_SAMPLE_CHARS = 300          # 太短看不出写法
MAX_SAMPLE_CHARS = 30_000       # 存盘上限
SUMMARIZE_CHARS = 5_000         # 每篇发给模型的上限
MAX_TRAITS = 8
MAX_TRAIT_LEN = 80
EXCERPT_CHARS = 400
ALLOWED_SUFFIXES = (".docx", ".pdf", ".txt", ".md")
_ID = re.compile(r"[a-z0-9]{8,32}")


def _index_path() -> Path:
    return STYLE_DIR / "index.json"


def _load() -> dict[str, Any]:
    try:
        d = json.loads(_index_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    d["samples"] = [s for s in d.get("samples") or [] if isinstance(s, dict) and (STYLE_DIR / f"{s.get('id')}.txt").is_file()]
    d["traits"] = [t for t in d.get("traits") or [] if isinstance(t, str) and t.strip()]
    d.setdefault("traits_at", None)
    d["traits_from"] = list(d.get("traits_from") or [])
    return d


def _save(d: dict[str, Any]) -> None:
    STYLE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _index_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_index_path())


def _text(sample_id: str) -> str:
    try:
        return (STYLE_DIR / f"{sample_id}.txt").read_text(encoding="utf-8")
    except OSError:
        return ""


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t　]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def excerpt(text: str, limit: int = EXCERPT_CHARS) -> str:
    """范文里挑成段的正文 (不要标题、表格行、清单项、联系方式), 凑到 limit 字。"""
    out, n = [], 0
    for para in re.split(r"\n+", text):
        p = para.strip()
        if len(p) < 30 or p.startswith(("|", "- [", "#")) or p.count("|") >= 3:
            continue
        if not re.search(r"[。；！？]", p) or re.search(r"1\d{10}|@\w+\.", p):
            continue
        room = limit - n
        if room < 60:
            break
        out.append(p if len(p) <= room else p[:room].rstrip() + "…")
        n += len(out[-1])
    return "\n".join(out)


def state() -> dict[str, Any]:
    """页面用: 范文列表 + 写法特点 + 特点是不是按现在这几篇总结的。"""
    d = _load()
    ids = [s["id"] for s in d["samples"]]
    return {
        "samples": d["samples"],
        "traits": d["traits"],
        "traits_at": d["traits_at"],
        # 范文换过了, 特点还是按旧的那几篇总结的
        "stale": bool(d["samples"]) and (not d["traits"] or sorted(ids) != sorted(d["traits_from"])),
        "max_samples": MAX_SAMPLES,
    }


def add(params: dict[str, Any]) -> dict[str, Any]:
    """{stage_id, name}: 界面把文件分片传到群聊的暂存区 (colleague_rooms_bigfiles.stage), 这里抽出文字存成范文。"""
    from .colleague_rooms_bigfiles import staged  # noqa: PLC0415

    name = Path(str(params.get("name") or "")).name.strip()
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"范文只收 {' / '.join(ALLOWED_SUFFIXES)} —— 老 .doc 请先另存为 .docx")
    src = staged(str(params.get("stage_id") or ""))
    try:
        d = _load()
        if len(d["samples"]) >= MAX_SAMPLES:
            raise ValueError(f"最多放 {MAX_SAMPLES} 篇范文, 先删掉一篇再加")
        STYLE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STYLE_DIR / f"incoming{suffix}"
        shutil.copyfile(src, tmp)
        try:
            text, why = extract_text(tmp)
        finally:
            tmp.unlink(missing_ok=True)
        text = _clean(text)[:MAX_SAMPLE_CHARS]
        if not text:
            raise ValueError(f"「{name}」读不出文字: {why or '里面没有文字'}")
        if len(text) < MIN_SAMPLE_CHARS:
            raise ValueError(f"「{name}」只有 {len(text)} 个字, 太短看不出写法 —— 换一篇完整的 (至少 {MIN_SAMPLE_CHARS} 字)")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if any(s.get("digest") == digest for s in d["samples"]):
            raise ValueError(f"「{name}」已经在范文里了")
        sid = secrets.token_hex(6)
        (STYLE_DIR / f"{sid}.txt").write_text(text, encoding="utf-8")
        d["samples"].append({"id": sid, "name": name, "chars": len(text), "added_at": time.time(), "digest": digest})
        _save(d)
    finally:
        src.unlink(missing_ok=True)
    return state()


def remove(params: dict[str, Any]) -> dict[str, Any]:
    sid = str(params.get("id") or "")
    if not _ID.fullmatch(sid):
        raise ValueError("范文编号不对")
    d = _load()
    d["samples"] = [s for s in d["samples"] if s["id"] != sid]
    (STYLE_DIR / f"{sid}.txt").unlink(missing_ok=True)
    if not d["samples"]:
        d["traits"], d["traits_at"], d["traits_from"] = [], None, []   # 没有范文了, 特点也不留
    _save(d)
    return state()


def _norm_traits(items: Any) -> list[str]:
    out = []
    for t in items if isinstance(items, list) else []:
        t = re.sub(r"\s+", " ", str(t)).strip().lstrip("-•·0123456789.、) ）").strip()
        if t and t not in out:
            out.append(t[:MAX_TRAIT_LEN])
    return out[:MAX_TRAITS]


def set_traits(params: dict[str, Any]) -> dict[str, Any]:
    """员工自己改写法特点 (页面上一行一条)。"""
    d = _load()
    if not d["samples"]:
        raise ValueError("先放至少一篇范文")
    d["traits"] = _norm_traits(params.get("traits"))
    d["traits_at"] = time.time()
    d["traits_from"] = [s["id"] for s in d["samples"]]
    _save(d)
    return state()


def clear(_params: dict[str, Any] | None = None) -> dict[str, Any]:
    if STYLE_DIR.exists():
        shutil.rmtree(STYLE_DIR)
    return state()


_SYSTEM = (
    "下面是同一位员工自己写的几篇公文 / 汇报。请总结他**怎么写** (不是写了什么): "
    "开头怎么起、结构怎么分 (用不用「一、二、三」或小标题)、句子长短、语气、常用的收尾方式、数字和结论怎么摆。"
    "输出 4 到 6 条, 每条一句话、30 字以内, 是别人照着就能写的写法, 例如「开头一句话先说结论」。"
    "不要写主题、项目名、人名、单位名, 不要写「内容详实」这类评价。"
    "只输出一个 JSON 数组, 元素是字符串。"
)


def _parse_traits(out: str) -> list[str]:
    out = re.sub(r"<think>.*?</think>", "", out or "", flags=re.S)
    m = re.search(r"\[.*\]", out, flags=re.S)
    if m:
        try:
            return _norm_traits(json.loads(m.group(0)))
        except ValueError:
            pass
    return _norm_traits([ln for ln in out.splitlines() if ln.strip()])


async def summarize(params: dict[str, Any]) -> dict[str, Any]:
    """让小鲶读范文、总结写法特点。会把范文正文 (每篇前 SUMMARIZE_CHARS 字) 发给模型 —— 页面上写明了, 员工点了才做。"""
    from .gateway_llm import call_llm  # noqa: PLC0415

    token = str(params.get("auth_token") or "").strip()
    gw = str(params.get("gateway_url") or "").strip().rstrip("/")
    if not token or not gw:
        raise ValueError("没登录 (拿不到访问令牌), 登录后再让小鲶总结")
    d = _load()
    if not d["samples"]:
        raise ValueError("先放至少一篇范文")
    body = "\n\n".join(f"【范文 {i}】\n{_text(s['id'])[:SUMMARIZE_CHARS]}" for i, s in enumerate(d["samples"], 1))
    out = await call_llm([{"role": "system", "content": _SYSTEM}, {"role": "user", "content": body}],
                         source="plugin:toolbridge-style-samples", gateway_url=gw, auth_token=token,
                         temperature=0.2, max_tokens=1200, timeout_s=150.0)
    traits = _parse_traits(out)
    if len(traits) < 2:
        raise RuntimeError("小鲶这次没总结出来, 再点一次试试")
    d = _load()   # 等模型的这段时间员工可能删过范文
    d["traits"], d["traits_at"], d["traits_from"] = traits, time.time(), [s["id"] for s in d["samples"]]
    _save(d)
    return state()


def for_model() -> dict[str, Any]:
    """模型 / 文书技能拿的: 写法特点 + 最多 3 段范文片段。没放范文 = exists False。"""
    d = _load()
    if not d["samples"]:
        return {"exists": False, "hint": "员工还没放范文 (Companion 设置 → 鲶鱼对你的认识 → 你的文书风格)。正常写, 不要编他的风格。"}
    excerpts = []
    for s in d["samples"][:3]:
        ex = excerpt(_text(s["id"]))
        if ex:
            excerpts.append({"from": s["name"], "text": ex})
    return {"exists": True, "traits": d["traits"], "excerpts": excerpts, "sample_count": len(d["samples"]),
            "usage": "照这些写法特点写; 范文片段只学行文方式, 不要抄里面的内容和数字。"}


# ── 模型工具 (名字沿用 catfish_style_fingerprint_*) ─────────────────────────

def style_fingerprint_get(_args: dict[str, Any]) -> dict[str, Any]:
    return {"type": "result", "result": for_model()}


def style_fingerprint_refresh(_args: dict[str, Any]) -> dict[str, Any]:
    return {"type": "result", "result": {
        "ok": False,
        "hint": "文书风格现在来自员工自己指定的范文, 不再自动扫文档。请员工到 Companion 设置 → 鲶鱼对你的认识 → 你的文书风格 里加范文。",
    }}


def style_fingerprint_clear(_args: dict[str, Any]) -> dict[str, Any]:
    clear()
    LEGACY_FINGERPRINT.unlink(missing_ok=True)
    return {"type": "result", "result": "已清掉范文和写法特点"}
