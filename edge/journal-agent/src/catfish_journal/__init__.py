"""catfish-journal — 让小鲶通过对话改员工日记 TODO.

模块结构:
  - core.py: 真改 journal 的纯函数 (无 IO 依赖, 给单测用)
  - cli.py:  CLI argparse 入口, 调 core 函数 + 读写 ~/.catfish/employee_journal.md
  - __main__.py: console_script entry point
"""

__version__ = "0.1.0"
