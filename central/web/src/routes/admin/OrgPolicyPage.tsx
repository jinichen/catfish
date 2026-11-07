/** /admin/policy — 协作设置 (11/6, docs/GROUP-CHAT-PLAN.md §4.2), admin+.
 *
 * 群聊记录存在每个员工自己的电脑上 (中央不存); 这里定**最多留几天**, 员工 Companion 每次同步时取这个数,
 * 到期自动从本机删。群主 / 员工个人只能在这个上限之内再设短, 不能更长。
 * 11/07: 「本单位邮箱域名」—— 员工电脑上钓鱼识别判「本单位发来的」以它为准 (换个单位装, 由那边管理员填自己的域名)。
 */
import { useEffect, useState } from "react";

import { Card } from "../../components/Card";
import { PageShell } from "../../components/PageShell";
import { RoleGate } from "../../components/RoleGate";
import { RETENTION_DEFAULT, RETENTION_MAX, RETENTION_MIN, orgPolicyApi, splitDomains, type OrgPolicy } from "../../lib/org_policy";

const PRESETS = [30, 90, 180, 365, 730];

export function OrgPolicyPage() {
  return (
    <RoleGate require={["admin"]}>
      <PolicyBody />
    </RoleGate>
  );
}

function PolicyBody() {
  const [p, setP] = useState<OrgPolicy | null>(null);
  const [days, setDays] = useState<string>("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    orgPolicyApi.get().then((r) => { setP(r); setDays(r.room_retention_days ? String(r.room_retention_days) : ""); })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  const n = days.trim() ? Number(days) : null;
  const bad = n !== null && (!Number.isInteger(n) || n < RETENTION_MIN || n > RETENTION_MAX);

  async function save(v: number | null) {
    setBusy(true); setErr(null); setMsg(null);
    try {
      const r = await orgPolicyApi.setRoomRetention(v);
      setP(r);
      setDays(r.room_retention_days ? String(r.room_retention_days) : "");
      setMsg(v === null ? `已清空, 员工电脑按默认 ${RETENTION_DEFAULT} 天` : `已保存: 群聊记录最多保留 ${v} 天`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <PageShell>
      <Card title="群聊记录保留天数">
        <p style={{ margin: "0 0 12px", color: "var(--text-muted)" }}>
          同事群聊的记录只存在每个成员自己的电脑上, 中央不存。这里定员工电脑上<strong>最多留几天</strong>,
          到期自动删掉; 员工的鲶鱼 Companion 下次同步时生效。群主或员工自己可以再设短, 不能比这里长。
        </p>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <input type="number" min={RETENTION_MIN} max={RETENTION_MAX} value={days} placeholder={`默认 ${RETENTION_DEFAULT}`}
                 onChange={(e) => setDays(e.target.value)} style={{ width: 120, padding: "6px 8px" }} aria-label="保留天数" />
          <span>天</span>
          {PRESETS.map((d) => (
            <button key={d} type="button" onClick={() => setDays(String(d))} style={{ padding: "4px 10px" }}>{d} 天</button>
          ))}
        </div>
        {bad && <p style={{ color: "var(--status-err)" }}>天数要在 {RETENTION_MIN} 到 {RETENTION_MAX} 之间的整数</p>}
        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          <button type="button" disabled={busy || bad || n === (p?.room_retention_days ?? null)} onClick={() => void save(n)}>保存</button>
          {p?.room_retention_days != null && (
            <button type="button" disabled={busy} onClick={() => void save(null)}>清空 (用默认 {RETENTION_DEFAULT} 天)</button>
          )}
        </div>
        <p style={{ marginTop: 12, color: "var(--text-muted)", fontSize: 13 }}>
          现在: {p == null ? "读取中…" : p.room_retention_days == null ? `没设, 员工电脑按默认 ${RETENTION_DEFAULT} 天`
            : `最多 ${p.room_retention_days} 天${p.updated_by ? ` (${p.updated_by} 设于 ${p.updated_at?.slice(0, 16).replace("T", " ")})` : ""}`}
        </p>
        {msg && <p style={{ color: "var(--status-ok)" }}>{msg}</p>}
        {err && <p style={{ color: "var(--status-err)" }}>{err}</p>}
      </Card>
      <MailDomainsCard p={p} onSaved={setP} />
    </PageShell>
  );
}

/** 本单位邮箱域名 (11/07): 员工电脑上钓鱼识别判「是不是本单位发来的」以它为准 */
function MailDomainsCard({ p, onSaved }: { p: OrgPolicy | null; onSaved: (p: OrgPolicy) => void }) {
  const [text, setText] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (p) setText(p.org_mail_domains.join("\n")); }, [p]);
  const list = splitDomains(text);
  const same = !!p && list.join(",") === p.org_mail_domains.join(",");

  async function save(domains: string[]) {
    setBusy(true); setErr(null); setMsg(null);
    try {
      const r = await orgPolicyApi.setMailDomains(domains);
      onSaved(r);
      setMsg(domains.length ? `已保存 ${r.org_mail_domains.length} 个域名, 员工电脑一小时内生效` : "已清空, 员工电脑按各自的工作邮箱算");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="本单位邮箱域名">
      <p style={{ margin: "0 0 12px", color: "var(--text-muted)" }}>
        员工电脑上的钓鱼识别靠它判断一封信<strong>是不是本单位发来的</strong>: 比如显示名写「信息安全中心」、
        但地址不在这些域名里, 就提醒员工小心冒充。把本单位和下属单位用的邮箱域名都填上, 一行一个 (或用逗号隔开)。
        不填的话, 每台电脑按员工自己配的工作邮箱算 (下属单位、合作方的域名就认不出)。
        gmail.com、qq.com、163.com 这类公共邮箱不能填 —— 谁都能注册。
      </p>
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4} placeholder={"chinatelecom.cn\nffcs.cn"}
                style={{ width: "100%", maxWidth: 420, padding: "6px 8px", fontFamily: "inherit" }} aria-label="本单位邮箱域名" />
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <button type="button" disabled={busy || same} onClick={() => void save(list)}>保存</button>
        {!!p?.org_mail_domains.length && <button type="button" disabled={busy} onClick={() => void save([])}>清空</button>}
      </div>
      <p style={{ marginTop: 12, color: "var(--text-muted)", fontSize: 13 }}>
        现在: {p == null ? "读取中…" : p.org_mail_domains.length ? p.org_mail_domains.join("、") : "没填, 每台电脑按员工自己的工作邮箱算"}
      </p>
      {msg && <p style={{ color: "var(--status-ok)" }}>{msg}</p>}
      {err && <p style={{ color: "var(--status-err)" }}>{err}</p>}
    </Card>
  );
}
