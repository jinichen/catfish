"""搭工作流时多查的几样 (11/08 宣传片专员那次暴露的): 校验 (skill_workflow.validate) 只管格式, 这里管「能不能真做成」。

- 小鲶那步 (xiaonian) 不给任何工具, 只看交进来的数据写字。说明里却写着「调 execute_code 生成 Word」「给出公开素材链接」
  —— 校验照样过, 跑起来要么空着, 要么编一个链接出来。起草 / 保存 / 卡上都当问题拦下来, 并说清楚换成什么。
- 起草时 (只在一句话搭专家 / 小鲶起草时) 再提醒两样: 步数太多、同一块积木连着抄好几遍 (该用「每一项做一次」)。
  这两样不拦保存 —— 员工自己要排长一点也行。
- plain(): 问题的说法里去掉步骤 id 和给小鲶看的工具提示, 卡上给员工看。

跑已经装好的工作流不查这些 (只查格式), 老工作流不会因为说明里一句话突然跑不了。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from . import skill_workflow as engine

#: 一句话搭专家 / 小鲶起草时希望的上限 (编辑器里手排不受这个限)
BUILD_MAX_STEPS = 8

_REF = re.compile(r"\$\{[^}]*\}")
_CLAIMS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"execute_code|python|调用?.{0,6}(工具|接口|api|脚本|积木)|(运行|执行).{0,4}(代码|脚本)", re.I),
     "它不能调工具、跑代码 —— 要动手的事换成对应的积木"),
    (re.compile(r"(生成|写入|写成|保存|存成|存为|导出|输出成|转成|做成).{0,8}(docx|word|pdf|excel|xlsx|pptx?|文件)|保存到|写入(磁盘|目录)",
                re.I),
     "它不能生成文件 —— 要 Word 用「导出 Word 文件」积木, 要存知识库用「建知识库页面」"),
    (re.compile(r"联网|上网|网上(搜|查|找)|搜索引擎|(公开|网络|在线|外部|网上).{0,6}(链接|网址|素材|资源|资料)|网址|url|https?://",
                re.I),
     "它上不了网, 写出来的网址、版权信息只会是编的 —— 改成只用前面查到的本机文件 / 知识库"),
    (re.compile(r"(发送|发出|发给|推送|群发).{0,6}(邮件|微信|消息|通知|同事)", re.I),
     "它发不了东西 —— 要发用「推送到我的微信」「存邮件草稿」这类积木"),
]

#: 「不要上网」「别给网址」是叮嘱它别做; 「已写入知识库」是在转述前面积木做完的事 —— 都不算
_NEG = re.compile(r"不|别|勿|禁止|无需|没有|已")


def claims(wf: dict[str, Any]) -> list[str]:
    """小鲶那步说明里要它做它做不到的事。"""
    out = []
    for i, st in enumerate(wf.get("steps") or [], 1):
        if not isinstance(st, dict) or engine.block_of(st) != "xiaonian":
            continue
        text = _REF.sub("", str(st.get("xiaonian") or ""))
        for pat, why in _CLAIMS:
            if any(not _NEG.search(text[max(0, m.start() - 6):m.start()]) for m in pat.finditer(text)):
                out.append(f"第 {i} 步 ({st.get('id')}) 是小鲶自己想、自己写的一步, {why}")
                break
    return out


def check(wf: dict[str, Any], *, skill_lookup: Callable[[str], Path | None]) -> list[str]:
    """起草 / 保存 / 卡上用: 格式 + 小鲶那步做不到的事。"""
    return engine.validate(wf, skill_lookup=skill_lookup) + claims(wf)


def build_hints(wf: dict[str, Any]) -> list[str]:
    """起草时交回去让小鲶改的 (不拦保存)。"""
    steps = [s for s in wf.get("steps") or [] if isinstance(s, dict)]
    out = []
    if len(steps) > BUILD_MAX_STEPS:
        out.append(f"步骤太多 ({len(steps)} 步), 员工看不过来: 收成 {BUILD_MAX_STEPS} 步以内, "
                   "对一串东西做同一件事用 for_each 写成一步")
    run, prev = 1, None
    for i, st in enumerate(steps, 1):
        a = st.get("action")
        run = run + 1 if a and a == prev and "for_each" not in st else 1
        prev = a
        if run == 3:
            out.append(f"第 {i - 2}-{i} 步连着用了好几次 {a}: 先让一步交出一个列表, 再用 for_each 一步做完")
    params = wf.get("params") or {}
    bare = [k for k, v in params.items() if not (isinstance(v, dict) and str(v.get("label") or "").strip())]
    if bare:
        out.append(f"参数 {', '.join(bare)} 要写 label (员工看得懂的中文名, 例「主题」)")
    return out


_ID_PAREN = re.compile(r"(第 \d+(?:-\d+)? 步) \([a-z][a-z0-9_]*\)")
_TOOL_HINT = re.compile(r"\s*\((?:用|见) ?catfish_[^)]*\)")


def plain(p: str) -> str:
    return _TOOL_HINT.sub("", _ID_PAREN.sub(r"\1", p)).strip()
