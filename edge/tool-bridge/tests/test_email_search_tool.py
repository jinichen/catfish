"""BL-EMAIL-SEARCH-TOOL 测试 — catfish_email_search native tool.

shell out catfish-email search --json, 测试用 monkeypatch subprocess.run
+ shutil.which 控制 fake CLI 行为, 不依赖真 Apple Mail / Foxmail.
"""
from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch, MagicMock

from catfish_tool_bridge import email_search


def _fake_run(stdout: str = "[]", stderr: str = "", returncode: int = 0):
    """构造 subprocess.run 返的 CompletedProcess 假值."""
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


class TestEmailSearchTool(unittest.TestCase):
    """tool_email_search 入口."""

    def test_missing_query_returns_error(self):
        out = email_search.tool_email_search({})
        self.assertFalse(out["ok"])
        self.assertEqual(out["matches"], [])
        self.assertIn("query 必填", out["error"])

    def test_empty_query_returns_error(self):
        out = email_search.tool_email_search({"query": "   "})
        self.assertFalse(out["ok"])

    def test_cli_not_found_friendly_error(self):
        with patch.object(email_search, "_find_catfish_email", return_value=None):
            out = email_search.tool_email_search({"query": "x"})
        self.assertFalse(out["ok"])
        self.assertIn("catfish-email CLI 没装", out["summary"])
        self.assertIn("not found", out["error"])

    def test_success_path_returns_matches_with_summary(self):
        fake_items = [
            {
                "id": "apple_mail|alice@x.com|1",
                "adapter": "apple_mail",
                "account": "iCloud",
                "subject": "工资条 4 月",
                "sender": "HR <hr@x.com>",
                "date": "2026-04-15T10:00:00",
                "is_read": True,
                "body_text": "本月工资条详见附件" + "x" * 300,
            },
            {
                "id": "foxmail-mac|hongbo@qq.com|99",
                "adapter": "foxmail_mac",
                "account": "hongbo@qq.com",
                "subject": "工资条 3 月",
                "sender": "财务",
                "date": "2026-03-15T10:00:00",
                "is_read": False,
                "body_text": "三月工资发了",
            },
        ]
        with patch.object(email_search, "_find_catfish_email", return_value="/fake/catfish-email"), \
             patch("subprocess.run", return_value=_fake_run(stdout=json.dumps(fake_items))):
            out = email_search.tool_email_search({"query": "工资条"})

        self.assertTrue(out["ok"])
        self.assertEqual(out["count"], 2)
        self.assertEqual(len(out["matches"]), 2)
        # snippet 截到 200 字
        self.assertLessEqual(len(out["matches"][0]["snippet"]), 200)
        # summary 按 adapter 分组
        self.assertIn("apple_mail", out["summary"])
        self.assertIn("foxmail_mac", out["summary"])
        self.assertIn("'工资条'", out["summary"])
        # matches 不含 body_text 原字段 (隐私 / token 省)
        self.assertNotIn("body_text", out["matches"][0])
        # snippet 字段在
        self.assertIn("snippet", out["matches"][0])

    def test_empty_result_returns_friendly_no_results(self):
        with patch.object(email_search, "_find_catfish_email", return_value="/fake/catfish-email"), \
             patch("subprocess.run", return_value=_fake_run(stdout="[]")):
            out = email_search.tool_email_search({"query": "找不到的"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["count"], 0)
        self.assertIn("没找到", out["summary"])
        self.assertIn("找不到的", out["summary"])

    def test_cli_nonzero_exit_returns_error(self):
        with patch.object(email_search, "_find_catfish_email", return_value="/fake/catfish-email"), \
             patch("subprocess.run", return_value=_fake_run(stderr="something went wrong", returncode=1)):
            out = email_search.tool_email_search({"query": "x"})
        self.assertFalse(out["ok"])
        self.assertIn("退出码 1", out["summary"])
        self.assertIn("something went wrong", out["error"])

    def test_subprocess_timeout_returns_friendly_error(self):
        with patch.object(email_search, "_find_catfish_email", return_value="/fake/catfish-email"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="catfish-email", timeout=10)):
            out = email_search.tool_email_search({"query": "x"})
        self.assertFalse(out["ok"])
        self.assertIn("超时", out["summary"])

    def test_subprocess_oserror_returns_friendly_error(self):
        with patch.object(email_search, "_find_catfish_email", return_value="/fake/catfish-email"), \
             patch("subprocess.run", side_effect=OSError("Permission denied")):
            out = email_search.tool_email_search({"query": "x"})
        self.assertFalse(out["ok"])
        self.assertIn("调用失败", out["summary"])

    def test_invalid_json_output_handled(self):
        """CLI 漏不合法 JSON → 友好降级, 不挂"""
        with patch.object(email_search, "_find_catfish_email", return_value="/fake/catfish-email"), \
             patch("subprocess.run", return_value=_fake_run(stdout="not valid json")):
            out = email_search.tool_email_search({"query": "x"})
        self.assertFalse(out["ok"])
        self.assertIn("JSON 解析失败", out["summary"])

    def test_limit_clamped_to_max(self):
        """limit > 50 被 clamp 到 50"""
        with patch.object(email_search, "_find_catfish_email", return_value="/fake/catfish-email"), \
             patch("subprocess.run", return_value=_fake_run(stdout="[]")) as m:
            email_search.tool_email_search({"query": "x", "limit": 999})
        # subprocess.run 第一个位置参数是 cmd list
        cmd = m.call_args[0][0]
        # 找 --limit 后面紧跟的值
        idx = cmd.index("--limit")
        self.assertEqual(cmd[idx + 1], "50")

    def test_limit_floor_minimum(self):
        """limit < 1 被 clamp 到 1"""
        with patch.object(email_search, "_find_catfish_email", return_value="/fake/catfish-email"), \
             patch("subprocess.run", return_value=_fake_run(stdout="[]")) as m:
            email_search.tool_email_search({"query": "x", "limit": 0})
        cmd = m.call_args[0][0]
        idx = cmd.index("--limit")
        self.assertEqual(cmd[idx + 1], "1")

    def test_account_param_passed_to_cli(self):
        """传 account → CLI 加 --account flag"""
        with patch.object(email_search, "_find_catfish_email", return_value="/fake/catfish-email"), \
             patch("subprocess.run", return_value=_fake_run(stdout="[]")) as m:
            email_search.tool_email_search({"query": "x", "account": "alice@x.com"})
        cmd = m.call_args[0][0]
        self.assertIn("--account", cmd)
        idx = cmd.index("--account")
        self.assertEqual(cmd[idx + 1], "alice@x.com")

    def test_folder_default_star_means_all(self):
        """folder 不传默认 * (跨所有文件夹搜)"""
        with patch.object(email_search, "_find_catfish_email", return_value="/fake/catfish-email"), \
             patch("subprocess.run", return_value=_fake_run(stdout="[]")) as m:
            email_search.tool_email_search({"query": "x"})
        cmd = m.call_args[0][0]
        idx = cmd.index("--folder")
        self.assertEqual(cmd[idx + 1], "*")


if __name__ == "__main__":
    unittest.main()
