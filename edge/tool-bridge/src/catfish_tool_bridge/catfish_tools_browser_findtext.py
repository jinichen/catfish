"""find_by_text 注进页面的那段 JS。

BL-TOOL-SPLIT 8/15: 从 catfish_tools_browser.py 抽出来 (1575 行超限), 沿用
5/20 那次 (browser 从 catfish_tools 抽出来) 的同一套做法。纯搬迁, 逻辑一行未改。
"""
from __future__ import annotations



_FIND_BY_TEXT_JS = r"""
(params) => {
    // BL-FIX44 (5/11) 重写: 返**全维度候选元数据 + 综合排序**, 让 LLM 自己判断挑哪个.
    // 之前版本只返 selector_hint, 撞 placeholder/label 同字符就翻车 (鸿波 EIS 登录场景).
    //
    // 新返字段:
    //   - selector: Playwright 最稳的 selector (优先 #id, 再 [name], 再 role/text 组合)
    //   - tag, role (ARIA), match_type (innerText/placeholder/aria-label/value/title/alt)
    //   - text (匹配到的文字), is_clickable (有 click handler 或 interactive role/tag)
    //   - bounds {x,y,w,h}, center {x,y} (供 catfish_browser_click coordinates 直点)
    //   - score (综合排序权重, 透明可解释)
    const wantedText = params.text;
    const exact = !!params.exact;
    const maxCount = params.maxCount || 10;
    const wantedRole = (params.role || '').toLowerCase();  // 'button' / 'link' / null

    function visible(el) {
        const rect = el.getBoundingClientRect();
        if (rect.width < 1 || rect.height < 1) return false;
        const cs = getComputedStyle(el);
        return cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0';
    }

    // 返 [text, match_type] — 哪个属性匹配的, 优先级 innerText > value > aria-label > placeholder > title > alt
    //
    // BL-FIX44-fix (5/12): 鸿波 EIS 实测 — 登录按钮真实文字是 '登 录' (中间空格),
    // text='登录' 子串匹配失败. 改 normalize whitespace 比较 — 两侧 strip + 内部
    // \s+ 折成空 (中文场景两字之间空格通常无意义), 再 substring 比.
    function _norm(s) {
        return (s || '').replace(/\s+/g, '').toLowerCase();
    }
    function matchedText(el, wanted, exact) {
        const wantedNorm = _norm(wanted);
        const tries = [
            ['innerText', (el.innerText || '').trim()],
            ['value', (el.value || el.getAttribute('value') || '').trim()],
            ['aria-label', (el.getAttribute('aria-label') || '').trim()],
            ['placeholder', (el.getAttribute('placeholder') || '').trim()],
            ['title', (el.getAttribute('title') || '').trim()],
            ['alt', (el.getAttribute('alt') || '').trim()],
        ];
        for (const [mt, t] of tries) {
            if (!t) continue;
            // 先试原始匹配 (保兼容); 没中再 norm-whitespace 匹配 (修 '登 录' 类按钮)
            const m1 = exact ? (t === wanted) : t.includes(wanted);
            if (m1) return [t, mt];
            const tNorm = _norm(t);
            const m2 = exact ? (tNorm === wantedNorm) : tNorm.includes(wantedNorm);
            if (m2) return [t, mt];
        }
        return [null, null];
    }

    // 显式 ARIA role 或隐式 (button/a/input[submit]/...).
    function getRole(el) {
        const explicit = el.getAttribute('role');
        if (explicit) return explicit.toLowerCase();
        const tag = el.tagName.toUpperCase();
        if (tag === 'BUTTON') return 'button';
        if (tag === 'A' && el.hasAttribute('href')) return 'link';
        if (tag === 'INPUT') {
            const t = (el.type || 'text').toLowerCase();
            if (t === 'submit' || t === 'button' || t === 'reset' || t === 'image') return 'button';
            if (t === 'checkbox') return 'checkbox';
            if (t === 'radio') return 'radio';
            return 'textbox';
        }
        if (tag === 'TEXTAREA') return 'textbox';
        if (tag === 'SELECT') return 'combobox';
        return '';
    }

    // 是否真可点击 — 有原生 interactive 行为或显式 click handler.
    function isClickable(el) {
        const tag = el.tagName.toUpperCase();
        if (['BUTTON', 'A', 'INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return true;
        if (el.hasAttribute('onclick')) return true;
        const r = (el.getAttribute('role') || '').toLowerCase();
        if (['button', 'link', 'menuitem', 'tab'].includes(r)) return true;
        // cursor:pointer 也是 click 信号
        try {
            if (getComputedStyle(el).cursor === 'pointer') return true;
        } catch (e) {}
        return false;
    }

    // 构造最稳的 Playwright selector. 注意: 不用 'text=' (歧义), 优先 #id / [name] / role-name.
    function bestSelector(el, matchType, matchedTextVal) {
        if (el.id) return '#' + CSS.escape(el.id);
        const nm = el.getAttribute('name');
        if (nm) return el.tagName.toLowerCase() + '[name=' + JSON.stringify(nm) + ']';
        // role + name (Playwright 1.27+ 支持 'role=button[name="登录"]')
        const role = getRole(el);
        if (role && matchType === 'innerText' && matchedTextVal && matchedTextVal.length <= 50) {
            return 'role=' + role + '[name=' + JSON.stringify(matchedTextVal) + ']';
        }
        // tag + class 兜底
        const cls = (el.className || '').toString().split(/\s+/).filter(Boolean).slice(0, 2).join('.');
        if (cls) return el.tagName.toLowerCase() + '.' + cls;
        return el.tagName.toLowerCase();
    }

    function inViewport(rect) {
        return rect.top < window.innerHeight && rect.bottom > 0 &&
               rect.left < window.innerWidth && rect.right > 0;
    }

    // 扫所有元素 (限制只看可见 + 文字匹配的)
    const all = document.querySelectorAll('*');
    const out = [];
    for (const el of all) {
        if (out.length >= 200) break;  // 硬上限, 防大页面爆
        if (!visible(el)) continue;
        const [matched, matchType] = matchedText(el, wantedText, exact);
        if (!matched) continue;

        const rect = el.getBoundingClientRect();
        const role = getRole(el);
        const clickable = isClickable(el);

        // 综合排序: 透明可解释
        let score = 0;
        // 1. 用户显式指定 role → 同 role 大加分, 不同 -10 排到末尾 (但仍返, 不丢)
        if (wantedRole) {
            if (role === wantedRole) score += 50;
            else score -= 20;
        }
        // 2. clickable > 不可点
        if (clickable) score += 30;
        // 3. match_type 优先级 (innerText 最强, alt 最弱)
        const mtBonus = {
            'innerText': 20, 'value': 15, 'aria-label': 12,
            'placeholder': 3, 'title': 2, 'alt': 1,
        };
        score += mtBonus[matchType] || 0;
        // 4. exact match + 10
        if (matched === wantedText) score += 10;
        // 5. 元素大小: 登录按钮通常 ≥ 100×40, log scale
        const area = Math.max(1, rect.width * rect.height);
        score += Math.min(15, Math.log2(area) | 0);
        // 6. 在 viewport 内 +5
        if (inViewport(rect)) score += 5;
        // 7. 文字越长越可能是误匹配 (placeholder 长描述 vs 按钮短文字)
        if (matched.length > 20) score -= 5;
        if (matched.length > 50) score -= 10;

        out.push({
            selector: bestSelector(el, matchType, matched),
            tag: el.tagName.toLowerCase(),
            role: role || null,
            text: matched.slice(0, 100),
            match_type: matchType,
            is_clickable: clickable,
            bounds: {
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                w: Math.round(rect.width),
                h: Math.round(rect.height),
            },
            center: {
                x: Math.round(rect.x + rect.width / 2),
                y: Math.round(rect.y + rect.height / 2),
            },
            in_viewport: inViewport(rect),
            score: score,
        });
    }

    // score 倒序
    out.sort((a, b) => b.score - a.score);
    return out.slice(0, maxCount);
}
"""
