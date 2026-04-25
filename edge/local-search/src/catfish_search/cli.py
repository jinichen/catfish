"""catfish-search 命令行入口。

子命令：
    index    扫描并建立索引
    query    全文查询
    status   索引库状态
    clean    清理已删除文件的索引
    config   打开配置文件
    watch    前台启动 watcher（Ctrl+C 退出）
    daemon   把 watcher 装成 launchd 守护进程（macOS 专用）
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys

from .config import CONFIG_FILE, ensure_config_exists, load_config
from .indexer import cleanup_missing, run_index
from .query import search, stats_summary

logger = logging.getLogger("catfish.search.cli")


def cmd_index(args) -> int:
    cfg = load_config()
    if not cfg.include:
        print("没有可索引的目录。请检查 ~/.catfish/search-scope.yaml")
        return 1

    print(f"开始索引 {len(cfg.include)} 个目录...")
    for p in cfg.include:
        print(f"  -> {p}")

    def on_progress(stats):
        if args.quiet:
            return
        sys.stdout.write(
            f"\r  已扫 {stats['scanned']}  已索引 {stats['indexed']}  "
            f"跳过 {stats['skipped']}"
        )
        sys.stdout.flush()

    stats = run_index(cfg, on_progress=on_progress)
    if not args.quiet:
        print()
    print(
        f"完成。扫 {stats['scanned']} / 新索引 {stats['indexed']} / "
        f"跳过 {stats['skipped']}  耗时 {stats['duration_sec']}s"
    )
    return 0


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


def cmd_watch(_args) -> int:
    from .watcher import run_watch  # noqa: PLC0415  延迟导入，没装 watchdog 时不影响别的命令
    print("启动 watcher（前台）。Ctrl+C 退出。")
    try:
        return run_watch()
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
    p_index.set_defaults(func=cmd_index)

    p_query = sub.add_parser("query", help="全文查询")
    p_query.add_argument("keyword", help="搜索关键词")
    p_query.add_argument("--limit", "-n", type=int, default=10)
    p_query.add_argument("--json", action="store_true", help="JSON 输出")
    p_query.set_defaults(func=cmd_query)

    p_status = sub.add_parser("status", help="查看索引库状态")
    p_status.set_defaults(func=cmd_status)

    p_clean = sub.add_parser("clean", help="清理已删除文件的索引")
    p_clean.set_defaults(func=cmd_clean)

    p_config = sub.add_parser("config", help="编辑 search-scope.yaml")
    p_config.set_defaults(func=cmd_config)

    p_watch = sub.add_parser("watch", help="前台启动 watcher（Ctrl+C 退出）")
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
