"""蒸馏的保险 (11/10)。

# 为什么

11/09 16:59 日志被一行代码清空 (4000 来条 → 1 条); 19:53 蒸馏看到「日志换过了」按设计全量重建,
长期记忆 distilled_facts.md 从 18 万字变成 1.3KB, 覆盖前没有任何备份, 也没人发现 —— 设置页只是平静地显示两个数字。
增量蒸馏 (10/7) 本身没错, 错在「原料突然少了九成九」和「结果突然小了九成九」都被当成正常情况。

# 三道保险

1. 每天给日志留一份快照 (backups/journal/YYYY-MM-DD.md, 留 14 天)。日志比上次的快照小了一半以上就不覆盖快照。
2. 日志比上次蒸馏时消费过的长度短了一半以上 → 判「日志被清空 / 截断」: 这一轮不蒸, 不推游标, 不动长期记忆,
   在 state 里记 journal_shrunk (设置页会报警, 给出最近的快照)。
3. 写长期记忆前先把旧的备份到 backups/distilled/ (留 10 份); 新结果比旧的小了六成以上 → 不覆盖,
   在 state 里记 distilled_refused (设置页会报警)。真要换掉就从 backups/distilled/ 手工处理, 不给自动通道。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional

JOURNAL_SNAPSHOTS_KEEP = 14
DISTILLED_BACKUPS_KEEP = 10
#: 日志比上次消费过的长度短到这个比例以下 → 判被清空 / 截断
JOURNAL_SHRINK_RATIO = 0.5
#: 新长期记忆比旧的小到这个比例以下 (且旧的够大) → 不覆盖
DISTILLED_SHRINK_RATIO = 0.4
DISTILLED_MIN_BYTES_TO_GUARD = 8 * 1024


def _state_path(home: Path) -> Path:
    return home / "memory_distill_state.json"


def _update_state(home: Path, **fields: Any) -> None:
    try:
        state = json.loads(_state_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    for k, v in fields.items():
        if v is None:
            state.pop(k, None)
        else:
            state[k] = v
    try:
        _state_path(home).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _prune(folder: Path, keep: int) -> None:
    files = sorted(folder.glob("*.md"))
    for f in files[:-keep] if len(files) > keep else []:
        try:
            f.unlink()
        except OSError:
            pass


def latest_journal_snapshot(home: Path) -> Optional[Path]:
    snaps = sorted((home / "backups" / "journal").glob("*.md"))
    return snaps[-1] if snaps else None


def snapshot_journal(home: Path, journal_text: str, now: Optional[float] = None) -> Optional[Path]:
    """今天还没有快照就留一份。比最近一份快照小一半以上 (多半是被清空了) → 不留, 免得把好快照挤掉。"""
    if not journal_text.strip():
        return None
    folder = home / "backups" / "journal"
    folder.mkdir(parents=True, exist_ok=True)
    day = time.strftime("%Y-%m-%d", time.localtime(now or time.time()))
    target = folder / f"{day}.md"
    if target.exists():
        return None
    last = latest_journal_snapshot(home)
    if last is not None:
        try:
            if len(journal_text) < len(last.read_text(encoding="utf-8", errors="replace")) * JOURNAL_SHRINK_RATIO:
                return None
        except OSError:
            pass
    target.write_text(journal_text, encoding="utf-8")
    _prune(folder, JOURNAL_SNAPSHOTS_KEEP)
    return target


def journal_shrunk(home: Path, journal_text: str) -> Optional[Dict[str, Any]]:
    """日志比上次蒸馏消费过的长度短了一半以上 → 返回说明 (并记进 state), 否则 None。"""
    try:
        state = json.loads(_state_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    consumed = int(state.get("journal_consumed_chars") or 0)
    if consumed <= 0 or len(journal_text) >= consumed * JOURNAL_SHRINK_RATIO:
        if state.get("journal_shrunk"):
            _update_state(home, journal_shrunk=None)
        return None
    snap = latest_journal_snapshot(home)
    info = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "was_chars": consumed, "now_chars": len(journal_text),
            "snapshot": str(snap) if snap else None}
    _update_state(home, journal_shrunk=info)
    return info


def guarded_write_distilled(home: Path, new_text: str, write) -> bool:
    """先备份旧的长期记忆再写; 新的比旧的小了六成以上 → 不写, 返回 False (记进 state)。"""
    path = home / "distilled_facts.md"
    try:
        old_size = path.stat().st_size
    except OSError:
        old_size = 0
    new_size = len(new_text.encode("utf-8"))
    if (old_size >= DISTILLED_MIN_BYTES_TO_GUARD
            and new_size < old_size * DISTILLED_SHRINK_RATIO):
        _update_state(home, distilled_refused={"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                               "old_bytes": old_size, "new_bytes": new_size})
        return False
    if old_size:
        folder = home / "backups" / "distilled"
        folder.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(path, folder / f"{time.strftime('%Y%m%d-%H%M%S')}.md")
            _prune(folder, DISTILLED_BACKUPS_KEEP)
        except OSError:
            pass
    write(home, new_text)
    _update_state(home, distilled_refused=None)
    return True
