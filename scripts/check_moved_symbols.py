#!/usr/bin/env python3
"""拆文件之后, 查新文件里有没有"用了但没导入"的跨文件符号。

    python3 scripts/check_moved_symbols.py <file.rs> [file.rs ...]

# 为什么有这个脚本

2026-08-15 拆 codex_backend.rs 时, codex_helper.rs 漏了一行
`use super::codex_probe::{hermes_agent_root, hermes_python};`。
漏的原因不是没查 —— 依赖扫描里明确列过 helper 用这两个函数, 是写文件头时
没把扫描结果带进去。编译器当然会报, 但那要等人跑一次 cargo, 一个来回。

# 它不是门禁

有误报 (闭包名、原始字符串里的词、宏), 所以**不要**拿它当 CI 门禁 —— 那样
只会教人忽略它。它的用途是: 搬完文件, 提交前自己跑一遍, 把明显的漏导入
在本地捞掉。

# 它的方向是对的 (反向验证)

抽掉 codex_helper.rs 那行真实的 import, 脚本报出了 hermes_agent_root 和
hermes_python 两个名字。也就是说对"漏导入"这一类, 它不漏报。
判据比被判的事宽, 判据就会替 bug 背书 —— 所以这条反向验证比脚本本身重要,
以后改这个脚本, 先确认它还能抓到这个例子。
"""
import re,sys
from pathlib import Path
ATTR={"derive","cfg","not","pub","target_os","test","serde","ignore","allow","warn","deny",
      "doc","crate","unix","windows","macos","feature","tauri","command","rename_all","default"}
STD={"format","println","vec","write","matches","assert","assert_eq","panic","min","max"}
# Rust 关键字 —— `for (a, b) in`、`if let ... (`、`match (x)` 都会被"标识符 + ("
# 的正则当成函数调用。2026-08-15 重写脚本时把这张表弄丢了, 于是每个文件都报
# for/let/return, 噪音盖过真问题 (hermes_pinned_tag 那条真漏报差点被淹掉)。
KW={"if","for","while","match","fn","let","return","self","move","loop","else","impl",
    "as","in","where","dyn","ref","mut","true","false","unsafe","break","continue","yield"}
def scan(f):
    L=Path(f).read_text(encoding="utf-8").splitlines()
    body=[]; inraw=False
    for l in L:
        if inraw:
            body.append("")
            if '"#' in l: inraw=False
            continue
        if re.search(r'r#"',l) and '"#' not in l.split('r#"',1)[1]:
            inraw=True; body.append(re.sub(r'r#".*$','',l)); continue
        body.append(re.sub(r'//.*$','',re.sub(r'"(\\.|[^"\\\n])*"','""',l)))
    txt="\n".join(body)
    local=set(re.findall(r'(?:^|\s)fn (\w+)',txt))|set(re.findall(r'(?:struct|enum|const|static) (\w+)',txt))
    # use 语句可能跨多行 (rustfmt 会把长的 {..} 拆开), 必须先拼起来再取名字。
    # 只看 "use " 开头的那一行会漏掉续行里的名字, 于是报一堆假的"没导入"。
    imported=set(); acc=None
    for l in L:
        s=l.strip()
        if acc is not None:
            acc+=" "+s
            if s.endswith(";"): imported|=set(re.findall(r'\b(\w+)\b',acc)); acc=None
            continue
        if s.startswith("use "):
            if s.endswith(";"): imported|=set(re.findall(r'\b(\w+)\b',s))
            else: acc=s
    calls={m.group(1) for m in re.finditer(r'(?<![.\w:])([a-z_][a-z0-9_]*)\s*\(',txt)}
    return sorted(c for c in calls if c not in local|imported|ATTR|STD|KW
                  and not re.search(rf'\b{c}!',txt) and not re.search(rf'\.\s*{c}\s*\(',txt))
bad=0
for f in sys.argv[1:]:
    m=scan(f)
    if m: bad+=1; print(f"  ❌ {f}: 可能没导入 → {m}")
print(f"\n可疑 {bad} 个" if bad else f"\n✓ {len(sys.argv)-1} 个文件, 未发现缺失的跨文件引用")
