"""11/10 日志 / 长期记忆只许追加: 11/09 一行 open(journal,"w").write(open(journal).read()+…) 把整本日志清空了。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location("jg", Path(__file__).resolve().parents[1] / "journal_guard.py")
jg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(jg)

J = "/Users/x/.catfish/employee_journal.md"


def blocked(tool, **args):
    return jg.journal_guard_hook(tool_name=tool, args=args) is not None


def test_11_09_那一行被拦():
    code = f'p="{J}"\nopen(p,"w",encoding="utf-8").write(open(p,encoding="utf-8").read().rstrip()+"\\n\\n"+node+"\\n")'
    assert blocked("execute_code", code=code)


def test_截掉重写_write_text_删除_改名都拦():
    assert blocked("execute_code", code=f"s=open('{J}').read()\nwith open('{J}','w') as f: f.write(s[:10])")
    assert blocked("execute_code", code=f"from pathlib import Path\nPath('{J}').write_text('x')")
    assert blocked("execute_code", code=f"import os\nos.remove('{J}')")
    assert blocked("execute_code", code="import os\nos.replace('/tmp/x', '/Users/x/.catfish/distilled_facts.md')")
    assert blocked("terminal", command=f"echo hi > {J}")
    assert blocked("terminal", command=f"sed -i '' 's/a/b/' {J}")
    assert blocked("terminal", command=f"rm {J}")
    assert blocked("write_file", path=J, content="x")
    assert blocked("patch", path="/Users/x/.catfish/distilled_facts.md")


def test_追加和读取照常():
    assert not blocked("execute_code", code=f"with open('{J}','a',encoding='utf-8') as f: f.write('## [x] journal | y')")
    assert not blocked("execute_code", code=f"print(open('{J}',encoding='utf-8').read()[-500:])")
    assert not blocked("execute_code", code=f"s=open('{J}').read(); print(s.replace('a','b')[:100])")
    assert not blocked("terminal", command=f"tail -20 {J}")
    assert not blocked("terminal", command=f"echo '## [x] journal | y' >> {J}")
    assert not blocked("terminal", command=f"grep -n ITSS {J}")
    assert not blocked("execute_code", code="open('/tmp/other.md','w').write('x')")   # 别的文件不管
    assert not blocked("write_file", path="/Users/x/.catfish/outputs/a.md")
    assert not blocked("memory", content="x")


def test_拦下时告诉它怎么记():
    msg = jg.journal_guard_hook(tool_name="execute_code", args={"code": f"open('{J}','w')"})["message"]
    assert "kind='journal'" in msg and "更正" in msg
