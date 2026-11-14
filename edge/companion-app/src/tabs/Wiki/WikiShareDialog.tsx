/** 分享到部门知识库的对话框 (P3.3.18 6/10; 11/09 改成先看清楚再发)。
 *
 * # 11/09 为什么改
 *
 * 鸿波看「市场部」这页: 正文就一句话, 依据在来源 `raw/sources/…福富组织架构` 里, 关系指向「中电福富」等别的条目。
 * 原来点「确认分享」只发这一个 md —— 同事那边来源是一个点不开的本机路径, 关系是点不开的名字 (或者按名字
 * 连到他自己的同名条目上)。而且员工发之前看不到会发出去什么, 警告也是发一次被拦一次才出来。
 *
 * 现在打开就做一次预检 (tool-bridge wiki_share.plan, 只读):
 *   · 会发出去什么: 这一页改写后的样子 (本机路径 / 对话编号换成人话), 可以展开看全文
 *   · 来源: 原件能不能一起分享 (作为「原件 · 名称」一页, 这一页里的来源变成能点的链接)
 *   · 关系: 同事已经能看到 / 本机有、可以一起分享 / 本机也没有 / 客户档案不能分享
 *   · 这次要发的几页的扫描结果一次列全: 含凭据的不能发, 其余警告看过勾一下
 * 顺序和参数在 lib/wikiShare.ts (纯函数)。
 *
 * # 守的东西 (没变, 别顺手简化掉)
 *
 * 分享是**不可逆**的 —— 别人装走的副本撤不回来 (manifesto 公理 4), 所以「我知道」那一勾必须员工自己勾;
 * 发到哪个部门明明白白摆出来 (公理 3, 不静默自决); 警告只认员工在这里点的确认 (tools/dispatch 带 _from_ui)。
 * 默认什么都不一起发: 原件和关联页都要员工自己勾 —— 多发出去的内容同样撤不回。
 *
 * # 11/09 本部门 / 全公司
 *
 * 鸿波:「WIKI 也应该支持部门 / 公司分享才合理吧」。顶上选范围, 默认上次的 (没发过 = 本部门):
 *   · 本部门: 跟原来一样, 同部门马上可见 (带待审), 部门主管审
 *   · 全公司: 本部门马上可见, 同时申请全公司可见, 管理员审过后别的部门才看得到 (知识页常带经营数字)
 * 「同事那边点不点得开」按选的范围重算 (lib/wikiShare.ts scopePlan); 原件和一起分享的关联页跟这一页同一个范围。
 */
import { useEffect, useMemo, useState } from "react";

import { fetchMe } from "../../lib/me";
import { fileManagerName } from "../../lib/platformLabels";
import {
  toolBridgeCallTool,
  wikiSensitiveTermsEnsure,
  wikiSharePlan,
  type WikiFileFull,
  type WikiSharePlan,
  type WikiShareRelation,
  type WikiShareScope,
} from "../../lib/tauri";
import { attachedKeys, scopePlan, shareIssues, shareSteps, sizeLabel, warningLabel, type ShareChoice } from "../../lib/wikiShare";
import WikiSharePreview, { reachableNames } from "./WikiSharePreview";
import WikiHistory from "./WikiHistory";

type Result = { label: string; ok: boolean; text: string };

const REL_STATUS: Record<WikiShareRelation["status"], string> = {
  shared: "同事已经能看到",
  local: "同事还看不到",
  missing: "你本机也没有这一页",
  private: "客户档案不能分享",
  dept_only: "只在别人的部门里, 其他部门点不开",
};

