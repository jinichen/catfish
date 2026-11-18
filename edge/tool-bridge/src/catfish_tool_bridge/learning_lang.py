"""外语 (11/18, docs/LEARNING-PLAN.md §6 / 第 5 步): 情景对话 + 跟读 + 复盘做卡。

    learn/lang_start {id, activity: dialogue|shadow, scene?}   开一段: 对话 → 小鲶扮角色说第一句; 跟读 → 出 6–10 句
    learn/lang_say   {id, text, secs?}                         对话里他说一句 (🎤 或打字) → 小鲶纠错 + 接着说
    learn/lang_shadow {id, idx, heard, secs?}                  跟读一句: 他读出来转的字 → 逐词对照 (不调模型)
    learn/lang_end   {id}                                      结束 → 复盘 (要记的说法、要紧的错) + 复习卡

朗读用系统语音 (界面 lib/speak.ts, 按语言挑声音); 🎤 转写: 中文 / 英语用会议组件 (FunASR, 实测英语句子一字不差),
日语等其他语言要高精度转写组件 (Qwen3-ASR; Rust speech_stop_and_transcribe_lang)。跟读对照只看「读出来的字对不对得上」,
不是口音打分 (本机没有可靠的发音评分模型), 界面上写明。

记录: lang_scene {activity, role, setting}  lang_line {who: role|learner, text, zh?, secs?}  lang_fix {items}
      shadow_set {sentences}  shadow_result {idx, target, heard, missed, extra, score}  lang_recap {...}
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any

from . import learning_prompts as P
from . import learning_store as store

LANGS = {"en": "英语", "ja": "日语", "ko": "韩语", "fr": "法语", "de": "德语", "es": "西班牙语", "ru": "俄语", "zh": "中文"}
ACTIVITIES = ("dialogue", "shadow")
_WORD = re.compile(r"[\w']+", re.U)
_CJK_OR_WORD = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]|[a-z0-9']+")


def lang_of(meta: dict) -> str:
    return (meta.get("lang") or {}).get("code") or "en"


def _system(meta: dict, part: str, fmt: str) -> str:
    code = lang_of(meta)
    return "\n\n".join([P.LANG_BASE, f"目标语言: {LANGS.get(code, code)} ({code})。员工想练: {meta['goal']}", part, fmt])


def tokens(text: str, code: str) -> list[str]:
    """对照用的单位: 有空格的语言按词 (小写、去标点); 日语 / 韩语 / 中文按字。"""
    t = (text or "").lower()
    if code in ("ja", "ko", "zh"):
        return _CJK_OR_WORD.findall(t)
    return _WORD.findall(t)


def compare(target: str, heard: str, code: str) -> dict[str, Any]:
    """读出来的 vs 原句: 漏读 / 读错 (原句里没对上的)、多出来的、对上的比例。"""
    a, b = tokens(target, code), tokens(heard, code)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    matched = sum(bl.size for bl in sm.get_matching_blocks())
    missed, extra = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("delete", "replace"):
            missed.extend(a[i1:i2])
        if tag in ("insert", "replace"):
            extra.extend(b[j1:j2])
    return {"missed": missed[:30], "extra": extra[:30], "score": round(matched / len(a), 2) if a else 0.0}


def _check_dialogue(o: dict, opening: bool) -> list[str]:
    from .learning import _heal  # noqa: PLC0415
    errs = []
    if not str(o.get("line") or "").strip():
        errs.append("要有 line (你的台词)")
    if opening and not str(o.get("setting") or "").strip():
        errs.append("开场要写 setting (情景)")
    fx = _heal(o.get("fixes"))
    if not opening and fx is not None and not isinstance(fx, list):
        errs.append("fixes 要是列表 (没有就 [])")
    return errs


def _check_shadow(o: dict) -> list[str]:
    from .learning import _heal  # noqa: PLC0415
    ss = _heal(o.get("sentences"))
    if not isinstance(ss, list) or not 4 <= len(ss) <= 12:
        return ["sentences 要 4–12 句"]
    bad = [i for i, x in enumerate(ss, 1) if not (isinstance(x, dict) and str(x.get("text") or "").strip())]
    return [f"第 {', '.join(map(str, bad[:5]))} 句没有 text"] if bad else []


def _check_recap(o: dict) -> list[str]:
    from .learning import _heal  # noqa: PLC0415
    errs = []
    for k in ("expressions", "mistakes", "cards"):
        if not isinstance(_heal(o.get(k)), list):
            errs.append(f"{k} 要是列表 (没有就 [])")
    if not str(o.get("summary") or "").strip():
        errs.append("要写 summary")
    return errs


def _dialogue_text(evs: list[dict]) -> str:
    out = []
    for ev in evs:
        if ev.get("kind") == "lang_scene":
            out = [f"情景: {ev.get('setting', '')} (你扮演: {ev.get('role', '')})"]
        elif ev.get("kind") == "lang_line":
            out.append(("你 (角色): " if ev["who"] == "role" else "员工: ") + ev["text"])
    return "\n".join(out[-40:])


def _session_text(evs: list[dict]) -> str:
    out = []
    for ev in evs:
        k = ev.get("kind")
        if k == "lang_line":
            out.append(("角色: " if ev["who"] == "role" else "员工: ") + ev["text"])
        elif k == "lang_fix":
            out += [f"  纠错: {x.get('said')} → {x.get('better')} ({x.get('why')})" for x in ev.get("items") or []]
        elif k == "shadow_result":
            out.append(f"跟读: 「{ev['target']}」读成「{ev['heard']}」, 没对上: {' '.join(ev['missed'])}")
    return "\n".join(out[-120:])


SOURCE = "plugin:toolbridge-learning-lang"


async def step(d: Path, meta: dict, evs: list[dict], llm_kw: dict) -> None:
    llm_kw = {**llm_kw, "source": SOURCE}
    from .learning import _call, _heal  # noqa: PLC0415
    lg = meta.setdefault("lang", {"code": "en"})
    if lg.get("ending"):
        out = await _call([{"role": "system", "content": _system(meta, P.LANG_RECAP, P.FORMAT_LANG_RECAP)},
                           {"role": "user", "content": "这次练习的记录:\n" + _session_text(evs)}], _check_recap, llm_kw)
        cards = [c for c in (_heal(out["cards"]) or []) if isinstance(c, dict) and c.get("question") and c.get("answer")]
        n = 0
        if cards:
            from . import learning_cards, practice  # noqa: PLC0415
            con = learning_cards._practice_con(str(d.parents[2]))
            try:
                n = practice.add_cards(con, [{**c, "source": f"外语: {meta['goal'][:60]}"} for c in cards], f"learn:{d.name}",
                                       [LANGS.get(lg["code"], lg["code"]), meta["goal"][:20]])["added"]
            finally:
                con.close()
        store.append(d, {"role": "tutor", "kind": "lang_recap", "summary": str(out["summary"])[:1500],
                         "expressions": [x for x in _heal(out["expressions"]) if isinstance(x, dict)][:15],
                         "mistakes": [x for x in _heal(out["mistakes"]) if isinstance(x, dict)][:10], "cards": n})
        lg.update(ending=False, activity=None)
        store.save(d, meta)
        return
    act = lg.get("activity")
    scene_note = f"\n员工想练的情景: {lg['scene']}" if lg.get("scene") else ""
    if act == "shadow" and not lg.get("sentences"):
        out = await _call([{"role": "system", "content": _system(meta, P.LANG_SHADOW, P.FORMAT_LANG_SHADOW)},
                           {"role": "user", "content": f"出一组跟读的句子。{scene_note}"}], _check_shadow, llm_kw)
        ss = [{"text": str(x["text"]).strip()[:400], "zh": str(x.get("zh") or "")[:400], "note": str(x.get("note") or "")[:300]}
              for x in _heal(out["sentences"])]
        lg["sentences"] = ss
        store.append(d, {"role": "tutor", "kind": "lang_scene", "activity": "shadow", "setting": str(out.get("setting") or "")[:300]})
        store.append(d, {"role": "tutor", "kind": "shadow_set", "sentences": ss})
        store.save(d, meta)
        return
    if act == "dialogue":
        opening = not any(ev.get("kind") == "lang_line" for ev in evs[_scene_start(evs):])
        msgs = [{"role": "system", "content": _system(meta, P.LANG_DIALOGUE, P.FORMAT_LANG_DIALOGUE)},
                {"role": "user", "content": (f"开场: 设定情景、你扮演谁, 说第一句。{scene_note}" if opening
                                             else "到目前为止:\n" + _dialogue_text(evs) + "\n\n他刚说完, 纠错 + 接着说。")}]
        out = await _call(msgs, lambda o: _check_dialogue(o, opening), llm_kw)
        if opening:
            store.append(d, {"role": "tutor", "kind": "lang_scene", "activity": "dialogue", "role_name": str(out.get("role") or "")[:60],
                             "setting": str(out["setting"])[:300]})
        else:
            fixes = [{"said": str(x.get("said") or "")[:300], "better": str(x.get("better") or "")[:300], "why": str(x.get("why") or "")[:300]}
                     for x in (_heal(out.get("fixes")) or []) if isinstance(x, dict)][:3]
            if fixes:
                store.append(d, {"role": "tutor", "kind": "lang_fix", "items": fixes})
        store.append(d, {"role": "tutor", "kind": "lang_line", "who": "role", "text": str(out["line"]).strip()[:1000],
                         "zh": str(out.get("zh") or "")[:1000]})
        store.save(d, meta)


def _scene_start(evs: list[dict]) -> int:
    idx = [i for i, ev in enumerate(evs) if ev.get("kind") == "lang_scene"]
    return idx[-1] if idx else 0


def start(d: Path, meta: dict, activity: str, scene: str) -> None:
    if activity not in ACTIVITIES:
        raise ValueError("练什么? 情景对话 (dialogue) 或跟读 (shadow)")
    lg = meta.setdefault("lang", {"code": "en"})
    lg.update(activity=activity, scene=scene.strip()[:300], sentences=None, ending=False)
    meta["phase"] = "lang"
    store.append(d, {"role": "learner", "kind": "say", "text": ("开始情景对话" if activity == "dialogue" else "开始跟读")
                     + (f": {scene.strip()[:100]}" if scene.strip() else "")})
    store.save(d, meta)


def say(d: Path, meta: dict, text: str, secs: Any) -> None:
    if (meta.get("lang") or {}).get("activity") != "dialogue":
        raise ValueError("先开始一段情景对话")
    text = (text or "").strip()[:2000]
    if not text:
        raise ValueError("说点什么 (可以点🎤)")
    ev: dict[str, Any] = {"role": "learner", "kind": "lang_line", "who": "learner", "text": text}
    if isinstance(secs, (int, float)) and secs > 0:
        ev["secs"] = round(float(secs), 1)
    store.append(d, ev)


def shadow(d: Path, meta: dict, idx: Any, heard: str, secs: Any) -> dict:
    lg = meta.get("lang") or {}
    ss = lg.get("sentences") or []
    try:
        i = int(idx)
    except (TypeError, ValueError):
        raise ValueError("第几句?") from None
    if not 0 <= i < len(ss):
        raise ValueError("没有这一句")
    heard = (heard or "").strip()[:2000]
    if not heard:
        raise ValueError("没听到 (再读一遍)")
    r = compare(ss[i]["text"], heard, lg.get("code") or "en")
    ev = {"role": "learner", "kind": "shadow_result", "idx": i, "target": ss[i]["text"], "heard": heard, **r}
    if isinstance(secs, (int, float)) and secs > 0:
        ev["secs"] = round(float(secs), 1)
    return store.append(d, ev)


def end(d: Path, meta: dict) -> None:
    lg = meta.get("lang") or {}
    evs = store.events(d)
    if not any(ev.get("kind") in ("lang_line", "shadow_result") and ev.get("role") == "learner" for ev in evs):
        raise ValueError("还没开口练, 先说几句或读几句再结束")
    lg["ending"] = True
    meta["lang"] = lg
    store.save(d, meta)
