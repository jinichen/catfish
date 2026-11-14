/** 早安「你装的共享内容」(11/14)。
 *
 * 装过的技能 / 专家包 / 部门知识页, 有新版的列出来带「更新」, 被作者或主管撤回的列出来 (本机已停用 / 已标「已撤回」),
 * 现在看不到了的 (换部门 / 作者把全公司收回) 也提一句。后台每 6 小时查一次 (Rust services/shared_check.rs),
 * 这里读上次的结果, 不联网; 「现在查」才联网。什么都没有不显示。
 *
 * 「知道了」只收起当前这一批: 记下这批的指纹, 有新的变化 (多了一条、状态变了) 会再出来。
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { config } from "../../lib/env";
import { toolBridgeCallTool } from "../../lib/tauri";
import { sharedCheckLast, sharedCheckRun, type SharedCheckResult, type SharedItem } from "../../lib/tauri_shared";
import { useUIStore } from "../../store/ui";
import { toolResultError } from "../Dashboard/wikiHubStatus";

const ACK_KEY = "catfish.sharedCheck.ack";
const KIND: Record<SharedItem["kind"], string> = { skill: "技能", expert: "专家", wiki: "知识页" };

function fingerprint(r: SharedCheckResult): string {
  const part = (xs: SharedItem[] | undefined, tag: string) =>
    (xs ?? []).map((x) => `${tag}:${x.kind}:${x.key}:${x.status}:${x.latest_version ?? ""}`);
  return [...part(r.updates, "u"), ...part(r.withdrawn, "w"), ...part(r.hidden, "h")].sort().join("|");
}

function readAck(): string {
  try {
    return localStorage.getItem(ACK_KEY) ?? "";
  } catch {
    return "";
  }
}

function writeAck(v: string) {
  try {
    localStorage.setItem(ACK_KEY, v);
  } catch {
    /* 存不了就只收起这一次 */
  }
}

function who(by: string | null | undefined): string {
  return by ? ` (${by})` : "";
}

export default function SharedContentStrip() {
  const [res, setRes] = useState<SharedCheckResult | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [ack, setAck] = useState(readAck);
  const setActiveTab = useUIStore((s) => s.setActiveTab);

  useEffect(() => {
    let alive = true;
    sharedCheckLast().then((r) => { if (alive) setRes(r); }).catch(() => undefined);
    return () => { alive = false; };
  }, []);

  const recheck = useCallback(async () => {
    setBusy("check");
    try {
      setRes(await sharedCheckRun());
    } catch (e) {
      setMsg(`没查成: ${String(e)}`);
    } finally {
      setBusy(null);
    }
  }, []);

  const update = useCallback(async (it: SharedItem) => {
    if (it.kind === "expert") {   // 专家包升级可能多要权限, 要在专家市场里确认
      setActiveTab("dashboard");
      return;
    }
    setBusy(it.key);
    setMsg(null);
    try {
      const r = it.kind === "skill"
        ? await toolBridgeCallTool("catfish_skill_install", {
          hub_skill: `${it.namespace}/${it.name}@${it.latest_version}`, hub_url: `${config.gatewayUrl}/v1/hub`, overwrite: true })
        : await toolBridgeCallTool("catfish_wiki_install", { hub_namespace: it.namespace, hub_file_id: it.file_id });
      const err = toolResultError(r);
      if (err) {
        setMsg(`${it.title} 没更新成: ${err}`);
        return;
      }
      setMsg(`${it.title} 已更新`);
      setRes(await sharedCheckRun());
    } catch (e) {
      setMsg(`${it.title} 没更新成: ${String(e)}`);
    } finally {
      setBusy(null);
    }
  }, [setActiveTab]);

  const fp = useMemo(() => (res ? fingerprint(res) : ""), [res]);
  if (!res || !fp || fp === ack) return null;
  const updates = res.updates ?? [];
  const withdrawn = res.withdrawn ?? [];
  const hidden = res.hidden ?? [];
  const bits = [
    updates.length ? `${updates.length} 个有新版` : "",
    withdrawn.length ? `${withdrawn.length} 个已被撤回` : "",
    hidden.length ? `${hidden.length} 个你现在看不到了` : "",
  ].filter(Boolean);

  return (
    <div role="status" className="shared-strip">
      <div className="shared-strip__head">
        <span className="shared-strip__summary">🔄 你装的共享内容: {bits.join(" · ")}</span>
        <button type="button" className="chat-composer__secondary-button" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "收起" : "看看"}</button>
        <button type="button" className="chat-composer__secondary-button" onClick={() => void recheck()} disabled={busy !== null}>
          {busy === "check" ? "查询中…" : "现在查"}
        </button>
        <button type="button" className="chat-composer__secondary-button" onClick={() => { writeAck(fp); setAck(fp); }}>知道了</button>
      </div>
      {open && (
        <ul className="shared-strip__list">
          {updates.map((it) => (
            <li key={`u-${it.key}`}>
              <span className="shared-strip__kind">{KIND[it.kind]}</span>
              <span className="shared-strip__title">{it.title}</span>
              <span className="shared-strip__meta">
                {it.kind === "wiki" ? "作者改过了" : `v${it.installed_version} → v${it.latest_version}`}
              </span>
              <button type="button" className="chat-composer__primary-button" onClick={() => void update(it)} disabled={busy !== null}>
                {busy === it.key ? "更新中…" : it.kind === "expert" ? "去专家市场" : "更新"}
              </button>
            </li>
          ))}
          {withdrawn.map((it) => (
            <li key={`w-${it.key}`} className="shared-strip__withdrawn">
              <span className="shared-strip__kind">{KIND[it.kind]}</span>
              <span className="shared-strip__title">{it.title}</span>
              <span className="shared-strip__meta">
                {it.status === "gone" ? "已从中央删掉" : it.status === "rejected" ? "审核没通过" : `已被撤回${who(it.withdrawn_by)}`}
                {it.note ? `: ${it.note}` : ""}
                {" · "}
                {it.kind === "wiki" ? "本机那份留着, 已标「已撤回」, 搜索默认不列" : "本机已停用"}
                {it.kind !== "wiki" && it.latest_version ? `; 可以换用 v${it.latest_version}` : ""}
              </span>
              {it.kind === "skill" && it.latest_version && (
                <button type="button" className="chat-composer__primary-button" onClick={() => void update(it)} disabled={busy !== null}>
                  {busy === it.key ? "更新中…" : `换成 v${it.latest_version}`}
                </button>
              )}
            </li>
          ))}
          {hidden.map((it) => (
            <li key={`h-${it.key}`}>
              <span className="shared-strip__kind">{KIND[it.kind]}</span>
              <span className="shared-strip__title">{it.title}</span>
              <span className="shared-strip__meta">
                {it.status === "gone" ? "中央已经没有这一页了" : "你现在看不到它了 (可能换了部门, 或作者收回了分享范围); 本机那份没动"}
              </span>
            </li>
          ))}
        </ul>
      )}
      {(msg || (res.errors?.length ?? 0) > 0) && (
        <div className="shared-strip__note">{msg ?? `上次没查全: ${(res.errors ?? []).join("; ")}`}</div>
      )}
    </div>
  );
}
