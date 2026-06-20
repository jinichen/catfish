#!/usr/bin/env python3
"""P3.5.42.2 (6/18 鸿波 catch '旧的 MEMORY 怎么处理') — 跑 LLM 二次校验扫旧 MEMORY.md / USER.md.

# 背景

P3.5.42 ship 了 memory_enforce pre_tool_call hook, 拦未来 LLM 误写, 但**已经写在
hermes memory 里的旧数据动不了**. 现有 scripts/hermes-memory-cleanup.py 只手动
list / delete, 没 LLM 二次校验.

鸿波截图 MEMORY.md 7.1 KB 内含 4 条:
  1. 资质工作总结大键数据... → 该 memory (project_fact)
  2. 6/18 巡视巡察整改回头看... → 灰区 (倾向 project_fact)
  3. 陈淡孜 6/19 飞抵福州... → 该 journal (单次事件 + 日期), **误写**
  4. cron 任务 deliver=origin... → 该 memory (系统行为常量)

本脚本扫所有 entry, 调 memory_enforce._classify_memory_route LLM 判定:
  - route == 当前 target → ✅ 留
  - route in (memory, user) 但 != target → ⚠️ 建议改 target
  - route in (journal, todo, skill) → ❌ 建议删 (改写到对的 fs 路径)

输出 markdown 报告. **不自动删**, 列出 hermes-memory-cleanup.py delete 命令清单
让鸿波拍.

# 设计原则 (跟 P3.5.42 一致)

1. **不硬编码词表** — 走 memory_enforce 现成 LLM 二次校验
2. **所有遵循 picker** — model 走 memory_enforce.get_verifier_model() chain
3. **fail-silent** — LLM 调挂的 entry → 跳过 (不阻其他 entry)
4. **不自动删** — 给鸿波 cleanup 命令清单, 鸿波拍

# 用法

  python3 scripts/audit-old-memory.py                # markdown to stdout
  python3 scripts/audit-old-memory.py --target USER  # 只扫 USER.md
  python3 scripts/audit-old-memory.py --target MEMORY  # 只扫 MEMORY.md (默认扫两个)
  python3 scripts/audit-old-memory.py --save         # 存 ~/.catfish/outputs/<date>/old-memory-audit.md
  python3 scripts/audit-old-memory.py --json         # 机器可读

# 不引依赖 (纯 stdlib + 加载 memory_enforce 模块)

memory_enforce.py 在 ~/.hermes/plugins/catfish-xcatfish-user/, 用 importlib 动态加载.
fallback: catfish 仓库内 edge/hermes-plugins/catfish-xcatfish-user/.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ── 配置 ──────────────────────────────────────────────────────────

# hermes ENTRY_DELIMITER (跟 hermes-memory-cleanup.py 对齐)
ENTRY_DELIMITER = "\n§\n"

# memory_enforce.py 可能路径 (按优先级 try):
#   1. 装机 hermes plugin (生产)
#   2. catfish 仓库内 edge/hermes-plugins/ (跟 __file__ 算相对, 不依赖 home)
#   3. ~/person_task/catfish/... (鸿波本机 backup)
_REPO_ROOT = Path(__file__).resolve().parent.parent  # scripts/ → repo root
_ENFORCE_PATHS = [
    Path.home() / ".hermes" / "plugins" / "catfish-xcatfish-user" / "memory_enforce.py",
    _REPO_ROOT / "edge" / "hermes-plugins" / "catfish-xcatfish-user" / "memory_enforce.py",
    Path.home() / "person_task" / "catfish" / "edge" / "hermes-plugins"
        / "catfish-xcatfish-user" / "memory_enforce.py",
]


def _load_memory_enforce() -> Optional[Any]:
    """动态加载 memory_enforce.py module. 返 module 或 None (找不到)."""
    for p in _ENFORCE_PATHS:
        if p.is_file():
            try:
                spec = importlib.util.spec_from_file_location("memory_enforce", p)
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module
            except Exception as e:  # noqa: BLE001
                print(f"⚠ 加载 {p} 失败: {e}", file=sys.stderr)
                continue
    return None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="跑 LLM 二次校验扫旧 hermes memory, 列误写 + 给 cleanup 命令清单",
    )
    p.add_argument(
        "--target", choices=["USER", "MEMORY", "BOTH"], default="BOTH",
        help="扫 USER.md / MEMORY.md / 两个 (默认 BOTH)",
    )
    p.add_argument(
        "--save", action="store_true",
        help="存 ~/.catfish/outputs/<date>/old-memory-audit.md",
    )
    p.add_argument(
        "--json", dest="as_json", action="store_true",
        help="JSON 输出 (机器可读)",
    )
    p.add_argument(
        "--model", default="",
        help=(
            "临时覆盖 picker chain 选的 verifier model (e.g. catfish-public-deepseek-flash). "
            "鸿波 6/20 catch: 离单位时 picker 选的 catfish-private-main 上游 timeout, "
            "走公网 model 跑 audit 不破红线 (data 已在本机 MEMORY.md, 只走公网做 router 判断, "
            "不新增数据出端). 不传则严格走 memory_enforce picker chain."
        ),
    )
    return p.parse_args()


# ── 读 hermes memory ──────────────────────────────────────────────


def _memories_dir() -> Path:
    return Path.home() / ".hermes" / "memories"


def _read_entries(target: str) -> list[str]:
    """读 USER.md / MEMORY.md, 按 ENTRY_DELIMITER 切返 entry list.

    target: "USER" | "MEMORY"
    """
    path = _memories_dir() / f"{target}.md"
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    # 切 entry, 去空
    raw = text.split(ENTRY_DELIMITER)
    return [e.strip() for e in raw if e.strip()]


# ── 跑 LLM classify ──────────────────────────────────────────────


def _classify_all(target: str, entries: list[str], enforce: Any,
                  override_model: str = "") -> list[dict]:
    """每条 entry 调 memory_enforce._classify_memory_route.

    target: "USER" → hermes memory target = "user"; "MEMORY" → "memory"
    override_model: 鸿波 6/20 加 — CLI --model 临时覆盖 picker chain
                    (离单位时内网 model 不通时用).
    """
    actual_target = "user" if target == "USER" else "memory"
    model = override_model.strip() or enforce.get_verifier_model()
    src = "--model 覆盖" if override_model.strip() else "picker chain"
    print(f"# audit {target}.md ({len(entries)} entries, model={model} [{src}])",
          file=sys.stderr)

    # 连续 N 条 classify 全挂 → 早 abort. 鸿波 6/20 catch '没用的都删掉, 留着没意义':
    # 25/25 全 skip 凑数没价值, gateway/model 不通就直接报修, 别傻跑完.
    EARLY_ABORT_THRESHOLD = 3
    consecutive_skip = 0

    # P3.5.42.5: 用 diag 版看真错. 没 diag 版的旧 memory_enforce 兜底走 fail-silent.
    use_diag = hasattr(enforce, "_classify_memory_route_diag")
    if not use_diag:
        print("  ⚠ memory_enforce 版本太老没 _classify_memory_route_diag, "
              "看不到真错. 升级 catfish-xcatfish-user plugin 后重跑.",
              file=sys.stderr)

    results = []
    first_error_msg = ""  # 记第一条错给早 abort 时报
    for i, content in enumerate(entries, 1):
        sys.stderr.write(f"\r  跑 entry {i}/{len(entries)}...")
        sys.stderr.flush()
        error_msg = ""
        try:
            if use_diag:
                cls, error_msg = enforce._classify_memory_route_diag(content, model)
            else:
                cls = enforce._classify_memory_route(content, model)
        except Exception as e:  # noqa: BLE001
            cls = None
            error_msg = f"audit 调用异常: {type(e).__name__}: {e}"

        if cls is None:
            if error_msg and not first_error_msg:
                first_error_msg = error_msg
                sys.stderr.write("\n")
                print(f"  ⚠ entry {i} classify 失败, 真错: {error_msg}", file=sys.stderr)
            results.append({
                "index": i, "target": target, "actual_target": actual_target,
                "content_preview": content[:100],
                "content_len": len(content),
                "llm_route": None,
                "llm_reason": error_msg or "classify 失败 (LLM 调挂 / parse 错)",
                "confidence": 0.0,
                "decision": "skip",  # 跳过, 不建议
                "content": content,
            })
            consecutive_skip += 1
            if consecutive_skip >= EARLY_ABORT_THRESHOLD:
                sys.stderr.write("\n")
                hint = (
                    f"  ⚠ 连续 {EARLY_ABORT_THRESHOLD} 条 classify 全挂, 早 abort.\n"
                    f"  verifier model: `{model}` ({src})\n"
                )
                if first_error_msg:
                    hint += f"  真错 (第一条 entry): {first_error_msg}\n"
                # 如果走的是 picker chain + 选了内网 model → 大概率离单位
                if not override_model.strip() and "private" in model:
                    hint += (
                        f"  原因可能: 这是内网 model, 离单位时 upstream 连不上.\n"
                        f"  临时方案 (不动 picker, 不破红线):\n"
                        f"    python3 scripts/audit-old-memory.py --model catfish-public-deepseek-flash\n"
                        f"  数据已在本机 MEMORY.md, 公网 model 只做 router 判断, 不新增数据出端."
                    )
                else:
                    hint += (
                        f"  上面真错信息是定位关键. 常见情况:\n"
                        f"    - HTTP 401/403 → audit 脚本没 OAuth token, gateway auth 拒了 (本应 X-Catfish-Internal 放行, 看 gateway 配)\n"
                        f"    - HTTP 400/422 → model 不接受 response_format=json_object (公网 deepseek/gemini 偶发)\n"
                        f"    - HTTP 502/503 → 上游 model 挂\n"
                        f"    - HTTP 异常 → gateway 8999 没起 (curl http://127.0.0.1:8999/v1/catalog)"
                    )
                print(hint, file=sys.stderr)
                return results  # 早 abort, 剩下 entry 不跑
            continue
        # 这条 classify 成功 → reset 连续 skip 计数
        consecutive_skip = 0

        route = cls["route"]
        # 决策:
        # - route == actual_target → keep (✅)
        # - route in (memory, user) 但 != actual_target → suggest_retarget (⚠️)
        # - route in (journal, todo, skill) → suggest_delete (❌)
        if route == actual_target:
            decision = "keep"
        elif route in ("memory", "user"):
            decision = "suggest_retarget"
        else:
            decision = "suggest_delete"

        results.append({
            "index": i, "target": target, "actual_target": actual_target,
            "content_preview": content[:100],
            "content_len": len(content),
            "llm_route": route,
            "llm_reason": cls.get("reason", ""),
            "confidence": cls.get("confidence", 0.0),
            "decision": decision,
            "content": content,
        })
    sys.stderr.write("\n")
    return results


# ── 报告 ──────────────────────────────────────────────────────────


def build_report(results: list[dict]) -> dict:
    """整理统计 + 分类报告."""
    keep = [r for r in results if r["decision"] == "keep"]
    retarget = [r for r in results if r["decision"] == "suggest_retarget"]
    delete = [r for r in results if r["decision"] == "suggest_delete"]
    skip = [r for r in results if r["decision"] == "skip"]
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "total": len(results),
        "keep": len(keep),
        "suggest_retarget": len(retarget),
        "suggest_delete": len(delete),
        "skip": len(skip),
        "entries": results,
    }


def render_markdown(rep: dict) -> str:
    lines: list[str] = []
    lines.append("# Hermes Memory 旧数据 LLM 审查报告")
    lines.append("")
    lines.append(f"- 生成时间: `{rep['generated_at']}`")
    lines.append(f"- 总 entry: **{rep['total']}**")
    lines.append(f"- ✅ 留 (route 跟 target 匹配): **{rep['keep']}**")
    lines.append(f"- ⚠️ 建议改 target (route 在 hermes 范围但不匹配): **{rep['suggest_retarget']}**")
    lines.append(f"- ❌ 建议删 (route 是 journal/todo/skill, 不该写 hermes): **{rep['suggest_delete']}**")
    if rep["skip"]:
        lines.append(f"- ⏭ 跳过 (classify 挂): **{rep['skip']}**")
    lines.append("")

    # 建议删
    delete = [e for e in rep["entries"] if e["decision"] == "suggest_delete"]
    if delete:
        lines.append(f"## ❌ 建议删 ({len(delete)})")
        lines.append("")
        lines.append("这些 entry 的 LLM 判定 route 是 journal/todo/skill, **不该**写到 hermes memory.")
        lines.append("建议手动执行 cleanup 命令删, 或在 Companion Dashboard 点 🗑.")
        lines.append("")
        for e in delete:
            lines.append(f"### Entry {e['index']} ({e['target']}.md, route={e['llm_route']})")
            lines.append("")
            lines.append(f"- 原因: {e['llm_reason']}")
            lines.append(f"- 置信度: {e['confidence']:.2f}")
            lines.append(f"- 内容预览: `{e['content_preview']}`")
            lines.append(f"- 长度: {e['content_len']} 字符")
            lines.append(f"- cleanup 命令:")
            lines.append(f"  ```")
            lines.append(f"  python3 scripts/hermes-memory-cleanup.py delete {e['target']} {e['index']}")
            lines.append(f"  ```")
            lines.append("")

    # 建议改 target
    retarget = [e for e in rep["entries"] if e["decision"] == "suggest_retarget"]
    if retarget:
        lines.append(f"## ⚠️ 建议改 target ({len(retarget)})")
        lines.append("")
        lines.append("这些 entry 内容是对的但 target 错了 (该写 USER.md 写到了 MEMORY.md, 反之).")
        lines.append("先 delete 再用对的 target 重写 (或让 LLM 自己重新 memory_update).")
        lines.append("")
        for e in retarget:
            lines.append(f"### Entry {e['index']} ({e['target']}.md → 应改 target={e['llm_route']})")
            lines.append("")
            lines.append(f"- 原因: {e['llm_reason']}")
            lines.append(f"- 置信度: {e['confidence']:.2f}")
            lines.append(f"- 内容预览: `{e['content_preview']}`")
            lines.append("")

    # 跳过的: 不罗列内容预览 (鸿波 6/20 catch '没用的都删掉, 留着没意义').
    # 只在 LLM 没全挂时给一句计数提示; 全挂时 main 已 exit 1, 这报告不会写出来.
    skip_count = sum(1 for e in rep["entries"] if e["decision"] == "skip")
    if skip_count and skip_count < rep["total"]:
        lines.append(f"## ⏭ 跳过 ({skip_count})")
        lines.append("")
        lines.append(
            f"{skip_count} 条 entry classify 挂 (LLM 偶发抖). 重跑脚本可恢复, 没全挂的报告内容不受影响."
        )
        lines.append("")

    # 留的 (简略)
    keep = [e for e in rep["entries"] if e["decision"] == "keep"]
    if keep:
        lines.append(f"## ✅ 留 ({len(keep)})")
        lines.append("")
        lines.append("这些 entry LLM 判定跟 target 匹配, 留.")
        lines.append("")
        for e in keep:
            lines.append(f"- Entry {e['index']} ({e['target']}.md, {e['llm_route']}): `{e['content_preview']}` ({e['confidence']:.2f})")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("**操作建议**:")
    lines.append("")
    lines.append('1. 先审 "❌ 建议删" 区, 一条条看 cleanup 命令再决定 (不要批量盲删)')
    lines.append('2. 再审 "⚠️ 建议改 target" 区, 改对 target 重写')
    lines.append("3. ⏭ 跳过的重跑脚本看能不能恢复")
    lines.append("4. 删前都有自动 backup (hermes-memory-cleanup.py 装了)")
    lines.append("")
    lines.append("**复用 P3.5.42 memory_enforce LLM 校验**: 跟 pre_tool_call hook 同 schema / picker chain.")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()

    enforce = _load_memory_enforce()
    if enforce is None:
        print("⚠ 没找到 memory_enforce.py — 装 catfish-xcatfish-user plugin 或 clone catfish 仓库",
              file=sys.stderr)
        print(f"  搜过路径: {[str(p) for p in _ENFORCE_PATHS]}", file=sys.stderr)
        return 1

    targets = ["USER", "MEMORY"] if args.target == "BOTH" else [args.target]
    all_results: list[dict] = []
    for target in targets:
        entries = _read_entries(target)
        if not entries:
            print(f"# {target}.md 空 / 不存在, skip", file=sys.stderr)
            continue
        results = _classify_all(target, entries, enforce, override_model=args.model)
        all_results.extend(results)

    if not all_results:
        print("⚠ 没扫到任何 entry, 退出", file=sys.stderr)
        return 1

    # 鸿波 6/20 catch '没用的都删掉, 留着没意义':
    # 如果 classify 全挂 (zero 有效判定) → 不写没意义的报告, 直接 exit 1.
    # 早 abort 时 _classify_all 已经 stderr 报过原因, 这只是兜底.
    valid_count = sum(1 for r in all_results if r["decision"] != "skip")
    if valid_count == 0:
        skipped = len(all_results)
        used_model = args.model.strip() or "(picker chain)"
        print(
            f"\n❌ {skipped}/{skipped} entry classify 全挂, 不写报告 (没意义).\n"
            f"   model={used_model}\n"
            f"   离单位 + picker 选内网 model 时, 加 --model 走公网:\n"
            f"     python3 scripts/audit-old-memory.py --model catfish-public-deepseek-flash",
            file=sys.stderr,
        )
        return 1

    rep = build_report(all_results)

    if args.as_json:
        out = json.dumps(rep, ensure_ascii=False, indent=2, default=str)
    else:
        out = render_markdown(rep)

    if args.save:
        date_str = datetime.now().strftime("%Y-%m-%d")
        save_dir = Path.home() / ".catfish" / "outputs" / date_str
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / ("old-memory-audit.json" if args.as_json else "old-memory-audit.md")
        save_path.write_text(out, encoding="utf-8")
        print(f"✓ 报告已存 {save_path}", file=sys.stderr)
    else:
        print(out)

    return 0


if __name__ == "__main__":
    sys.exit(main())
