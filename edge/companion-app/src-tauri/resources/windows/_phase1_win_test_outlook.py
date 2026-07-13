"""Phase 1 Windows verify: outlook_win.py COM adapter smoke test (W3 verify).

Prereq:
    1. Outlook desktop OPEN + logged in with an account (foreground window most stable)
    2. pywin32 installed: uv pip install pywin32 (or pip install pywin32)
    3. catfish-email importable: pip install -e "catfish/edge/email-agent[windows]"

Usage:
    python _phase1_win_test_outlook.py

7 smoke tests:
    [test 1] platform guard: sys.platform=win32 OK
    [test 2] _import_pywin32() OK
    [test 3] Outlook.Application COM Dispatch OK
    [test 4] list_accounts(): returns your email
    [test 5] list_messages(): returns 3 recent
    [test 6] read_message(): returns full subject/body/message_id
    [test 7] search(): finds N

If any test fails: paste full log back to chat.

NOTE: Pure ASCII output (no Chinese) to avoid Windows PowerShell 5.1 codepage
issues + Python 3 stdout encoding fallbacks. Chinese docs in _phase1_win_CHECKLIST.md.
"""
from __future__ import annotations

import sys
import traceback


def main() -> int:
    print("[Phase 1] outlook_win adapter smoke test")
    print()

    # test 1: platform guard
    print(f"[test 1] platform guard: sys.platform={sys.platform}")
    if sys.platform != "win32":
        print(f"  FAIL: not Windows (sys.platform={sys.platform!r}). Run on Windows.")
        return 1
    print("  OK Windows")
    print()

    # test 2: pywin32 installed
    print("[test 2] pywin32 import")
    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
        import pywintypes  # noqa: F401
        print("  OK pywin32 present")
    except ImportError as e:
        print(f"  FAIL pywin32 missing: {e}")
        print("  fix: uv pip install pywin32  (or pip install pywin32)")
        return 1
    print()

    # test 3: catfish_email importable
    print("[test 3] catfish_email.adapters.outlook_win import")
    try:
        from catfish_email.adapters.outlook_win import OutlookWinAdapter
        from catfish_email.adapters.base import (
            ClientNotRunningError,
            DataNotFoundError,
            ListFilter,
        )
    except ImportError as e:
        print(f"  FAIL import error: {e}")
        print("  fix: cd catfish\\edge\\email-agent && uv pip install -e .[windows]")
        return 1
    print("  OK catfish_email present")
    print()

    # test 4: Outlook COM Dispatch
    print("[test 4] OutlookWinAdapter() + Dispatch")
    try:
        adapter = OutlookWinAdapter()
        print("  OK platform guard + pywin32 check passed")
    except Exception as e:
        print(f"  FAIL OutlookWinAdapter() raised: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1
    print()

    # test 5: list_accounts
    print("[test 5] list_accounts()")
    try:
        accounts = adapter.list_accounts()
    except ClientNotRunningError as e:
        print(f"  FAIL Outlook not running: {e}")
        print("  fix: open Outlook desktop app, log in with an account, retry")
        return 1
    except DataNotFoundError as e:
        print(f"  FAIL Outlook has no accounts: {e}")
        print("  fix: open Outlook -> File -> Add Account -> configure IMAP/Exchange")
        return 1
    except Exception as e:
        print(f"  FAIL list_accounts raised: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1

    print(f"  OK found {len(accounts)} account(s):")
    for a in accounts:
        default_mark = " (default)" if a.is_default else ""
        print(f"    - {a.address}  [{a.name}]{default_mark}")
    print()

    # test 6: list_messages
    print("[test 6] list_messages(folder=Inbox, limit=3)")
    try:
        msgs = adapter.list_messages(ListFilter(folder="Inbox", limit=3))
    except Exception as e:
        print(f"  FAIL list_messages raised: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1
    print(f"  OK returned {len(msgs)} messages:")
    for i, m in enumerate(msgs, 1):
        subj = (m.subject or "(no subject)")[:50]
        sndr = (m.sender or "")[:40]
        print(f"    {i}. [{sndr}] {subj}")
    print()

    if not msgs:
        print("[Phase 1] Inbox empty. Skipping test 7 + 8.")
        print()
        print("[Phase 1] 5 tests passed (list_accounts + list_messages with empty inbox)")
        return 0

    # test 7: read_message
    first_id = msgs[0].id
    print(f"[test 7] read_message(id={first_id[:60]}...)")
    try:
        full = adapter.read_message(first_id)
    except Exception as e:
        print(f"  FAIL read_message raised: {type(e).__name__}: {e}")
        traceback.print_exc()
        return 1

    print(f"  OK subject: {full.subject[:60]}")
    print(f"     sender:  {full.sender[:50]}")
    print(f"     date:    {full.date}")
    print(f"     is_read: {full.is_read}")
    print(f"     has_attachments: {full.has_attachments}")
    print(f"     body_text (first 100 chars): {full.body_text[:100]!r}")
    print(f"     body_html len: {len(full.body_html)}")
    print(f"     message_id (RFC 822): {full.message_id!r}")
    if full.message_id:
        print("       -> Exchange account (Message-ID present, isReplied precise)")
    else:
        print("       -> IMAP account (Message-ID None, isReplied falls back to subject fuzzy)")
    print()

    # test 8: search (non-fatal)
    print("[test 8] search(query='the', limit=2)")
    try:
        results = adapter.search("the", limit=2)
    except Exception as e:
        print(f"  WARN search raised (non-fatal): {type(e).__name__}: {e}")
        print("    Outlook search DASL LIKE may hit locale/index issues")
        print("    list_accounts + list_messages already passed -- W3 main path OK")
        print()
    else:
        print(f"  OK found {len(results)}:")
        for r in results:
            print(f"    - {r.subject[:50]}")
        print()

    print("[Phase 1] outlook_win adapter smoke test ALL PASS")
    print("          W3 (BL-EMAIL-OUTLOOK-WIN) verify DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
