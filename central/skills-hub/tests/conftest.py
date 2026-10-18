"""skills-hub 测试隔离 (10/18)。

app.py 一 import 就 load 本机 .env (override=False), 里面的 CATFISH_DB_URL 指向开发机真库 —— 10/18 跑测试时
存储测试因此往本机中央的 skills_versions / skills_audit 写了 18 个版本 + 27 条审计 (已删)。
这里在任何测试 import app 之前把 CATFISH_DB_URL 设成空 (dotenv 不会覆盖已存在的变量) → 全走文件模式;
CATFISH_HUB_ROOT 也默认指到临时目录, 不碰 ~/.catfish-hub。单个测试自己 setenv 会覆盖这里。
"""
from __future__ import annotations

import os
import tempfile

os.environ["CATFISH_DB_URL"] = ""
os.environ.setdefault("CATFISH_HUB_ROOT", tempfile.mkdtemp(prefix="catfish-hub-test-"))