export default function WikiShareDialog({
  selectedFile,
  onClose,
  onShared,
}: {
  selectedFile: WikiFileFull;
  onClose: () => void;
  /** 发完了 (至少一页成功) —— 让外面刷新知识库列表 */
  onShared?: () => void;
}) {
  const relPath = selectedFile.info.rel_path;
  // 9/30: 分享目标 = 身份里的部门 (hub 也按这个定)。undefined = 还在查; "" = 账号没设部门
  const [dept, setDept] = useState<string | undefined>(undefined);
  const [deptError, setDeptError] = useState<string | null>(null);
  const [rawPlan, setPlan] = useState<WikiSharePlan | null>(null);
  // 11/09 范围: null = 还没定 (等预检拿到上次的范围)
  const [scopeChoice, setScope] = useState<WikiShareScope | null>(null);
  const scope: WikiShareScope = scopeChoice ?? rawPlan?.scope_before ?? "department";
  const plan = useMemo(() => (rawPlan ? scopePlan(rawPlan, scope) : null), [rawPlan, scope]);
  const company = scope === "company";
  const who = company ? "其他部门同事" : "同事";
  const [planError, setPlanError] = useState<string | null>(null);
  const [attach, setAttach] = useState<Set<string>>(new Set());
  const [together, setTogether] = useState<Set<string>>(new Set());
  const [showPreview, setShowPreview] = useState(false);
  const [ack, setAck] = useState(false);
  const [ackWarnings, setAckWarnings] = useState(false);
  const [sharing, setSharing] = useState(false);
  const [results, setResults] = useState<Result[] | null>(null);
  const [termsHint, setTermsHint] = useState<string | null>(null);

  useEffect(() => {
    fetchMe()
      .then((me) => setDept((me.department || "").trim()))
      .catch((e) => setDeptError(e instanceof Error ? e.message : String(e)));
  }, []);

  // 勾 / 取消原件时重算预览: 这一页里的来源要在「人话」和「[[原件 · 名称]] 链接」之间切换
  const attachKey = [...attach].sort().join("\n");
  useEffect(() => {
    let cancelled = false;
    wikiSharePlan(relPath, attachKey ? attachKey.split("\n") : [])
      .then((p) => {
        if (cancelled) return;
        if (!p.ok) setPlanError(p.error ?? "预检失败");
        else {
          setPlan(p);
          setPlanError(null);
        }
      })
      .catch((e) => {
        if (!cancelled) setPlanError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [relPath, attachKey]);

  const choice: ShareChoice = useMemo(() => ({ attach, together }), [attach, together]);
  const steps = useMemo(() => (plan ? shareSteps(plan, choice, ackWarnings, scope) : []), [plan, choice, ackWarnings, scope]);
  const issues = useMemo(() => shareIssues(steps), [steps]);
  const reachable = useMemo(
    () => (plan ? reachableNames(plan, attachedKeys(plan, choice), together) : new Set<string>()),
    [plan, choice, together],
  );
  // 个人信息 / 内网地址 / 你设的敏感词: 看过才能发; 含凭据的那一页直接不能发
  const canSend =
    !!plan && !!dept && ack && !sharing && issues.blocked.length === 0 && (issues.warned.length === 0 || ackWarnings);

  // 范围变了, 看到的人不一样了: 「我知道」和警告确认都要重新勾
  const pickScope = (next: WikiShareScope) => {
    setScope(next);
    setAck(false);
    setAckWarnings(false);
  };

  const toggle = (set: Set<string>, key: string, on: boolean) => {
    const next = new Set(set);
    if (on) next.add(key);
    else next.delete(key);
    return next;
  };

  const send = async () => {
    setSharing(true);
    const out: Result[] = [];
    for (const step of steps) {
      try {
        const res = await toolBridgeCallTool("catfish_wiki_publish", step.args);
        const r = (res.result ?? {}) as { ok?: boolean; error?: string; republished?: boolean };
        if (res.ok && r.ok)
          out.push({
            label: step.label,
            ok: true,
            text: (r.republished ? "已更新" : "已分享") + (company ? ", 本部门已能看到, 全公司等管理员审" : ""),
          });
        else out.push({ label: step.label, ok: false, text: r.error || res.error || "没发出去" });
      } catch (e) {
        out.push({ label: step.label, ok: false, text: e instanceof Error ? e.message : String(e) });
      }
      setResults([...out]);
    }
    setSharing(false);
    if (out.some((r) => r.ok)) onShared?.();
  };

  const ensureTerms = async () => {
    try {
      const r = await wikiSensitiveTermsEnsure();
      setTermsHint(`${r.created ? "已创建模板" : "已存在"}: ${r.path}。用 ${fileManagerName()} 打开编辑${r.created ? ", 删掉 # 启用对应行" : ""}。`);
    } catch (e) {
      setTermsHint(`创建失败: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const done = !!results && !sharing;
  const rawSources = plan?.sources.filter((s) => s.kind === "raw") ?? [];
  const otherSources = plan?.sources.filter((s) => s.kind !== "raw" && s.kind !== "manual") ?? [];
  const sourceName = (title?: string) => (title ?? "").replace(/^原件 · /, "");

  return (
    <div className="wiki-share__backdrop" onClick={() => !sharing && onClose()}>
      <div className="wiki-share" role="dialog" aria-label="分享到知识库" onClick={(e) => e.stopPropagation()}>
        <h3 className="wiki-share__title">分享到知识库</h3>

        <div className="wiki-share__dept">
          {deptError ? (
            <span className="wiki-share__err">查不到你的部门: {deptError}</span>
          ) : dept === undefined ? (
            <span className="wiki-share__muted">正在查你所在的部门…</span>
          ) : dept === "" ? (
            <span className="wiki-share__err">你的账号没有设置部门, 不能分享 (知识库按部门存放)。请联系管理员在用户管理里补上部门。</span>
          ) : (
            <div className="wiki-share__scope" role="radiogroup" aria-label="分享范围">
              <label className={`wiki-share__scope-opt${!company ? " is-on" : ""}`}>
                <input type="radio" name="wiki-share-scope" checked={!company} disabled={sharing || done || !rawPlan}
                  onChange={() => pickScope("department")} />
                <span>
                  <strong>本部门</strong> · {dept}
                  <span className="wiki-share__muted wiki-share__scope-sub">{dept}同事马上能看到, 部门主管审</span>
                </span>
              </label>
              <label className={`wiki-share__scope-opt${company ? " is-on" : ""}`}>
                <input type="radio" name="wiki-share-scope" checked={company} disabled={sharing || done || !rawPlan}
                  onChange={() => pickScope("company")} />
                <span>
                  <strong>全公司</strong>
                  <span className="wiki-share__muted wiki-share__scope-sub">{dept}马上能看到; 管理员审过后全公司都能看到</span>
                </span>
              </label>
            </div>
          )}
          {rawPlan?.hub_status?.scope === "company" && (
            <div className={rawPlan.hub_status.company_status === "rejected" ? "wiki-share__warn-text" : "wiki-share__muted"}>
              {rawPlan.hub_status.company_status === "approved"
                ? "上次分享的这一页现在全公司都能看到。这次更新后要管理员再审一次, 审过前其他部门暂时看不到这一页 (已经装走的副本还在)。"
                : rawPlan.hub_status.company_status === "pending"
                  ? "上次申请的全公司可见还在等管理员审。"
                  : `上次申请全公司可见没通过${rawPlan.hub_status.company_note ? `: ${rawPlan.hub_status.company_note}` : ""}。`}
              {!company && rawPlan.hub_status.company_status !== "rejected" && (
                <strong> 这次选本部门 = 收回全公司{rawPlan.hub_status.company_status === "approved" ? "可见" : "申请"}, 其他部门就看不到了。</strong>
              )}
            </div>
          )}
        </div>

        {planError && <div className="wiki-share__box wiki-share__box--err">{planError}</div>}
        {!plan && !planError && <div className="wiki-share__muted">正在看这一页会发出去什么…</div>}

        {plan && (
          <>
            <section className="wiki-share__section">
              <div className="wiki-share__label">会发出去</div>
              <div className="wiki-share__row wiki-share__row--static">
                <span className="wiki-share__grow">
                  这一页「{plan.title}」{plan.shared_before && <span className="wiki-share__muted"> (分享过, 这次是更新)</span>}
                </span>
                <button type="button" className="wiki-share__link" onClick={() => setShowPreview((v) => !v)}>
                  {showPreview ? "收起" : "看发出去的全文"}
                </button>
              </div>
              {/* 11/14: 分享过的页 —— 中央留着之前每一版, 改错了能看、能把同事那边恢复回去 */}
              {plan.hub_ref && <WikiHistory namespace={plan.hub_ref.namespace} fileId={plan.hub_ref.file_id} canRestore />}
              {showPreview && <WikiSharePreview plan={plan} reachable={reachable} />}
              {!showPreview && (plan.preview.fields?.evidence.length ?? 0) > 0 && (
                <div className="wiki-share__warn-text">
                  这一页带 {plan.preview.fields!.evidence.length} 条原文依据 (从原件里摘的原话), 会一起发出去。点「看发出去的全文」核对。
                </div>
              )}
            </section>

            {(rawSources.length > 0 || otherSources.length > 0) && (
              <section className="wiki-share__section">
                <div className="wiki-share__label">来源</div>
                {rawSources.map((s) => {
                  const pickable = !!s.attachable && !s.shared;
                  return (
                    <label key={s.key} className={`wiki-share__row${pickable ? "" : " wiki-share__row--static"}`}>
                      {pickable ? (
                        <input
                          type="checkbox"
                          checked={attach.has(s.key!)}
                          disabled={sharing || done}
                          onChange={(e) => setAttach((a) => toggle(a, s.key!, e.target.checked))}
                        />
                      ) : (
                        <span className="wiki-share__dot" aria-hidden="true" />
                      )}
                      <span className="wiki-share__grow">
                        {s.shared ? (
                          <>
                            原件「{sourceName(s.title)}」
                            <span className="wiki-share__ok"> · {company ? "全公司" : "部门里"}已经有, 这一页会链接过去</span>
                          </>
                        ) : pickable ? (
                          <>
                            把原件「{sourceName(s.title)}」也分享
                            <span className="wiki-share__muted"> ({sizeLabel(s.size_bytes)}, 作为单独一页)</span>
                            <div className="wiki-share__muted">
                              {attach.has(s.key!)
                                ? "这一页里的来源会变成能点开的链接。"
                                : `不勾: ${who}那边显示「原件在发布人那里」, 看不到内容。`}
                            </div>
                          </>
                        ) : (
                          <>
                            {s.label} <span className="wiki-share__muted">({s.reason})</span>
                          </>
                        )}
                      </span>
                    </label>
                  );
                })}
                {otherSources.map((s) => (
                  <div key={s.ref} className="wiki-share__row wiki-share__row--static">
                    <span className="wiki-share__dot" aria-hidden="true" />
                    <span className="wiki-share__grow">
                      {s.kind === "conversation" ? "你的对话记录不会分享出去" : s.ref}
                      <span className="wiki-share__muted"> · 同事那边显示「{s.label}」</span>
                    </span>
                  </div>
                ))}
              </section>
            )}

            {plan.relations.length > 0 && (
              <section className="wiki-share__section">
                <div className="wiki-share__label">
                  关系 <span className="wiki-share__muted">· {who}那边只有他能看到的页才点得开</span>
                </div>
                {plan.relations.map((r) => {
                  const pickable = r.status === "local" && !!r.rel_path && !r.scan?.blocked;
                  const picked = !!r.rel_path && together.has(r.rel_path);
                  return (
                    <label key={r.name} className={`wiki-share__row${pickable ? "" : " wiki-share__row--static"}`}>
                      {pickable ? (
                        <input
                          type="checkbox"
                          checked={picked}
                          disabled={sharing || done}
                          onChange={(e) => setTogether((t) => toggle(t, r.rel_path!, e.target.checked))}
                        />
                      ) : (
                        <span className="wiki-share__dot" aria-hidden="true" />
                      )}
                      <span className="wiki-share__grow">
                        {r.rel ? <span className="wiki-share__muted">{r.rel} · </span> : null}
                        {r.title ?? r.name}
                        <span className={`wiki-share__status wiki-share__status--${r.status}`}>
                          {r.status === "local" && picked
                            ? "一起分享"
                            : company && r.status === "shared"
                              ? "全公司已经能看到"
                              : company && r.status === "local" && r.mine
                                ? "只在你部门里, 可以一起分享到全公司"
                                : REL_STATUS[r.status]}
                        </span>
                        {r.scan?.blocked && <div className="wiki-share__err">{r.scan.blocked}</div>}
                      </span>
                    </label>
                  );
                })}
                {!plan.hub_known && (
                  <div className="wiki-share__muted">没连上中央知识库, 「同事能不能看到」只按你自己分享过的算。</div>
                )}
              </section>
            )}

            {(issues.blocked.length > 0 || issues.warned.length > 0) && (
              <section className="wiki-share__box wiki-share__box--warn">
                {issues.blocked.map((b) => (
                  <div key={b.label}>
                    <strong>「{b.label}」不能发</strong>: {b.reason}
                  </div>
                ))}
                {issues.warned.map((w) => (
                  <div key={w.label}>
                    「{w.label}」含 {w.warnings.map(warningLabel).join("、")}
                  </div>
                ))}
                {issues.warned.length > 0 && issues.blocked.length === 0 && (
                  <label className="wiki-share__check">
                    <input
                      type="checkbox"
                      checked={ackWarnings}
                      disabled={sharing || done}
                      onChange={(e) => setAckWarnings(e.target.checked)}
                    />
                    <span>我看过这些内容, 确认要分享给{company ? "全公司" : "部门"} (不会自动脱敏)</span>
                  </label>
                )}
              </section>
            )}
          </>
        )}

        <div className="wiki-share__muted wiki-share__terms">
          想让发布前扫到客户名 / 项目代号? 配 <code>~/.catfish/wiki/sensitive_terms.txt</code>{" "}
          <button type="button" className="wiki-share__link" onClick={() => void ensureTerms()} disabled={sharing}>
            创建模板
          </button>
          {termsHint && <div>{termsHint}</div>}
        </div>

        <label className="wiki-share__check wiki-share__check--strong">
          <input type="checkbox" checked={ack} disabled={sharing || done} onChange={(e) => setAck(e.target.checked)} />
          <span>
            <strong>我知道</strong>分享后{company ? "本部门同事 (管理员审过后全公司同事)" : "部门同事"}能看到、能装到自己电脑上;
            以后撤回只清中央那份, <strong>已经装走的撤不回</strong>。
          </span>
        </label>

        {results && (
          <div className="wiki-share__results">
            {results.map((r) => (
              <div key={r.label} className={r.ok ? "wiki-share__ok" : "wiki-share__err"}>
                {r.ok ? "✓" : "✗"} 「{r.label}」{r.text}
              </div>
            ))}
          </div>
        )}

        <div className="wiki-share__actions">
          <button type="button" className="approval-banner__btn-link" onClick={onClose} disabled={sharing}>
            {done ? "关闭" : "取消"}
          </button>
          {!done && (
            <button type="button" className="approval-banner__btn-primary" onClick={() => void send()} disabled={!canSend}>
              {sharing ? "分享中…" : steps.length > 1 ? `分享 ${steps.length} 页` : "确认分享"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
