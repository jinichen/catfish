"""BL-LEARN-RECMODE V2 #68 (5/15) — DOM mutation summary 算法.

events.jsonl 的 dom_changed event 现在只记 "DOM updated" 没语义. 这模块
注入 MutationObserver script 到 page, 拿真 mutation 列表, 摘要给 LLM:

  before: {"kind": "dom_changed", "summary": "DOM updated"}
  after:  {"kind": "dom_changed",
           "summary": "+12 .app-icon (新增应用网格), -3 .login-form"}

LLM 看到摘要能准判 "用户从登录页切到应用网格", 不只是"DOM 变了".

# CDP 集成

注入 script via Runtime.evaluate (page-level CDP):
```js
window.__catfishObserver = new MutationObserver(records => {
  // 累积每秒 mutation
  records.forEach(r => {...});
});
window.__catfishObserver.observe(document.body, {childList: true, subtree: true, attributes: false});
```

每秒 Runtime.evaluate `window.__catfishGetMutations()` 拿汇总.

# v0 ship 的

- Python helper 把 raw mutation list (dict[selector → +/-N]) → 人话 summary
- inject_mutation_observer JS 字符串
- 不真接 CDP runtime (那要在 cdp_listener._event_loop 里 setInterval, V3 加).
  v0 ship 算法 + JS, 跟现有 cdp_listener.dom_changed 替换路径留 V3.
"""
from __future__ import annotations

from collections import Counter

# 注入 page 的 MutationObserver script — Runtime.evaluate 跑一次, 之后 page
# 上有 window.__catfishGetMutations() 函数拿汇总.
INJECT_OBSERVER_JS = """
(function() {
  if (window.__catfishObserver) return 'already_installed';
  window.__catfishMutations = { added: {}, removed: {}, attrs: 0 };
  function selectorOf(el) {
    if (!el || el.nodeType !== 1) return null;
    var tag = el.tagName.toLowerCase();
    var cls = (el.className || '').split(/\\s+/).filter(Boolean).slice(0, 2).join('.');
    return cls ? tag + '.' + cls : tag;
  }
  window.__catfishObserver = new MutationObserver(function(records) {
    records.forEach(function(r) {
      if (r.type === 'childList') {
        r.addedNodes.forEach(function(n) {
          var s = selectorOf(n);
          if (s) window.__catfishMutations.added[s] = (window.__catfishMutations.added[s] || 0) + 1;
        });
        r.removedNodes.forEach(function(n) {
          var s = selectorOf(n);
          if (s) window.__catfishMutations.removed[s] = (window.__catfishMutations.removed[s] || 0) + 1;
        });
      } else if (r.type === 'attributes') {
        window.__catfishMutations.attrs++;
      }
    });
  });
  window.__catfishObserver.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['class', 'data-state', 'aria-expanded']  // 只关心状态 attr 不监 style
  });
  window.__catfishGetMutations = function() {
    var snap = JSON.parse(JSON.stringify(window.__catfishMutations));
    window.__catfishMutations = { added: {}, removed: {}, attrs: 0 };
    return snap;
  };
  return 'installed';
})();
"""


def summarize_mutation_snapshot(snap: dict) -> str:
    """把 raw mutation snapshot ({added: {sel→N}, removed: {sel→N}, attrs: N})
    转人话 summary 给 LLM.

    e.g. {added: {"div.app-icon": 12, "span.label": 12}, removed: {"div.login-form": 3}, attrs: 5}
    →   "+12 div.app-icon, +12 span.label, -3 div.login-form, 5 attr 变化"

    空 snapshot → "DOM 无变化"
    """
    added = snap.get("added") or {}
    removed = snap.get("removed") or {}
    attrs = snap.get("attrs") or 0

    if not added and not removed and not attrs:
        return "DOM 无变化"

    parts = []
    # 按数量降序 (大变化先), top 5
    for sel, n in sorted(added.items(), key=lambda x: -x[1])[:5]:
        parts.append(f"+{n} {sel}")
    for sel, n in sorted(removed.items(), key=lambda x: -x[1])[:5]:
        parts.append(f"-{n} {sel}")
    if attrs:
        parts.append(f"{attrs} attr 变化")
    return ", ".join(parts)


def is_significant_mutation(snap: dict, threshold: int = 5) -> bool:
    """够大才算 keyframe 触发条件 (RecMode keyframe 抽取算法用).

    总 added + removed 节点 ≥ threshold → True. 仅 attr 变化不触发.
    """
    added = sum((snap.get("added") or {}).values())
    removed = sum((snap.get("removed") or {}).values())
    return (added + removed) >= threshold


def merge_snapshots(snaps: list[dict]) -> dict:
    """合并多个 snapshot 成一个 (e.g. RecMode session 跨 N 秒累积总变化).

    用 Counter 加和.
    """
    added = Counter()
    removed = Counter()
    attrs = 0
    for s in snaps:
        added.update(s.get("added") or {})
        removed.update(s.get("removed") or {})
        attrs += s.get("attrs") or 0
    return {"added": dict(added), "removed": dict(removed), "attrs": attrs}


__all__ = [
    "INJECT_OBSERVER_JS",
    "summarize_mutation_snapshot",
    "is_significant_mutation",
    "merge_snapshots",
]
