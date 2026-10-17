"""catfish_form 工具的入口 + Companion 界面的 pim/form_* (docs/FORM-COLLECT-PLAN.md)。

小鲶能做: 看表 / 建表 / 从 Excel 建表 / 发 / 催 / 结束 / 看回答 / 导出 / 替员工预填草稿 / 查同事。
发、催、结束会给同事发消息: 先不带 confirmed 调, 把对象念给员工, 员工同意才带 confirmed=true。
**提交和不填只能员工自己在表单上点** —— 工具里没有这两个动作。
"""
from __future__ import annotations

from typing import Any

from . import forms, forms_excel, forms_net

_OUTWARD = {"send", "remind", "close"}


def _proposal(action: str, form_id: str) -> dict[str, Any]:
    form = forms.load_outgoing(form_id)
    if action == "send":
        who = [e for e, v in form["recipients"].items() if not v.get("invited_at")]
        what = f"把《{form['title']}》({len(form['fields'])} 项, 截止 {form.get('deadline') or '未定'}) 发给"
    elif action == "remind":
        answered = forms.responses_of(form_id)
        who = [e for e, v in form["recipients"].items() if v.get("invited_at") and e not in answered]
        what = f"催《{form['title']}》还没交的"
    else:
        who = [e for e, v in form["recipients"].items() if v.get("invited_at")]
        what = f"结束《{form['title']}》的收集, 通知"
    return {"ok": True, "needs_confirmation": True, "action": action, "form_id": form_id,
            "proposal": f"{what} {len(who)} 人: {'、'.join(who) or '(没有人)'}",
            "note": "念给员工听, 员工同意后带 confirmed=true 再调"}


def dispatch(name: str, args: dict[str, Any]) -> dict[str, Any]:
    action = str(args.get("action") or "").strip()
    form_id = str(args.get("form_id") or "").strip()
    try:
        if action == "list":
            return forms.list_all()
        if action in ("get", "status"):
            return forms.get(form_id)
        if action == "responses":
            return forms.responses_table(form_id)
        if action == "from_excel":
            return forms_excel.infer_fields(str(args.get("path") or ""))
        if action == "create":
            return forms.create(args)
        if action == "update":
            return forms.update_draft(form_id, args)
        if action == "export":
            return forms_excel.export(form_id)
        if action == "draft":
            return forms.save_draft(form_id, args.get("values"), args.get("sources"))
        if action == "directory":
            return {"ok": True, **forms_net.directory(str(args.get("text") or ""), int(args.get("limit") or 30))}
        if action in _OUTWARD:
            if not args.get("confirmed"):
                return _proposal(action, form_id)
            if action == "send":
                return forms.send(form_id)
            if action == "remind":
                return forms.remind(form_id, str(args.get("text") or ""))
            return forms.close(form_id)
        raise ValueError("action 只能是 list / get / status / responses / from_excel / create / update / "
                         "send / remind / close / export / draft / directory")
    except (ValueError, LookupError, OSError) as e:
        return {"ok": False, "error": str(e)}


# Companion 界面 (Rust commands/forms.rs → tool-bridge pim/form_*)。界面上的按钮本身就是员工的确认,
# 所以发 / 催 / 结束这里不再要 confirmed; 提交 / 不填只走这里。
def pim_handlers(params: dict[str, Any]) -> dict[str, Any]:
    fid = str(params.get("form_id") or "")
    return {
        "pim/form_list": forms.list_all,
        "pim/form_get": lambda: forms.get(fid),
        "pim/form_sync": forms.sync,
        "pim/form_from_excel": lambda: forms_excel.infer_fields(str(params.get("path") or "")),
        "pim/form_create": lambda: forms.create(params),
        "pim/form_update": lambda: forms.update_draft(fid, params),
        "pim/form_send": lambda: forms.send(fid),
        "pim/form_remind": lambda: forms.remind(fid, str(params.get("text") or "")),
        "pim/form_close": lambda: forms.close(fid),
        "pim/form_export": lambda: forms_excel.export(fid),
        "pim/form_draft": lambda: forms.save_draft(fid, params.get("values"), params.get("sources")),
        "pim/form_submit": lambda: forms.submit(fid, params.get("values")),
        "pim/form_decline": lambda: forms.decline(fid, str(params.get("reason") or "")),
        "pim/form_directory": lambda: forms_net.directory(str(params.get("q") or ""), int(params.get("limit") or 50)),
    }
