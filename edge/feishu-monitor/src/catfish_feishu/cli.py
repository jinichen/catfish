"""catfish-feishu CLI 入口。

子命令：
    start       前台启动 monitor（阻塞）
    status      看配置 + CDP 可达性 + inbox/drafts 数
    test        模拟一条相关消息，走完三个 handler
    drafts      列出未处理的草稿
    inbox       列出 inbox 条目
    config      打开 ~/.catfish/feishu.yaml
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import CONFIG_FILE, DRAFTS_DIR, INBOX_DIR, ensure_config_exists, load_config, resolve_cdp_url
from .handlers import dispatch
from .relevance import Relevance, RelevanceVerdict, judge

logger = logging.getLogger("catfish.feishu.cli")


def cmd_start(_args) -> int:
    from .monitor import run_forever  # noqa: PLC0415 lazy import 启动时加载
    print("启动 catfish-feishu monitor（前台，Ctrl+C 退出）...")
    run_forever()
    return 0


def cmd_status(_args) -> int:
    cfg = load_config()
    print(f"配置文件：{CONFIG_FILE}")
    print()
    print("=== CDP ===")
    ws = resolve_cdp_url(cfg)
    if ws:
        print(f"  CDP URL: {ws}")
        # 快速 probe 下 9222 HTTP 端点
        try:
            import httpx  # noqa: PLC0415

            from .cdp_client import cdp_http_base_from_ws, pick_feishu_targets  # noqa: PLC0415
            http_base = cdp_http_base_from_ws(ws)
            r = httpx.get(f"{http_base}/json", timeout=3)
            r.raise_for_status()
            targets = r.json()
            feishu = pick_feishu_targets(targets, cfg.runtime.feishu_domains)
            print(f"  Chrome 可达。共 {len(targets)} 个 target，其中飞书 tab {len(feishu)} 个")
            for t in feishu:
                print(f"    - {t.get('title', '(no title)')} -> {t.get('url')}")
        except Exception as e:  # noqa: BLE001
            print(f"  CDP 不可达：{e}")
    else:
        print("  未配置 —— 先跑 catfish-browser-attach.sh")
    print()
    print("=== 关键词 ===")
    r = cfg.relevance
    print(f"  strong names: {r.strong_names}")
    print(f"  strong mentions: {r.strong_mentions}")
    print(f"  soft projects: {r.soft_projects}")
    print(f"  soft systems: {r.soft_systems}")
    print(f"  soft people: {r.soft_people}")
    if not (r.strong_names or r.strong_mentions):
        print("  ⚠ 你还没配强关键词（姓名 / @mention），所有群消息都只会走弱相关")
        print(f"    编辑 {CONFIG_FILE} 加上你的姓名")
    print()
    print("=== Inbox / Drafts ===")
    inbox = list(INBOX_DIR.glob("*.json")) if INBOX_DIR.exists() else []
    drafts = list(DRAFTS_DIR.glob("*.txt")) if DRAFTS_DIR.exists() else []
    print(f"  inbox  条目：{len(inbox)}")
    print(f"  drafts 条目：{len(drafts)}")
    return 0


def cmd_test(_args) -> int:
    """模拟一条相关消息，端到端走完三个 handler，用于验证。"""
    cfg = load_config()
    fake = {
        "type": "new_message",
        "conversation": "(test) 部门工作群",
        "sender": "某领导",
        "text": "@" + (cfg.relevance.strong_names[0] if cfg.relevance.strong_names else "X") +
                " 鲶鱼那个合规平台对接的进度怎么样了？",
        "timestamp": int(time.time()),
        "is_dm": False,
    }
    verdict = judge(
        message_text=fake["text"],
        sender=fake["sender"],
        is_direct_message=False,
        cfg=cfg.relevance,
    )
    print(f"消息：{fake['text']}")
    print(f"判定：level={verdict.level.value}  reason={verdict.reason}  matched={verdict.matched}")
    if verdict.level == Relevance.NONE:
        print("相关性 NONE，不触发 handler。在 ~/.catfish/feishu.yaml 里加你的姓名/项目关键词再试。")
        return 0

    result = dispatch(fake, verdict, cfg.handlers, cfg.draft)
    print(f"分发结果：{json.dumps(result, ensure_ascii=False, indent=2)}")
    return 0


def cmd_drafts(_args) -> int:
    if not DRAFTS_DIR.exists():
        print("(空)")
        return 0
    drafts = sorted(DRAFTS_DIR.glob("*.txt"))
    if not drafts:
        print("(空)")
        return 0
    for p in drafts:
        meta_path = p.with_suffix(".meta.json")
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        print(f"=== {p.name} ===")
        if meta:
            print(f"  生成于：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(meta.get('generated_at', 0)))}")
            print(f"  来自：{meta.get('sender')} @ {meta.get('conversation')}")
            print(f"  原消息片段：{meta.get('original_preview')}")
        print(f"  草稿：")
        print("    " + (p.read_text(encoding="utf-8").replace("\n", "\n    ")))
        print()
    return 0


def cmd_inbox(_args) -> int:
    if not INBOX_DIR.exists():
        print("(空)")
        return 0
    entries = sorted(INBOX_DIR.glob("*.json"))
    if not entries:
        print("(空)")
        return 0
    for p in entries:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        ts = time.strftime("%H:%M", time.localtime(d.get("received_at", 0)))
        level = d.get("relevance", {}).get("level", "?")
        sender = d.get("sender", "?")
        conv = d.get("conversation", "?")
        preview = (d.get("text", "") or "")[:60].replace("\n", " ")
        print(f"[{ts}] {level:6s} {sender} @ {conv}: {preview}")
    return 0


def cmd_config(_args) -> int:
    ensure_config_exists()
    print(CONFIG_FILE)
    editor = os.environ.get("EDITOR", "vi")
    try:
        subprocess.run([editor, str(CONFIG_FILE)], check=False)  # noqa: S603
    except FileNotFoundError:
        print(f"无法打开 {editor}，请手动编辑 {CONFIG_FILE}")
        return 1
    return 0


def main(argv=None) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        prog="catfish-feishu",
        description="鲶鱼飞书消息监听（本地，不走飞书 API）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start", help="前台启动 monitor")
    p_start.set_defaults(func=cmd_start)

    p_status = sub.add_parser("status", help="看状态（CDP / 关键词 / inbox / drafts）")
    p_status.set_defaults(func=cmd_status)

    p_test = sub.add_parser("test", help="模拟一条消息，端到端测三个 handler")
    p_test.set_defaults(func=cmd_test)

    p_drafts = sub.add_parser("drafts", help="列出待处理草稿")
    p_drafts.set_defaults(func=cmd_drafts)

    p_inbox = sub.add_parser("inbox", help="列出 inbox 条目")
    p_inbox.set_defaults(func=cmd_inbox)

    p_config = sub.add_parser("config", help="编辑 feishu.yaml")
    p_config.set_defaults(func=cmd_config)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
