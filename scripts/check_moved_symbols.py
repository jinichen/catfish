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
def scan_members(files):
    """impl 块里的方法、结构体的字段 —— 跨文件被用到却还是私有的。

    2026-08-15 拆 hermes_install.rs 时一次报出 16 个编译错, 全是这两类。
    漏的原因是上面那套扫描只看**缩进 0 的顶层条目**: impl 里的方法是缩进的,
    从来没进过扫描范围; 而结构体字段更隐蔽 —— `backup.preserve` 这行里
    根本不出现 `PreviousInstall` 这个名字, 类型是顺着函数返回值流过去的。

    也就是说, 判据是"名字有没有出现", 真问题是"这个条目跨模块可不可达"。
    两者不等价, 而不等价的地方正好就是 bug 藏身的地方。
    """
    src={f:Path(f).read_text(encoding="utf-8") for f in files}
    # 字段名 (.path / .model 这种) 满仓都是。只按字段名匹配, 会把毫不相干的文件
    # 算成使用方 —— 这个检查就变成了噪音源。
    # 收紧: 只有真的 import 了对方模块的文件, 才可能拿到那个类型的值。
    mod=lambda f: Path(f).stem
    def linked(g,f):
        return re.search(rf'\b{mod(f)}::', src[g]) is not None
    # 再收一道: 只报**字段名在全集里唯一**的那些。
    # `.path` / `.model` 这种名字十几个结构体都有, 光看名字判不出类型, 报出来
    # 全是噪音 (回扫 16 个已编译通过的文件, 一口气报了 33 条假的)。
    # 名字唯一时才有把握。代价是同一个结构体上有多个字段出问题时只报得出
    # 名字独特的那个 —— 但那已经足够把人引到正确的结构体上了。
    owner={}
    for g in files:
        cur=None
        for l in src[g].splitlines():
            m=re.match(r'(?:pub(?:\(crate\))? )?struct (\w+)',l)
            if m: cur=m.group(1); continue
            if cur and l.startswith("}"): cur=None; continue
            m=re.match(r'    (?:pub(?:\(crate\))? )?(\w+):',l)
            if m and cur: owner.setdefault(m.group(1),set()).add(cur)
    def clean(t):
        return "\n".join(re.sub(r'//.*$','',re.sub(r'"(\\.|[^"\\\n])*"','""',l)) for l in t.splitlines())
    out=[]
    for f in files:
        cur=None
        for l in src[f].splitlines():
            m=re.match(r'impl (\w+)',l)
            if m: cur=m.group(1); continue
            m=re.match(r'    (?:pub(?:\(crate\))? )?(?:const )?fn (\w+)',l)
            if m and cur and not l.strip().startswith("pub"):
                me=m.group(1)
                u=[g for g in files if g!=f and linked(g,f) and (re.search(rf'\.\s*{me}\s*\(',clean(src[g]))
                                                 or re.search(rf'{cur}::{me}\b',clean(src[g])))]
                if u: out.append(f"  {f}: {cur}::{me}() 私有, 但 {', '.join(u)} 在调它")
        cur=None
        for l in src[f].splitlines():
            m=re.match(r'(?:pub(?:\(crate\))? )?struct (\w+)',l)
            if m: cur=m.group(1); continue
            if cur and l.startswith("}"): cur=None; continue
            m=re.match(r'    (?:pub(?:\(crate\))? )?(\w+):',l)
            if m and cur and not l.strip().startswith("pub"):
                fl=m.group(1)
                if len(owner.get(fl,()))!=1: continue
                u=[g for g in files if g!=f and linked(g,f) and re.search(rf'\.\s*{fl}\b',clean(src[g]))]
                if u: out.append(f"  {f}: {cur}.{fl} 私有, 但 {', '.join(u)} 在读它")
    return out

def scan_signatures(files):
    """pub / pub(crate) 函数的签名里出现了本文件的私有类型。

    2026-08-15 第三次栽在这上面: acquire_bootstrap_lock 放开成 pub(crate) 了,
    它的返回类型 BootstrapLock 没有 —— 于是别的模块拿不到那个值。
    编译器的 private_interfaces lint 说的就是这件事, 但它先报 warning 再报
    error, 混在一堆输出里容易滑过去。

    前两类 (impl 方法、结构体字段) 问的是"这个条目本身可不可达";
    这一类问的是"可达的条目, 它签名里提到的东西可不可达"。
    放开一个函数而不看它的签名, 等于开了门没给钥匙。
    """
    out=[]
    for f in files:
        L=Path(f).read_text(encoding="utf-8").splitlines()
        priv={m.group(2) for l in L if (m:=re.match(r'^(struct|enum|type) (\w+)',l))}
        if not priv: continue
        acc=None
        for l in L:
            if acc is None:
                if re.match(r'^pub(\(crate\))? (async )?fn ',l): acc=l
                else: continue
            else: acc+=" "+l.strip()
            if "{" in acc or acc.rstrip().endswith(";"):
                sig=acc.split("{")[0]
                for ty in priv:
                    if re.search(rf'\b{ty}\b',sig):
                        out.append(f"{f}: {sig.strip()[:66]}… 签名里的 {ty} 还是私有的")
                acc=None
    return out

bad=0
for f in sys.argv[1:]:
    m=scan(f)
    if m: bad+=1; print(f"  ❌ {f}: 可能没导入 → {m}")
for w in scan_members(sys.argv[1:])+scan_signatures(sys.argv[1:]):
    bad+=1; print("  ❌ "+w.strip())
print(f"\n可疑 {bad} 个" if bad else f"\n✓ {len(sys.argv)-1} 个文件, 未发现缺失的跨文件引用")
