"""改技能要过验证题: 原来答对的题改完答错了, 改动撤回 (11/27 鸿波「做门槛, 做完测一遍精度」)。

# 为什么

小鲶自己改技能 (hermes skill_manage), 两个月 753 次, 结算核对一个技能 178 次、从 4 万字长到 7.9 万字,
约 100 次是定时任务 / 系统触发的轮次里改的, 没人看着。11/27 拿员工「记住 / 以后都这样 / 不对应该…」的原话
出了 47 道题 (简单 32 + 复杂 15), 线上方式精度 90%。答错的都能指到具体的坏改动:
  · 资质通报技能正文里小鲶自己加了条「教训」, 说集成交付运营部是「一级部、无二级部」—— 跟员工 11/2
    定的「集成能力中心的二级部」正好相反 (参考文件和脚本里是对的);
  · 「福建建设从业人员考试计划」技能 6/30 被「并进」另一个技能 (实际什么都没并进去), 后来那个也没了,
    员工纠正过的查询网址哪都找不到。
EvoOntology (arXiv:2609.15779) 消融: 进化不加「验证分数不降才保留」这道门, 掉 11.2 分。

# 做法 (换个员工也成立, 不靠手写的题; 出题 / 判分 / 两道检查在 skill_eval_learn.py)

  · 挂在 hermes 的 tool_execution 中间件上 (钩子限时 30 秒, 跑题会超时, 见 tool_execution_middleware)。
    改之前 (skill_manage / write_file / patch 改到技能): 先把技能目录拷一份 (跳过 node_modules / .git / 大文件)。
    删技能: 技能里有员工定的口径 (「员工 X/X 定」这类行 / 验证题) → 没写 absorbed_into 且员工这一轮没说删, 拦;
    写了 absorbed_into → 逐条核对口径在那个技能里还在不在, 不在就拦。
  · 改之后 (SKILL.md 变了才跑, 改 references / scripts 不花钱):
      1. 员工这一轮说了口径 → 照原话出待确认的题 / 照新口径改旧题 (先改题再验, 员工要的改动不会被旧题拦回去);
         定时任务 / 系统触发的轮次没有员工的话, 不出题不改题;
      2. 新加的内容跟技能里员工定的规则直接矛盾 (两道: 先找, 再逐条复核) → 撤回;
      3. 员工确认过的题: 用员工 picker 选的模型按新内容作答, 判分先按要点、没命中再让模型对着标准答案判;
         原来答对、改完两次都错 → 撤回;
    撤回 = 按文件恢复成改之前, 工具结果换成哪条口径 / 哪道题、员工原话、现在答成什么, 让模型改对了再改。
  · 题放在 ~/.catfish/skill_evals/<技能名>.json (skill_manage 碰不到, 模型删不了题), 员工在 Companion
    「技能 → 验证题」里确认 / 改答案 / 删; 老员工点「从历史对话出题」(= 本文件 mine 子命令) 一次补出来。
  · 跑不成 (没 picker 模型 / 网关报错): 改动照常生效, 结果里明说「没验证」。不换别的模型兜底 (鸿波 7/22 军规)。
  · 终端 / execute_code 直接改文件的绕得过去, 这里管不到 —— 模型改技能 99% 走 skill_manage (11/27 统计)。

# 11/27 验证 (鸿波本机历史, qwen-flash)

  手写 47 道题: 修技能前线上方式精度 91.3%, 修了题指出的 5 处后 95.7% (同 46 题)。
  模型判分 vs 人工: 168 个回答 165 个一致; 自称「技能里没有」却给答案的改成直接判错 (模型会判对)。
  矛盾检查: 故意写反的 10 条 + 修前那条坏教训, 抓到 10/11 (漏的那条坏教训验证题抓得到);
            30 次历史真实改动 (带当轮员工原话) 误报 2 次, 都是拿更晚定的规则查早先的改动 (测试用的是现在的技能)。
  真 hermes 上: tests/test_skill_eval_gate_on_real_hermes.py (撤回 / 放行 / 拦删 / 验证过 30 秒)。详见 docs/SKILL-EVAL-GATE.md。

流量记成 catfish_source=plugin:skill-eval (「数据外发记录」里叫「技能验证题」), 不扣员工配额 (X-Catfish-Internal)。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("catfish-xcatfish-user.skill_eval_gate")

SOURCE = "plugin:skill-eval"
_MUTATING = {"create", "edit", "patch", "write_file", "remove_file", "delete"}
_FILE_TOOLS = {"write_file", "patch"}
_WORKERS = 6
_TIMEOUT = 180.0
_SNAPS: dict[str, list[dict[str, Any]]] = {}
#: 拷快照时跳过 (video-shotcraft 一个 node_modules 就 524MB); 撤回也不碰这些
_SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv"}
_SKIP_BYTES = 2 * 1024 * 1024
_LOCK = threading.Lock()

ANSWER_SYSTEM = ("你是鲶鱼 Catfish 的 AI 同事「小鲶」。回答要简短、直接给结论。"
                 "按下面技能里的口径回答；技能里没有的就说没有，不要编。\n\n# 技能 {name}\n{text}")
#: 自称技能里没有却给了答案 = 猜的, 一律判错
GLOBAL_NOT = ("技能里没有专门", "技能里没有这个", "技能里没固化", "技能里没有固化", "没有这个口径")


# ── 路径 ──────────────────────────────────────────────────────────

def _catfish_home() -> Path:
    env = os.environ.get("CATFISH_HOME", "").strip()
    return Path(env).expanduser() if env else Path.home() / ".catfish"


def _skills_root() -> Path:
    return _learn().hermes_home() / "skills"


def evals_dir() -> Path:
    return _catfish_home() / "skill_evals"


def _safe(name: str) -> str:
    return re.sub(r"[^\w.\-]", "_", name.split("/")[-1])


def load_evals(name: str) -> Optional[dict[str, Any]]:
    p = evals_dir() / f"{_safe(name)}.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("questions") else None


def save_evals(name: str, data: dict[str, Any]) -> None:
    p = evals_dir() / f"{_safe(name)}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _log(entry: dict[str, Any]) -> None:
    try:
        evals_dir().mkdir(parents=True, exist_ok=True)
        with (evals_dir() / "gate_log.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def skill_dir(name: str) -> Optional[Path]:
    """hermes 自己的 _find_skill 优先 (分类目录 / 软链都认); 不在 hermes 进程里就自己找。"""
    base = name.split("/")[-1]
    try:
        from tools.skill_manager_tool import _find_skill  # noqa: PLC0415
        found = _find_skill(base)
        if found and found.get("path"):
            return Path(found["path"])
    except Exception:  # noqa: BLE001
        pass
    for root, _dirs, files in os.walk(_skills_root(), followlinks=True):
        if "SKILL.md" in files and Path(root).name == base:
            return Path(root)
    return None


def skill_for_path(path: str) -> Optional[str]:
    """文件工具改的路径落在哪个技能里 (往上找到有 SKILL.md 的目录)。"""
    try:
        p = Path(path).expanduser().resolve()
        roots = [_skills_root().resolve()] + [Path(os.path.realpath(e.path)) for e in os.scandir(_skills_root())
                                               if e.is_symlink()]
    except OSError:
        return None
    if not any(str(p).startswith(str(r)) for r in roots):
        return None
    for d in [p] + list(p.parents):
        if (d / "SKILL.md").is_file():
            return d.name
        if d in roots:
            break
    return None


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_skill(d: Optional[Path]) -> str:
    try:
        return (d / "SKILL.md").read_text(encoding="utf-8") if d else ""
    except OSError:
        return ""


# ── 判分: 要点先, 没命中再让模型对着标准答案判 ──────────────────────────────

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").replace("**", "").replace("`", "")
    s = re.sub(r"(?<=\d),(?=\d{3})", "", s)
    return re.sub(r"\s+", "", s)


def _hit(pat: str, a: str) -> bool:
    if pat.startswith("re:"):
        return re.search(pat[3:].replace(" ?", ""), a, re.I) is not None
    return _norm(pat).lower() in a.lower()


def keys_grade(q: dict[str, Any], answer: str) -> tuple[bool, str]:
    """只按要点判 (不问模型)。没有要点的题 → (False, "没有要点")。"""
    if not q.get("must"):
        return False, "没有要点"
    a = _norm(answer)
    for p in list(q.get("must_not") or []) + list(GLOBAL_NOT):
        if _hit(p, a):
            return False, f"出现了不该有的说法「{p}」"
    for group in q.get("must") or []:
        if not any(_hit(p, a) for p in group):
            return False, "缺要点「" + " / ".join(group) + "」"
    return True, ""


def grade(q: dict[str, Any], answer: str, chat: Optional[Any] = None) -> tuple[bool, str]:
    """要点全命中就算对 (省一次调用); 要点没命中、或这道题没有要点 → 模型对着标准答案判。
    11/27: 光按要点会冤枉对的 (「第二批第1次」对不上「第二批第一次」), 也认不出别人的说法。"""
    ok, why = keys_grade(q, answer)
    if ok or chat is None or why.startswith("出现了不该有的说法"):
        # 自称「技能里没有」却给了答案 / 说了明确不许的说法: 直接判错 (11/27 验证: 模型判分会把这种猜对的判成对)
        return ok, why
    if not q.get("must"):
        a = _norm(answer)
        hit = next((p for p in GLOBAL_NOT if _hit(p, a)), None)
        if hit:
            return False, f"出现了不该有的说法「{hit}」"
    return _learn().judge(chat, q, answer)


# ── 调模型 (员工 picker 选的模型, 经本机网关) ──────────────────────────

def _env_key(key: str) -> str:
    v = os.environ.get(key, "").strip()
    if v:
        return v
    env = _skills_root().parent / ".env"
    try:
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def eval_model() -> str:
    try:
        data = json.loads((_catfish_home() / "picker_state.json").read_text(encoding="utf-8"))
        return str(data.get("chat_model") or "").strip()
    except (OSError, ValueError, AttributeError):
        return ""


def _gateway() -> str:
    return (_env_key("CATFISH_GATEWAY_URL") or "http://127.0.0.1:8999").rstrip("/")


def chat_with(model: str):
    """→ chat(messages, max_tokens) -> 文本, 走员工选的模型。"""
    def chat(messages: list[dict[str, str]], max_tokens: int) -> str:
        import httpx  # noqa: PLC0415
        headers = {"Authorization": f"Bearer {_env_key('OPENAI_API_KEY') or _env_key('CATFISH_INTERNAL_DEV_TOKEN')}",
                   "X-Catfish-Skip-Identity": "true", "X-Catfish-Internal": "true", "X-Catfish-Source": SOURCE}
        user = _env_key("CATFISH_DEFAULT_USER")
        if "@" in user:
            headers["X-Catfish-User"] = user
        body = {"model": model, "temperature": 0, "max_tokens": max_tokens, "stream": False, "messages": messages}
        err = ""
        for i in range(2):
            try:
                with httpx.Client(timeout=_TIMEOUT, trust_env=False) as c:
                    r = c.post(f"{_gateway()}/v1/chat/completions?catfish_source={SOURCE}", json=body, headers=headers)
                if r.status_code == 200:
                    return (r.json().get("choices") or [{}])[0].get("message", {}).get("content") or ""
                err = f"网关 {r.status_code}: {r.text[:160]}"
            except Exception as e:  # noqa: BLE001
                err = str(e)[:160]
            time.sleep(2 * (i + 1))
        raise RuntimeError(err)
    return chat


def answer(question: str, name: str, text: str, model: str) -> str:
    return chat_with(model)([{"role": "system", "content": ANSWER_SYSTEM.format(name=name, text=text)},
                             {"role": "user", "content": question}], 1500)


def confirmed(data: dict[str, Any]) -> list[dict[str, Any]]:
    """拦改动只用员工确认过的题 (手工出的、首页点过确认的); 待确认的不拦。"""
    return [q for q in data.get("questions") or [] if q.get("status", "confirmed") == "confirmed"]


def run(name: str, text: str, questions: list[dict[str, Any]], model: str,
        retry: Optional[set[str]] = None) -> dict[str, dict[str, Any]]:
    """→ {题号: {ok, why, answer}}。retry 里的题第一次错了再答一遍 (temperature 0 也会抖)。"""
    chat = chat_with(model)

    def one(q: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        tries = 2 if retry and q["id"] in retry else 1
        res: dict[str, Any] = {}
        for _ in range(tries):
            ans = answer(q["q"], name, text, model)
            ok, why = grade(q, ans, chat)
            res = {"ok": ok, "why": why, "answer": ans}
            if ok:
                break
        return q["id"], res
    if not questions:
        return {}
    with ThreadPoolExecutor(min(_WORKERS, len(questions))) as ex:
        return dict(ex.map(one, questions))


def baseline(name: str, data: dict[str, Any], text: str, model: str) -> set[str]:
    """改之前的内容答对哪些题。基线是同一模型、同一内容、同一批题算的就直接用, 否则现算一遍记下。"""
    qs = confirmed(data)
    sig = _sha(json.dumps([[q["id"], q.get("gold", "")] for q in qs], ensure_ascii=False))
    b = data.get("baseline") or {}
    if b.get("model") == model and b.get("sha") == _sha(text) and b.get("questions") == sig:
        return set(b.get("passed") or [])
    res = run(name, text, qs, model)
    passed = {k for k, v in res.items() if v["ok"]}
    data["baseline"] = {"model": model, "sha": _sha(text), "questions": sig, "passed": sorted(passed),
                        "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    return passed


def check(name: str, data: dict[str, Any], before: str, after: str, model: str) -> list[dict[str, Any]]:
    """→ 退步的题 (改之前对、改之后两次都错)。没退步就把基线换成改之后的 (data 改了, 调用方落盘)。"""
    qs = confirmed(data)
    if not qs:
        return []
    passed = baseline(name, data, before, model)
    res = run(name, after, qs, model, retry=passed)
    worse = [{"id": q["id"], "q": q["q"], "gold": q.get("gold", ""), "why": res[q["id"]]["why"],
              "answer": res[q["id"]]["answer"][:300]} for q in qs if q["id"] in passed and not res[q["id"]]["ok"]]
    if not worse:
        sig = _sha(json.dumps([[q["id"], q.get("gold", "")] for q in qs], ensure_ascii=False))
        data["baseline"] = {"model": model, "sha": _sha(after), "questions": sig, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "passed": sorted(k for k, v in res.items() if v["ok"])}
    return worse


def _worse_text(name: str, worse: list[dict[str, Any]], conf: list[dict[str, str]]) -> str:
    lines = [f"「{name}」这次改动跟员工定的口径对不上, 已撤回 (技能恢复成改之前的样子):"]
    for c in conf[:5]:
        lines.append(f"- 新加的「{str(c.get('added'))[:120]}」跟员工定的「{str(c.get('rule'))[:160]}」矛盾 ({c.get('why', '')})")
    for w in worse[:5]:
        lines.append(f"- 验证题「{w['q']}」原来答对, 改完答错了。\n  员工定的: {w['gold']}\n  改完答成: {w['answer'][:160]} ({w['why']})")
    lines.append("把这些口径保留在技能里再改 (别删、别写反)。员工要是改了口径, 请员工明说新的口径, 再改一次就会照员工的话更新。")
    return "\n".join(lines)


# ── 钩子 ──────────────────────────────────────────────────────────

def _targets(tool_name: str, args: dict[str, Any]) -> list[tuple[str, str, str]]:
    """→ [(技能名, 动作, absorbed_into)]。所有技能都管 (没题的也做矛盾检查 / 删并检查)。"""
    out: list[tuple[str, str, str]] = []
    if tool_name == "skill_manage":
        ops = args.get("operations") if isinstance(args.get("operations"), list) else [args]
        for o in ops:
            if not isinstance(o, dict):
                continue
            n = str(o.get("name") or args.get("name") or "").split("/")[-1]
            act = str(o.get("action") or args.get("action") or "")
            if n and act in _MUTATING:
                out.append((n, act, str(o.get("absorbed_into") or "")))
    elif tool_name in _FILE_TOOLS:
        n = skill_for_path(str(args.get("path") or args.get("file_path") or ""))
        if n:
            out.append((n, "file", ""))
    seen: set[str] = set()
    return [t for t in out if not (t[0] in seen or seen.add(t[0]))]


def pre_tool_call_hook(tool_name: str = "", args: Optional[dict] = None, tool_call_id: str = "",
                       session_id: str = "", **_kw: Any) -> Optional[dict]:
    try:
        targets = _targets(str(tool_name or ""), args or {})
        if not targets:
            return None
        key = tool_call_id or uuid.uuid4().hex
        snaps = []
        for name, act, absorbed in targets:
            d = skill_dir(name)
            if act == "delete":
                msg = _delete_verdict(name, load_evals(name) or {}, d, absorbed, session_id)
                if msg:
                    _log({"skill": name, "action": "delete", "blocked": True, "why": msg[:300]})
                    return {"action": "block", "message": msg}
                continue
            snap = evals_dir() / ".snap" / key / _safe(name)
            shutil.rmtree(snap, ignore_errors=True)
            if d is not None:
                d = d.resolve()
                _snapshot(d, snap)
            snaps.append({"name": name, "dir": str(d) if d else "", "snap": str(snap), "before": _read_skill(d)})
        if snaps:
            with _LOCK:
                _SNAPS[key] = snaps
        return None
    except Exception as e:  # noqa: BLE001 — 门槛自己出错不能挡住工具
        logger.warning("skill_eval_gate pre hook: %s", e)
        return None


_DELETE_CUE = re.compile(r"删|不要了|不用了|去掉这个技能|废弃")


def _delete_verdict(name: str, data: dict[str, Any], d: Optional[Path], absorbed: str, session_id: str) -> str:
    L = _learn()
    rules = L.rule_lines(_read_skill(d), data.get("questions") or [])
    if not rules:
        return ""
    if not absorbed:
        words = L.recent_employee_words(L.default_state_db(), session_id, 1)
        if words and _DELETE_CUE.search(words[0]):
            return ""
        return (f"「{name}」里有员工定的口径 ({len(rules)} 条), 直接删就没了。要合并就先把口径并进另一个技能, "
                f"再用 delete + absorbed_into=<那个技能> 删; 只是不想要了, 先问员工, 员工说删再删。")
    model = eval_model()
    target = skill_dir(absorbed)
    if target is None:
        return f"要并进的「{absorbed}」找不到, 「{name}」先别删。"
    if not model:
        return f"「{name}」里有员工定的口径, 员工没在鲶鱼里选模型, 没法核对「{absorbed}」里是不是都有了, 先别删。"
    try:
        gone = L.missing(chat_with(model), rules, _read_skill(target))
    except Exception as e:  # noqa: BLE001
        return f"核对「{absorbed}」有没有收下「{name}」的口径没跑成 ({str(e)[:100]}), 先别删。"
    if gone:
        return (f"「{name}」并进「{absorbed}」后, 这些员工定的口径在「{absorbed}」里找不到, 没删:\n" +
                "\n".join(f"- {g}" for g in gone) + f"\n先把它们写进「{absorbed}」再删。")
    return ""


def transform_tool_result_hook(tool_name: str = "", args: Optional[dict] = None, result: Any = None,
                               tool_call_id: str = "", session_id: str = "", **_kw: Any) -> Optional[str]:
    with _LOCK:
        snaps = _SNAPS.pop(tool_call_id, None) if tool_call_id else None
    if not snaps:
        return None
    try:
        if _failed(result):
            return None
        return _verify(snaps, args or {}, result, session_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("skill_eval_gate result hook: %s", e)
        return None
    finally:
        for s in snaps:
            shutil.rmtree(Path(s["snap"]).parent, ignore_errors=True)


def _verify(snaps: list[dict[str, Any]], args: dict[str, Any], result: Any, session_id: str) -> Optional[str]:
    L = _learn()
    model = eval_model()
    notes, reverted = [], []
    words = L.recent_employee_words(L.default_state_db(), session_id)
    for s in snaps:
        d = Path(s["dir"]) if s["dir"] else skill_dir(s["name"])
        after = _read_skill(d)
        if after == s["before"]:
            continue
        data = load_evals(s["name"]) or {"skill": s["name"], "questions": []}
        if not model:
            if data["questions"] or L.rule_lines(s["before"], []):
                notes.append(f"员工没在鲶鱼里选模型, 「{s['name']}」这次改动没验证。")
            continue
        chat = chat_with(model)
        try:
            if words:  # 员工这一轮的话: 出新题 / 照新口径改旧题 (先改题再验, 员工要的改动不会被旧题拦回去)
                notes += L.apply_learned(data, L.learn(chat, s["name"], words, L.added_text(s["before"], after),
                                                       data["questions"]), origin="auto", session_id=session_id)
            conf = L.conflicts(chat, L.rule_lines(s["before"], data["questions"]), L.added_text(s["before"], after), words)
            worse = check(s["name"], data, s["before"], after, model)
        except Exception as e:  # noqa: BLE001
            notes.append(f"「{s['name']}」的验证没跑成 ({str(e)[:120]}), 这次改动没验证。")
            _log({"skill": s["name"], "error": str(e)[:300]})
            continue
        if data["questions"]:
            save_evals(s["name"], data)
        if conf or worse:
            if d is not None and Path(s["snap"]).exists():
                _restore(d, Path(s["snap"]))
            elif d is not None and not s["before"]:
                shutil.rmtree(d, ignore_errors=True)  # 这次新建的技能, 撤回就是不建
            reverted.append(_worse_text(s["name"], worse, conf))
            _log({"skill": s["name"], "reverted": True, "worse": [w["id"] for w in worse],
                  "conflicts": [c.get("rule", "")[:80] for c in conf], "model": model, "session_id": session_id})
        else:
            _log({"skill": s["name"], "reverted": False, "questions": len(confirmed(data)), "model": model,
                  "learned": len(notes), "session_id": session_id})
    if reverted:
        return json.dumps({"success": False, "error": "\n\n".join(reverted + notes)}, ensure_ascii=False)
    if notes:
        return _with_note(result, " ".join(notes))
    return None


def tool_execution_middleware(tool_name: str = "", args: Optional[dict] = None, next_call: Any = None,
                              tool_call_id: str = "", session_id: str = "", **_kw: Any) -> Any:
    """挂在 hermes 的 tool_execution 中间件上 (包住工具执行本身)。

    不挂 pre_tool_call / transform_tool_result 钩子: hermes 给这两个钩子限时 (plugins.hook_callback_timeout,
    默认 30 秒), 超时就丢下不等 —— 结算核对技能 16 道题一跑就过 30 秒, 小鲶会以为改成功了, 后台却还在验、
    验完再悄悄撤回。中间件不限时, 改技能这一步要等验完才返回 (11/27 真 hermes 上核过, 见
    tests/test_skill_eval_gate_on_real_hermes.py)。"""
    args = args if isinstance(args, dict) else {}
    key = tool_call_id or uuid.uuid4().hex
    if str(tool_name or "") not in ("skill_manage",) + tuple(_FILE_TOOLS):
        return next_call(args)
    verdict = pre_tool_call_hook(tool_name, args, tool_call_id=key, session_id=session_id)
    if verdict and verdict.get("action") == "block":
        return json.dumps({"success": False, "error": verdict.get("message", "")}, ensure_ascii=False)
    try:
        result = next_call(args)
    except Exception:
        with _LOCK:
            snaps = _SNAPS.pop(key, None) or []
        for s in snaps:
            shutil.rmtree(Path(s["snap"]).parent, ignore_errors=True)
        raise
    replaced = transform_tool_result_hook(tool_name, args, result, tool_call_id=key, session_id=session_id)
    return replaced if replaced is not None else result


_LEARN_MOD: list[Any] = []


def _learn():
    """skill_eval_learn.py 跟本文件一样按路径单独加载 (插件不是真包, 不能相对 import)。"""
    if not _LEARN_MOD:
        import importlib.util  # noqa: PLC0415
        spec = importlib.util.spec_from_file_location("_catfish_xcatfish_user_skill_eval_learn",
                                                      Path(__file__).with_name("skill_eval_learn.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _LEARN_MOD.append(mod)
    return _LEARN_MOD[0]


def _failed(result: Any) -> bool:
    try:
        parsed = json.loads(result) if isinstance(result, str) else result
    except (TypeError, ValueError):
        return False
    return isinstance(parsed, dict) and (parsed.get("success") is False or bool(parsed.get("error")))


def _with_note(result: Any, note: str) -> Optional[str]:
    try:
        parsed = json.loads(result) if isinstance(result, str) else dict(result)
        parsed["skill_eval_note"] = note
        return json.dumps(parsed, ensure_ascii=False)
    except (TypeError, ValueError):
        return f"{result}\n\n[{note}]" if isinstance(result, str) else None


def _files(root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x not in _SKIP_DIRS]
        for f in files:
            p = Path(base) / f
            try:
                if p.is_symlink() or p.stat().st_size > _SKIP_BYTES:
                    continue
            except OSError:
                continue
            out[str(p.relative_to(root))] = p
    return out


def _snapshot(d: Path, snap: Path) -> None:
    for rel, p in _files(d).items():
        (snap / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, snap / rel)


def _restore(d: Path, snap: Path) -> None:
    """按文件还原: 快照里有的拷回去, 这次新冒出来的删掉; 跳过的大文件 / 依赖目录不动。"""
    old = _files(snap)
    for rel, p in _files(d).items():
        if rel not in old:
            p.unlink(missing_ok=True)
    for rel, p in old.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, d / rel)
    try:
        from agent.prompt_builder import clear_skills_system_prompt_cache  # noqa: PLC0415
        clear_skills_system_prompt_cache(clear_snapshot=True)
    except Exception:  # noqa: BLE001
        pass


def mine_history(only: Optional[set[str]] = None) -> dict[str, int]:
    """老员工冷启动: 从历史对话里挖待确认的题 (Companion 首页卡片里确认)。命令行: python skill_eval_gate.py mine [技能...]"""
    model = eval_model()
    if not model:
        raise RuntimeError("员工没在鲶鱼里选模型")
    evals_dir().mkdir(parents=True, exist_ok=True)
    return _learn().mine(chat_with(model), load_evals_any, save_evals, only=only)


def load_evals_any(name: str) -> Optional[dict[str, Any]]:
    """跟 load_evals 一样, 但没有题的文件也读 (挖题时从空开始)。"""
    p = evals_dir() / f"{_safe(name)}.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "mine":
        print(json.dumps(mine_history(set(sys.argv[2:]) or None), ensure_ascii=False))
