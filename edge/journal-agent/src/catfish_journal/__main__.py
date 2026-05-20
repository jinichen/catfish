"""console_script entry point. 走 cli.main(). 让 `python -m catfish_journal` 也跑通."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())


# console_scripts 在 pyproject.toml [project.scripts] 注册成 main:
# 这里 re-export 一份
__all__ = ["main"]
