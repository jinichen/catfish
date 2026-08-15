"""注进页面的 DOM 快照 JS, 以及 a11y 树的展平。

BL-TOOL-SPLIT 8/15: 从 catfish_tools_browser.py 抽出来 (1575 行超限), 沿用
5/20 那次 (browser 从 catfish_tools 抽出来) 的同一套做法。纯搬迁, 逻辑一行未改。
"""
from __future__ import annotations

from typing import Any, Dict, List

# DOM-based fallback. ``page.accessibility.snapshot()`` 在新版 Playwright (>=1.50)
# 已废弃 / 返 None, 这条路走 ``page.evaluate(JS)`` 直接扫 DOM 拿可见可交互元素.
# 输出 schema 跟 _flatten_a11y 兼容: {role, name, depth} + 多个 selector_hint
# 给模型抓 selector 用.
_DOM_SNAPSHOT_JS = r"""
(maxCount) => {
    const out = [];
    function visible(el) {
        const rect = el.getBoundingClientRect();
        if (rect.width < 1 || rect.height < 1) return false;
        const cs = getComputedStyle(el);
        return cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0';
    }
    function role(el) {
        const r = el.getAttribute('role');
        if (r) return r;
        const tag = el.tagName.toUpperCase();
        if (tag === 'A') return 'link';
        if (tag === 'BUTTON') return 'button';
        if (tag === 'INPUT') {
            const t = (el.getAttribute('type') || 'text').toLowerCase();
            if (t === 'checkbox') return 'checkbox';
            if (t === 'radio') return 'radio';
            if (t === 'submit' || t === 'button') return 'button';
            if (t === 'password') return 'textbox';
            return 'textbox';
        }
        if (tag === 'TEXTAREA') return 'textbox';
        if (tag === 'SELECT') return 'combobox';
        if (tag === 'IMG') return 'img';
        if (tag === 'FORM') return 'form';
        if (tag === 'LABEL') return 'label';
        if (/^H[1-6]$/.test(tag)) return 'heading';
        return tag.toLowerCase();
    }
    function name(el) {
        const candidates = [
            el.getAttribute('aria-label'),
            el.getAttribute('placeholder'),
            el.getAttribute('name'),
            el.getAttribute('title'),
            el.getAttribute('alt'),
            (el.innerText || '').trim(),
            el.getAttribute('value'),
        ];
        for (const c of candidates) {
            if (c) return String(c).trim().slice(0, 100);
        }
        return '';
    }
    function selectorHint(el) {
        if (el.id) return '#' + el.id;
        const nm = el.getAttribute('name');
        if (nm) return el.tagName.toLowerCase() + '[name="' + nm + '"]';
        const cls = (el.className || '').toString().split(/\s+/).filter(Boolean).slice(0, 2).join('.');
        if (cls) return el.tagName.toLowerCase() + '.' + cls;
        return el.tagName.toLowerCase();
    }
    function depthOf(el) {
        let d = 0;
        let cur = el;
        while (cur.parentElement) { d += 1; cur = cur.parentElement; }
        return d;
    }
    const all = document.querySelectorAll(
        'button, a, input, textarea, select, [role], h1, h2, h3, h4, h5, h6, label, form, img'
    );
    for (const el of all) {
        if (out.length >= maxCount) break;
        if (!visible(el)) continue;
        const r = role(el);
        const n = name(el);
        // 没 name 的 generic / div / span 跳过, 跟 a11y 行为一致
        const interesting = ['button', 'link', 'textbox', 'checkbox', 'radio',
                             'combobox', 'menuitem', 'tab', 'heading', 'img', 'form'];
        if (!n && interesting.indexOf(r) === -1) continue;
        out.push({
            role: r,
            name: n,
            depth: depthOf(el),
            selector_hint: selectorHint(el),
        });
    }
    return out;
}
"""


def _evaluate_dom_snapshot(page: Any, max_count: int = 200) -> List[Dict[str, Any]]:
    """跑 JS 拿可见可交互元素列表. 跟 _flatten_a11y 输出格式兼容 + 多 selector_hint."""
    raw = page.evaluate(_DOM_SNAPSHOT_JS, max_count) or []
    # 防 JS 端塞了脏 / 非 dict
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append({
            "role": str(item.get("role", "")),
            "name": str(item.get("name", ""))[:100],
            "depth": int(item.get("depth", 0)),
            "selector_hint": str(item.get("selector_hint", ""))[:200],
        })
    return out


def _flatten_a11y(
    node: Optional[Dict[str, Any]],
    out: List[Dict[str, Any]],
    max_count: int = 200,
    depth: int = 0,
) -> None:
    """把 accessibility tree 递归平铺成 element 列表. 超 max_count 立刻停."""
    if not node or len(out) >= max_count:
        return
    role = node.get("role", "")
    name = node.get("name", "")
    # 只收 "有意义" 的元素 (有 name 或可交互 role)
    interesting_roles = {
        "button", "link", "textbox", "checkbox", "radio", "combobox",
        "menuitem", "tab", "heading", "img", "img-text", "form",
    }
    if name or role in interesting_roles:
        out.append({
            "role": role,
            "name": name[:100] if name else "",
            "depth": depth,
        })

    for child in node.get("children", []) or []:
        if len(out) >= max_count:
            break
        _flatten_a11y(child, out, max_count=max_count, depth=depth + 1)
