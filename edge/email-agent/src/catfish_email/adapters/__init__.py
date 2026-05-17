"""邮件客户端适配器集合。每个 client × OS 一个 adapter, 共用 EmailAdapter ABC。

工厂入口在 catfish_email.inbox.get_adapter, 不直接 import 子模块。

当前 / 规划:
  - foxmail_mac.py    ✅ 实现 (SQLite + .mail 文件, 只读)
  - apple_mail.py     ⏳ scaffold (5/17 BL-EMAIL-APPLEMAIL, AppleScript-first)
  - outlook_win.py    ⏳ planned (pywin32 COM)
  - foxmail_win.py    ⏳ planned (.box parser)

5/17 调整: macOS 端 outlook_mac.py → apple_mail.py
  Mail.app 100% 装机 / AS dictionary 完整 / 免 Microsoft 365.
  详见 DESIGN.md 1.2 + 4.1.
"""
