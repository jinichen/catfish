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
        # P3.5.151: snippet 围绕 query 上下文, 上限 ≈ 2 * _PREVIEW_CHARS + len(query) + 2
        max_snippet = 2 * email_search._PREVIEW_CHARS + len("工资条") + 2
        self.assertLessEqual(len(out["matches"][0]["snippet"]), max_snippet)
        # snippet 应该含 query 关键词 (因 fake body 含"工资条")
        self.assertIn("工资条", out["matches"][0]["snippet"])
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


class TestBuildSnippet(unittest.TestCase):
    """P3.5.151: _build_snippet 围绕 query 上下文算法 (跟 sessions_search 一致)."""

    def test_short_body_returned_as_is_when_query_not_in_body(self):
        """body 短于 2 * _PREVIEW_CHARS 且 query 没命中 → 原文返回, 不加省略号"""
        out = email_search._build_snippet("短邮件正文", "找不到")
        self.assertEqual(out, "短邮件正文")

    def test_empty_body_returns_empty(self):
        self.assertEqual(email_search._build_snippet("", "x"), "")

    def test_query_in_middle_returns_window_around_hit(self):
        """query 在 body 中段 → snippet 围绕命中位置取 ±N 上下文, 前后带 …"""
        # 造一段超长 body, "智能体" 在正中间. 用不同字符让前后段 distinguishable.
        # body 总长 1000 + 14 + 1000 = 2014, "智能体" idx = 1003.
        # _PREVIEW_CHARS = 600 → snippet 窗口 [703, 1606], 长度 903 + 2 省略号 = 905.
        prefix = "前缀" * 500  # 1000 字
        suffix = "后缀" * 500  # 1000 字
        body = prefix + "评测的智能体列表如下: A B C" + suffix
        out = email_search._build_snippet(body, "智能体")
        # snippet 含 query
        self.assertIn("智能体", out)
        # snippet 含 query 后面的列表内容 (核心修法目标)
        self.assertIn("列表如下", out)
        # snippet 前后被截 → 带省略号
        self.assertTrue(out.startswith("…"))
        self.assertTrue(out.endswith("…"))
        # snippet 长度受 _PREVIEW_CHARS 限制, 远小于完整 body (2014 字)
        # 上限: 前 _PREVIEW_CHARS//2 (300) + len("智能体") (3) + _PREVIEW_CHARS (600) + 2 省略号
        max_len = email_search._PREVIEW_CHARS // 2 + len("智能体") + email_search._PREVIEW_CHARS + 2
        self.assertLessEqual(len(out), max_len)
        # 确认被截了 (snippet 不应回完整 body)
        self.assertLess(len(out), len(body))

    def test_query_not_in_body_falls_back_to_head_truncation(self):
        """query 没命中 body (可能命中 subject) → 从头截 2 * _PREVIEW_CHARS + …"""
        body = "x" * (email_search._PREVIEW_CHARS * 3)  # 超长 body
        out = email_search._build_snippet(body, "找不到的关键词")
        self.assertEqual(len(out), email_search._PREVIEW_CHARS * 2 + 1)  # +1 for …
        self.assertTrue(out.endswith("…"))

    def test_query_at_start_no_leading_ellipsis(self):
        """query 在 body 开头 → snippet 不加前置 …"""
        body = "智能体列表如下: A, B, C" + "x" * 1000
        out = email_search._build_snippet(body, "智能体")
        self.assertFalse(out.startswith("…"))
        self.assertIn("智能体", out)

    def test_query_at_end_no_trailing_ellipsis(self):
        """query 在 body 末尾 → snippet 不加后置 …"""
        body = "x" * 1000 + "智能体"
        out = email_search._build_snippet(body, "智能体")
        self.assertTrue(out.startswith("…"))
        self.assertFalse(out.endswith("…"))
        self.assertTrue(out.endswith("智能体"))

    def test_case_insensitive_match(self):
        body = "Subject AI agent 列表"
        out = email_search._build_snippet(body, "AI")
        self.assertIn("AI", out)


if __name__ == "__main__":
    unittest.main()
