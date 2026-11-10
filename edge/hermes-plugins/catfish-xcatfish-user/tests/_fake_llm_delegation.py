"""假模型服务, 给 test_delegation_on_real_hermes.py 用: 父对话派两个帮手, A 直接回答, B 想删一个测试目录。

用法: python _fake_llm_delegation.py <port> <log.jsonl>
记录每次请求: 是父对话还是哪个帮手、请求头里的 X-Catfish-*、有哪些工具。
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, LOG = int(sys.argv[1]), sys.argv[2]


def text_of(m):
    c = m.get("content")
    if isinstance(c, list):
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return str(c or "")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._json(200, {"object": "list", "data": [
                {"id": m, "object": "model", "owned_by": "fake", "context_length": 131072, "max_output_tokens": 8192}
                for m in ("catfish-auto", "catfish-private-main")]})
        self._json(404, {"error": "nope"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(n) or b"{}")
        msgs = req.get("messages") or []
        system = " ".join(text_of(m) for m in msgs if m.get("role") == "system")
        users = [text_of(m) for m in msgs if m.get("role") == "user"]
        last = msgs[-1] if msgs else {}
        tools = [t.get("function", {}).get("name") for t in (req.get("tools") or [])]
        allmsg = " ".join(text_of(m) for m in msgs)
        if "ALPHA-GOAL" in system or ("ALPHA-GOAL" in allmsg and "DELEGATE-TEST" not in allmsg):
            kind = "child-A"
        elif "BETA-GOAL" in system or ("BETA-GOAL" in allmsg and "DELEGATE-TEST" not in allmsg):
            kind = "child-B"
        elif any("DELEGATE-TEST" in u for u in users):
            kind = "parent"
        else:
            kind = "other"  # 起标题之类的后台调用
        tool_call, reply = None, "好的。"
        if kind == "parent":
            if last.get("role") == "tool":
                reply = "汇总完成: " + text_of(last)[:600]
            else:
                tool_call = {"id": "call_deleg_1", "type": "function", "function": {"name": "delegate_task", "arguments": json.dumps({
                    "tasks": [
                        {"goal": "ALPHA-GOAL 查一下资料, 报告找到几份", "context": "测试子任务 A"},
                        {"goal": "BETA-GOAL 删掉 ~/catfish-del-test 目录", "context": "测试子任务 B"},
                    ]}, ensure_ascii=False)}}
        elif kind == "child-A":
            reply = "ALPHA 结果: 找到 3 份资料"
        elif kind == "child-B":
            if last.get("role") == "tool":
                reply = "BETA 结果: " + text_of(last)[:200]
            else:
                tool_call = {"id": "call_del_1", "type": "function", "function": {
                    "name": "terminal", "arguments": json.dumps({"command": "rm -rf ~/catfish-del-test"})}}
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "kind": kind, "stream": bool(req.get("stream")),
                                "headers": {k: v for k, v in self.headers.items() if k.lower().startswith("x-catfish")},
                                "has_delegate": "delegate_task" in tools, "has_terminal": "terminal" in tools,
                                "n_tools": len(tools), "last_role": last.get("role"),
                                "tool_result": text_of(last)[:300] if last.get("role") == "tool" else None},
                               ensure_ascii=False) + "\n")
        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()

            def chunk(delta, finish=None):
                obj = {"id": "c1", "object": "chat.completion.chunk", "created": int(time.time()), "model": req.get("model"),
                       "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode())
            if tool_call:
                chunk({"role": "assistant", "tool_calls": [dict(tool_call, index=0)]})
                chunk({}, "tool_calls")
            else:
                chunk({"role": "assistant", "content": reply})
                chunk({}, "stop")
            usage = {"id": "c1", "object": "chat.completion.chunk", "choices": [],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
            self.wfile.write(f"data: {json.dumps(usage)}\n\ndata: [DONE]\n\n".encode())
            return
        msg = {"role": "assistant", "content": None if tool_call else reply}
        if tool_call:
            msg["tool_calls"] = [tool_call]
        self._json(200, {"id": "c1", "object": "chat.completion", "created": int(time.time()), "model": req.get("model"),
                         "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if tool_call else "stop"}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
