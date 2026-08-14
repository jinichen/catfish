#!/usr/bin/env python3
"""拆文件之后, 查新文件里有没有"用了但没导入"的跨文件符号。

    python3 scripts/check_moved_symbols.py <file.rs> [file.rs ...]

# 为什么有这个脚本

2026-08-15 拆 codex_backend.rs 时, codex_helper.rs 漏了一行
`use super::codex_probe::{hermes_agent_root, hermes_python};`。
漏的原因不是没查 —— 依赖扫描里明确列过 helper 用这两个函数, 是写文件头时
没把扫描结果带进去。编译器当然会报, 但那要等人跑一次 cargo, 一个来回。

# 七条反向验证 (改这个脚本之前先跑一遍, 确认它还抓得到)
#
# ⚠ 每条的变异必须**精确**。2026-08-15 验证第 7 条时我把那行整个删掉了 ——
#    那变成了"名字未定义", 由第 5 条 (模块前缀/没导入) 接住, 于是第 7 条看着
#    像失效了。变异写错, 得到的"漏报"结论也是错的。
#
#   1 impl 方法        hermes_install_state.rs   pub(crate) fn unique_sibling → fn
#   2 结构体字段       hermes_install_steps.rs   pub(crate) preserve: bool → preserve: bool
#   3 签名私有类型     hermes_install_recover.rs pub(crate) struct BootstrapLock → struct
#   4 super 差一层     hermes_install.rs         mod tests 里 crate::commands:: → super::
#   5 模块前缀没导入   email_llm.rs              删掉 use crate::services::{hermes_api_config, …}
#   6 死 import        任意新文件                加一条 use std::collections::HashMap;
#   7 只在测试里用     autostart_mcp.rs          把 mod tests 里那条 use **挪到文件顶层**
#                                               (不是删掉 —— 删掉是第 5 条的场景)

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
import re,sys,os,glob
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
                # 改进 1 (8/15): 字段访问后面不跟 '('。`.count()` 是迭代器方法,
                # 按 `\.count\b` 匹配会把它当成读 UrgentEventPayload.count。
                pat=rf'\.\s*{fl}\b(?!\s*\()'
                # 改进 2 (8/15): 名字不唯一时, 若别的文件里出现 `结构体名 {` 的
                # 字面量构造, 那它必然要碰到全部字段 —— 这条比名字唯一更硬。
                # EmailItem 的 id/subject/sender 就是这么漏掉的: 名字太常见,
                # 唯一性规则直接跳过, 而 email_scheduler.rs 里在 `EmailItem {` 构造它。
                ctor=[g for g in files if g!=f and re.search(rf'\b{cur}\s*\{{',clean(src[g]))]
                if len(owner.get(fl,()))!=1 and not ctor: continue
                u=[g for g in files if g!=f and linked(g,f)
                   and (re.search(pat,clean(src[g])) or g in ctor)]
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

def scan_super_depth(files):
    """嵌套 mod 里的 `use super::<兄弟模块>` —— super 差了一层。

    2026-08-15 第五次: 拆 hermes_install.rs 时把测试专属的 import 放进了
    mod tests 内部 (放文件顶上非测试编译会报 unused), 但照抄了顶层的写法:

        文件顶层:      super = commands          → super::hermes_install_state  ✓
        mod tests 里:  super = hermes_install    → super::hermes_install_state  ✗

    cargo check 完全看不出来 —— mod tests 有 #[cfg(test)], 只有 cargo test
    才编译它。所以这一类必须自己查, 不能指望"check 绿了就行"。

    判据: 用花括号配对定出每个 mod 块的范围 (不靠缩进猜), 块内的
    `use super::X` 若 X 是同目录的另一个 .rs, 就是差了一层。
    """
    out=[]
    for f in files:
        d=os.path.dirname(f) or "."
        sibs={Path(x).stem for x in glob.glob(f"{d}/*.rs")}-{Path(f).stem,"mod"}
        if not sibs: continue
        L=Path(f).read_text(encoding="utf-8").splitlines()
        spans=[]
        for i,l in enumerate(L):
            if re.match(r'\s*(pub(\(crate\))? )?mod \w+\s*\{',l):
                depth=0
                for j in range(i,len(L)):
                    depth+=L[j].count("{")-L[j].count("}")
                    if depth==0: spans.append((i+1,j+1)); break
        for a,b in spans:
            for k in range(a,b):
                m=re.match(r'\s*use super::(\w+)',L[k])
                if m and m.group(1) in sibs:
                    out.append(f"{f}:{k+1}: mod 块里的 super::{m.group(1)} 差一层 "
                               f"(该写 crate::…::{m.group(1)})")
    return out

def scan_module_prefixes(files):
    """`foo::bar()` 里的模块前缀 foo 没导入。

    2026-08-15: email_llm.rs 漏了
    `use crate::services::{hermes_api_config, picker_config, upstream_error_guard};`
    —— 4 个编译错。

    上面 scan 的调用点正则是 `(?<![.\w:])(\w+)\s*\(`, 它匹配的是紧挨着
    左括号的那个名字。碰到 `hermes_api_config::hermes_api_config()`, 被匹配的是
    `::` 后面的函数名, 而那个 lookbehind 又把它排掉了 —— 于是**前缀模块整个
    是隐形的**。检查器看得见"函数没导入", 看不见"模块没导入"。

    判据: 同目录下存在同名 .rs, 且它以 `foo::` 的形式出现在正文里, 却不在
    任何 use 语句中。
    """
    out=[]
    for f in files:
        d=os.path.dirname(f) or "."
        sibs={Path(x).stem for x in glob.glob(f"{d}/*.rs")}-{"mod"}
        L=Path(f).read_text(encoding="utf-8").splitlines()
        body="\n".join(re.sub(r'//.*$','',re.sub(r'"(\\.|[^"\\\n])*"','""',l))
                       for l in L if not l.strip().startswith("use "))
        imported=set(); acc=None
        for l in L:
            s=l.strip()
            if acc is not None:
                acc+=" "+s
                if s.endswith(";"): imported|=set(re.findall(r'\w+',acc)); acc=None
                continue
            if s.startswith("use "):
                if s.endswith(";"): imported|=set(re.findall(r'\w+',s))
                else: acc=s
        used={m.group(1) for m in re.finditer(r'(?<![:\w])([a-z_][a-z0-9_]*)::',body)}
        for m in sorted((used & sibs) - imported - {Path(f).stem}):
            out.append(f"{f}: 用了 {m}:: 但没 use 它")
    return out

def scan_dead_imports(files):
    """顶层 use 里引进来却没人用的名字。

    每次拆文件都能抓到 (codex 5 个 / hermes 11 个 / email 5 个) —— 块搬走了,
    它当初需要的 import 就留在了原地。

    判据只有一条: **前面不是 `::` 的裸名**。
      · 函数   foo()            → 命中
      · 模块   foo::bar()       → 命中
      · std::fs::File 里的 fs   → 前面是 `::`, 正确排掉
    2026-08-15 这里错过两版: 第一版搜 `fs::` 连 `std::fs::` 一起匹配 (判据太宽);
    第二版把小写名一律当模块搜 `foo::`, 于是函数 import 全被误报成死的 (判据
    太窄)。一条统一的判据反而两头都对。

    trait 是例外: 它们靠方法调用生效, 名字不出现在正文里, 只能按方法名判。
    """
    TRAIT={"Context":r'\.(with_)?context\(',"FileExt":r'(try_)?lock_exclusive\(|\bunlock\(',
           "Emitter":r'\.emit\(',"Write":r'\.write_all\(|\.flush\(|write!\(',
           "Manager":r'\.state\(|\.state::<',"PermissionsExt":r'\.mode\(|\.set_mode\(',
           "Engine":r'\.(encode|decode)\(',"RngCore":r'fill_bytes\(',"FileExt2":r'x^',
           # derive(serde::Serialize) 是全名写法, **不需要** use serde::Serialize。
           # (?<!::) 把它跟裸的 derive(Serialize) 区分开。
           "Serialize":r'derive\([^)]*(?<!::)\bSerialize|:\s*(?<!::)Serialize\b',
           "Deserialize":r'derive\([^)]*(?<!::)\bDeserialize|:\s*(?<!::)Deserialize\b'}
    out=[]
    for f in files:
        L=Path(f).read_text(encoding="utf-8").splitlines()
        body="\n".join(re.sub(r'//.*$','',re.sub(r'"(\\.|[^"\\\n])*"','""',l))
                       for l in L if not l.strip().startswith("use ")
                       and not re.match(r'^\s+\w+[,;]?\s*$',l))
        tops=[];acc=None
        for l in L:
            if acc is not None:
                acc+=" "+l.strip()
                if l.strip().endswith(";"): tops.append(acc); acc=None
                continue
            if l.startswith("use "):
                if l.rstrip().endswith(";"): tops.append(l.strip())
                else: acc=l.strip()
        dead=[]
        for u in tops:
            g=re.findall(r'\{(.*)\}',u)
            names=re.findall(r'\b(\w+)\b',g[0]) if g else [u.rstrip(";").split("::")[-1].strip()]
            # `use x::Trait as _;` —— 名字被刻意匿名化了, 就是为了只引 trait 方法。
            # 按名字根本查不到, 一律跳过。2026-08-15 oauth 的 `Engine as _` 被
            # 报成死 import 就是这个。
            if re.search(r'\bas\s+_\s*;', u): continue
            for nm in names:
                if nm=="self": continue
                if nm in TRAIT:
                    if not re.search(TRAIT[nm],body): dead.append(nm)
                elif not re.search(rf'(?<!::)\b{nm}\b',body): dead.append(nm)
        if dead: out.append(f"{f}: 死 import {sorted(set(dead))}")
    return out

def scan_test_only_imports(files):
    """顶层 use 引进来的名字, 只在 #[cfg(test)] 块里被用到。

    非测试编译时那些块被 cfg 掉, 这条 import 就成了 unused 警告。
    而 `cargo test` 是绿的 —— 又一次"绿了不等于没问题"。

    2026-08-15 拆 autostart.rs 时中招: autostart_mcp.rs 顶上写了
    `use super::autostart_deps::find_agent_browser;`, 而 find_agent_browser
    唯一的调用点在 mcp_autofix_tests 里。
    拆 hermes_install.rs 时明明已经处理过同一件事 (把测试专属 import 放进
    mod tests), 这次没应用, 因为规划脚本把测试块整个算进了目标模块, 没区分
    "只有测试在用"。

    修法: 把这条 use 挪进 mod tests 内部。注意 mod 里的 super 差一层,
    要写 crate::…, 见 scan_super_depth。
    """
    out=[]
    for f in files:
        L=Path(f).read_text(encoding="utf-8").splitlines()
        # cfg(test) 块的行号范围 (花括号配对)
        tl=set()
        for i,l in enumerate(L):
            if re.match(r'\s*#\[cfg\((all\()?test',l):
                depth=0; started=False
                for j in range(i,len(L)):
                    depth+=L[j].count("{")-L[j].count("}")
                    if "{" in L[j]: started=True
                    if started and depth==0: tl|=set(range(i,j+1)); break
        if not tl: continue
        tops=[];acc=None
        for i,l in enumerate(L):
            if acc is not None:
                acc+=" "+l.strip()
                if l.strip().endswith(";"): tops.append(acc); acc=None
                continue
            if l.startswith("use "):
                if l.rstrip().endswith(";"): tops.append(l.strip())
                else: acc=l.strip()
        clean=lambda s: re.sub(r'//.*$','',re.sub(r'"(\\.|[^"\\\n])*"','""',s))
        prod="\n".join(clean(l) for i,l in enumerate(L)
                       if i not in tl and not l.startswith("use "))
        test="\n".join(clean(L[i]) for i in sorted(tl))
        for u in tops:
            g=re.findall(r'\{(.*)\}',u)
            names=re.findall(r'\b(\w+)\b',g[0]) if g else [u.rstrip(";").split("::")[-1].strip()]
            for nm in names:
                if nm in ("self","crate","super","use","std","serde","tauri"): continue
                pat=rf'(?<!::)\b{nm}\b'
                if not re.search(pat,prod) and re.search(pat,test):
                    out.append(f"{f}: {nm} 只在 #[cfg(test)] 里用, 但 use 写在文件顶层")
    return out

bad=0
for f in sys.argv[1:]:
    m=scan(f)
    if m: bad+=1; print(f"  ❌ {f}: 可能没导入 → {m}")
for w in scan_members(sys.argv[1:])+scan_signatures(sys.argv[1:])+scan_super_depth(sys.argv[1:])+scan_module_prefixes(sys.argv[1:])+scan_dead_imports(sys.argv[1:])+scan_test_only_imports(sys.argv[1:]):
    bad+=1; print("  ❌ "+w.strip())
print(f"\n可疑 {bad} 个" if bad else f"\n✓ {len(sys.argv)-1} 个文件, 未发现缺失的跨文件引用")
