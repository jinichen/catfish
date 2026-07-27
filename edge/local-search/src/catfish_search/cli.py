"""catfish-search 命令行入口。

子命令：
    index    扫描并建立索引（--only DIR 只索引某一个已配置的根）
    query    全文查询
    status   索引库状态
    clean    清理已删除 / 已被 exclude 排除的索引条目
    config   打开配置文件
    watch    前台启动 watcher（Ctrl+C 退出；空库时先自动全量一次）
    daemon   把 watcher 装成 launchd 守护进程（macOS 专用）
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from .config import CONFIG_FILE, ensure_config_exists, load_config
from .indexer import cleanup_missing, full_index_lock, run_index
from .query import search, stats_summary

logger = logging.getLogger("catfish.search.cli")


def cmd_index(args) -> int:
    cfg = load_config()
    if not cfg.include:
        print("没有可索引的目录。请检查 ~/.catfish/search-scope.yaml")
        return 1

    # BL-SEARCH-NO-BOOTSTRAP (7/27): --only 只索引某一个已配置的根。
    # Companion 在员工刚加完目录时调它 —— 只补这一个，不用整库重扫。
    only = getattr(args, "only", None)
    if only:
        target = Path(only).expanduser().resolve()
        if target not in cfg.include:
            print(f"{target} 不在 search-scope.yaml 的 include 里，先加进去再索引。")
            return 1
        cfg = replace(cfg, include=[target])

    _report_missing_roots(cfg)

    print(f"开始索引 {len(cfg.include)} 个目录...")
    for p in cfg.include:
        print(f"  -> {p}")

    def on_progress(stats):
        if args.quiet:
            return
        sys.stdout.write(
            f"\r  已扫 {stats['scanned']}  新入库 {stats['indexed']}  "
            f"没变 {stats['unchanged']}  抽不出 {stats['failed']}"
        )
        sys.stdout.flush()

    # BL-SEARCH-DB-LOCKED (7/27): 跟 watcher 的 bootstrap 抢同一把锁。
    # 员工手点"重建索引"时 watcher 可能正在补建，两个一起扫除了白干还抢写锁。
    with full_index_lock() as got:
        if not got:
            print(
                "另一个进程正在做全量索引（多半是 Local Search watcher 在补建），"
                "等它跑完再来。\n看是谁: pgrep -fl 'catfish_search'"
            )
            return 1
        stats = run_index(cfg, on_progress=on_progress)
    if not args.quiet:
        print()
    # BL-SEARCH-STATS-MISLEADING (7/27): 老格式 "扫 6830 / 新索引 4 / 跳过 6826"
    # 把"内容没变不用重建"（正常，重跑时占绝大多数）和"抽不出文本"（异常）
    # 混成一个"跳过"，一次健康的重跑看着像 99.9% 都失败了。
    line = (
        f"完成。扫 {stats['scanned']} / 新入库 {stats['indexed']} / "
        f"内容没变 {stats['unchanged']}"
    )
    if stats["failed"]:
        line += f" / 抽不出文本 {stats['failed']}"
    print(f"{line}  耗时 {stats['duration_sec']}s")

    _report_per_root(stats)
    return 0


def _report_missing_roots(cfg) -> None:
    """BL-SEARCH-MISSING-ROOT-SILENT (7/27): 把"配了但拿不到"的目录喊出来。

    鸿波面板上配着 6 个目录，点"重建索引"只跑了 2 个，输出里连一句解释都没有 ——
    老 load_config 一句 `[p for p in deduped if p.exists()]` 就把它们静默滤掉了。
    """
    if not cfg.missing:
        return
    print(f"\n❌ yaml 的 include 里配了 {len(cfg.missing)} 个目录，但拿不到，本次不扫：")
    for p in cfg.missing:
        print(f"     {p}")
    print(
        "   要么路径写错了 / 目录已删；要么是 macOS 没给访问授权 —— \n"
        "   ~/Documents、~/Desktop、~/Downloads 属于受保护目录，没授权时\n"
        "   连「目录存不存在」都读不到。去 系统设置 → 隐私与安全性 →\n"
        "   文件与文件夹 / 完全磁盘访问权限，把跑索引的程序（鲶鱼 Companion\n"
        "   或 终端）勾上，然后重跑。\n"
    )


def _report_per_root(stats: dict) -> None:
    """BL-SEARCH-TCC-SILENT-SKIP (7/27 鸿波实盘): 逐个 include 根报数。

    老版本只报总数。目录读不了 / 配错了的时候，那个根 0 条会被别的根的
    几万条掩盖掉，看总数完全发现不了。

    报的是「索引库里这个根底下现有多少条」，不是「本次新增几条」——
    见 indexer._count_under 的注释。
    """
    print("\n各目录在索引库里的条数：")
    empty_roots = []
    for root, n in stats.get("per_root", {}).items():
        print(f"  {'  ' if n else '⚠️'} {n:6d}  {root}")
        if not n:
            empty_roots.append(root)

    unreadable = stats.get("unreadable") or []
    if unreadable:
        print("\n❌ 下面这些目录读不了（整棵没进索引）：")
        for root, reason in unreadable:
            print(f"     {root}\n       {reason}")
        print(
            "\n   macOS 上 ~/Documents、~/Desktop、~/Downloads 需要授权：\n"
            "   系统设置 → 隐私与安全性 → 文件与文件夹 / 完全磁盘访问权限，\n"
            "   把跑索引的这个程序（终端 或 鲶鱼 Companion）勾上，然后重跑。"
        )
    elif empty_roots:
        print(
            "\n⚠️ 上面标 ⚠️ 的目录一个文件都没索引到，但也没报权限错 —— "
            "确认里面确实有 file_types 段列的那些扩展名。"
        )


def cmd_query(args) -> int:
    hits = search(args.keyword, limit=args.limit)
    if not hits:
        print("未找到匹配。")
        return 0

    if args.json:
        print(json.dumps(
            [h.__dict__ for h in hits],
            ensure_ascii=False,
            indent=2,
        ))
        return 0

    for i, hit in enumerate(hits, 1):
        print(f"\n[{i}] {hit.title}  ({hit.file_type})")
        print(f"    {hit.path}")
        if hit.snippet.strip():
            print(f"    {hit.snippet[:200]}")
    print()
    return 0


def cmd_status(_args) -> int:
    ensure_config_exists()
    summary = stats_summary()
    print("索引库：~/.catfish/search.db")
    print(f"总文件数：{summary['total_files']}")
    print(f"总大小：{summary['total_size_mb']} MB")
    if summary["by_type"]:
        print("按类型：")
        for item in summary["by_type"]:
            print(f"  {item['ft']:10s}  {item['count']}")
    return 0


def cmd_clean(_args) -> int:
    cfg = load_config()
    removed = cleanup_missing(cfg)
    print(f"清理了 {removed} 条失效条目。")
    return 0


def cmd_config(_args) -> int:
    ensure_config_exists()
    print(CONFIG_FILE)
    editor = os.environ.get("EDITOR", "vi")
    try:
        subprocess.run([editor, str(CONFIG_FILE)], check=False)  # noqa: S603
    except FileNotFoundError:
        print(f"无法打开编辑器 {editor}，请手动编辑 {CONFIG_FILE}")
        return 1
    return 0


def cmd_watch(args) -> int:
    from .watcher import run_watch  # noqa: PLC0415  延迟导入，没装 watchdog 时不影响别的命令
    print("启动 watcher（前台）。Ctrl+C 退出。")
    try:
        return run_watch(bootstrap=not args.no_bootstrap)
    except RuntimeError as e:
        print(str(e))
        return 1


def cmd_daemon(args) -> int:
    from . import daemon  # noqa: PLC0415
    if args.action == "install":
        return daemon.install()
    if args.action == "uninstall":
        return daemon.uninstall()
    if args.action == "status":
        return daemon.status()
    print(f"未知的 daemon 操作：{args.action}")
    return 1


def main(argv=None) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        prog="catfish-search",
        description="鲶鱼本地文件搜索",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_index = sub.add_parser("index", help="扫描并建立索引")
    p_index.add_argument("--quiet", "-q", action="store_true", help="不打印进度")
    p_index.add_argument(
        "--only",
        metavar="DIR",
        help="只索引这一个已配置的根（Companion 在员工刚加完目录时用）",
    )
    p_index.set_defaults(func=cmd_index)

    p_query = sub.add_parser("query", help="全文查询")
    p_query.add_argument("keyword", help="搜索关键词")
    p_query.add_argument("--limit", "-n", type=int, default=10)
    p_query.add_argument("--json", action="store_true", help="JSON 输出")
    p_query.set_defaults(func=cmd_query)

    p_status = sub.add_parser("status", help="查看索引库状态")
    p_status.set_defaults(func=cmd_status)

    p_clean = sub.add_parser("clean", help="清理已删除 / 已被 exclude 排除的索引条目")
    p_clean.set_defaults(func=cmd_clean)

    p_config = sub.add_parser("config", help="编辑 search-scope.yaml")
    p_config.set_defaults(func=cmd_config)

    p_watch = sub.add_parser("watch", help="前台启动 watcher（Ctrl+C 退出）")
    p_watch.add_argument(
        "--no-bootstrap",
        action="store_true",
        help="库为空时也不自动做全量索引（默认会做一次）",
    )
    p_watch.set_defaults(func=cmd_watch)

    p_daemon = sub.add_parser(
        "daemon",
        help="把 watcher 装成后台服务（Mac=launchd / Windows=任务计划 / Linux=systemd）",
    )
    p_daemon.add_argument(
        "action",
        choices=["install", "uninstall", "status"],
        help="install=注册并启动；uninstall=卸载；status=看状态",
    )
    p_daemon.set_defaults(func=cmd_daemon)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
