/** BL-CATFISH-WIKI-MODE P3.3.4 (6/4) — markdown preview + wikilink clickable.
 *
 * react-markdown + remark-gfm render body, [[wikilink]] regex 转 clickable button →
 *   点击 → find target file (按 slug / title match) → store.selectFile.
 * 顶部 frontmatter metadata 块 (title / type / tags / related / sources / mtime).
 *
 * E5 (6/6 taste-skill 改造): 走 className `.wiki-preview*` / `.wiki-related*`.
 * 7 类 anti-pattern 修法见 globals.css 顶 `.wiki-preview` block 注释.
 */

import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useWikiStore } from "../../store/wiki";
import { topKRelated } from "../../lib/wikiRelevance";
import {
  wikiDeleteFile,
  wikiUpdateFile,
  wikiCreateEntityOrConcept, // P3.5.114: dangling click → 真自动建真文件
  toolBridgeCallTool,
  wikiUninstallShared,
  wikiSensitiveTermsEnsure,
} from "../../lib/tauri";
// P3.3.18 Phase 4 P2 (6/10): 检 hub 是否 stale
import { config } from "../../lib/env";
import { fetchWithAuth } from "../../lib/me";

const KIND_LABEL: Record<string, string> = {
  entity: "实体",
  concept: "概念",
  query: "查询",
};

export default function WikiPreview() {
  const selectedFile = useWikiStore((s) => s.selectedFile);
  const selectedLoading = useWikiStore((s) => s.selectedLoading);
  const selectedError = useWikiStore((s) => s.selectedError);
  const files = useWikiStore((s) => s.files);
  const selectFile = useWikiStore((s) => s.selectFile);
  const loadFiles = useWikiStore((s) => s.loadFiles);
  // P3.5.111/114 (6/25 鸿波 catch "应该形成真文件才合理"): dangling wikilink click →
  // 真**自动建真文件**, 失败 fallback setVirtualSystem 虚拟态.
  const setVirtualSystem = useWikiStore((s) => s.setVirtualSystem);

  // P35 (6/5 鸿波): inline 编辑器 state. editing=true 时 body 渲染 textarea.
  const [editing, setEditing] = useState(false);
  const [draftBody, setDraftBody] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  // P3.3.4 (6/9 鸿波): 软删 state — confirmDelete=true 第一次点变红色 "确认删除",
  // 5 秒不点再变回 "删除" (跟 SkillEntry undo toast 风格一致).
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteErr, setDeleteErr] = useState<string | null>(null);

  // P3.3.18 (6/10): 分享到部门 dialog state. 走 catfish_wiki_publish tool.
  // 强警告员工 "已 pull 副本撤不回" (manifesto 公理 4). 第一次失败若 warnings
  // (PII / 内网 / 敏感词), 显警告 + ack checkbox + 再 retry with acknowledge_warnings=true.
  const [shareDialogOpen, setShareDialogOpen] = useState(false);
  const [shareNamespace, setShareNamespace] = useState("");
  const [shareAck, setShareAck] = useState(false);
  const [sharing, setSharing] = useState(false);
  const [shareWarnings, setShareWarnings] = useState<Array<{ category: string; hits: any[]; advice: string }>>([]);
  const [shareError, setShareError] = useState<string | null>(null);
  const [shareSuccess, setShareSuccess] = useState<string | null>(null);

  // P3.3.18 Phase 4 P2 (6/10): 卸载本机部门 wiki 副本 state. 5 秒 confirm 同款.
  const [confirmUninstall, setConfirmUninstall] = useState(false);
  const [uninstalling, setUninstalling] = useState(false);
  const [uninstallErr, setUninstallErr] = useState<string | null>(null);

  // P3.3.18 Phase 4 P2 (6/10): hub stale check for 部门 wiki. 选了 wiki-shared/
  // 文件时, 后台拉 /v1/wiki/documents/<ns>/<file_id>, 看 stale_after_unpublish.
  // 不写本机 .stale sidecar (manifesto 公理 4 — 中央不直接动员工本机文件).
  // 只在 UI 显警告 banner.
  const [hubStaleInfo, setHubStaleInfo] = useState<{
    stale: boolean;
    unpublished_at: string | null;
    unpublished_reason: string | null;
  } | null>(null);
  useEffect(() => {
    setHubStaleInfo(null);
    if (!selectedFile) return;
    const rp = selectedFile.info.rel_path;
    if (!rp.startsWith("wiki-shared/dept/")) return;
    // parse "wiki-shared/dept/<部门>/<file_id>.md" → ns=dept/<部门>, file_id=<stem>
    const m = rp.match(/^wiki-shared\/(dept\/[^/]+)\/([^/]+)\.md$/);
    if (!m) return;
    const ns = m[1];
    const fileId = m[2];
    let cancelled = false;
    void (async () => {
      try {
        const url = `${config.backendUrl}/v1/wiki/documents/${encodeURIComponent(ns)}/${encodeURIComponent(fileId)}`;
        const res = await fetchWithAuth(url);
        if (!res.ok || cancelled) return;
        const data = await res.json();
        if (cancelled) return;
        setHubStaleInfo({
          stale: data?.stale_after_unpublish === true,
          unpublished_at: data?.unpublished_at ?? null,
          unpublished_reason: data?.unpublished_reason ?? null,
        });
      } catch (e) {
        // 不阻塞 — 网络挂时不影响员工看本机副本
        console.warn("[WikiPreview] hub stale check 失败:", e);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedFile?.info.rel_path]);
  useEffect(() => {
    if (!confirmUninstall) return;
    const t = setTimeout(() => setConfirmUninstall(false), 5000);
    return () => clearTimeout(t);
  }, [confirmUninstall]);

  const handleUninstallClick = async () => {
    if (!selectedFile) return;
    if (!confirmUninstall) {
      setConfirmUninstall(true);
      return;
    }
    setUninstalling(true);
    setUninstallErr(null);
    try {
      await wikiUninstallShared(selectedFile.info.rel_path);
      await selectFile(null);
      await loadFiles();
    } catch (e) {
      setUninstallErr(String(e));
      setConfirmUninstall(false);
    } finally {
      setUninstalling(false);
    }
  };

  // selectedFile 切换时 reset edit state
  useEffect(() => {
    setEditing(false);
    setSaveErr(null);
    setConfirmDelete(false);
    setDeleteErr(null);
    if (selectedFile) {
      setDraftBody(selectedFile.body);
    }
  }, [selectedFile?.info.rel_path]);

  // P3.3.4: confirmDelete 5 秒自动 reset (防员工悬停太久误点)
  useEffect(() => {
    if (!confirmDelete) return;
    const t = setTimeout(() => setConfirmDelete(false), 5000);
    return () => clearTimeout(t);
  }, [confirmDelete]);

  const startEdit = () => {
    if (!selectedFile) return;
    setDraftBody(selectedFile.body);
    setEditing(true);
    setSaveErr(null);
  };

  const cancelEdit = () => {
    setEditing(false);
    setSaveErr(null);
  };

  // P3.3.4 (6/9): 软删流程 — 第一次点变 confirm, 第二次点真删 (5 秒 timeout reset)
  // P3.5.132 #3: 第 1 次 click 真 dryRun 看 affected, UI 显「N 文件引用」,
  // 第 2 次 click 真删. confirmDelete=true 期间不再 dryRun, 直接删.
  const [affectedFiles, setAffectedFiles] = useState<
    { rel_path: string; title: string }[]
  >([]);
  const handleDeleteClick = async () => {
    if (!selectedFile) return;
    if (!confirmDelete) {
      // 第 1 次: dry_run 真扫 affected
      setDeleting(true);
      setDeleteErr(null);
      try {
        const r = await wikiDeleteFile(selectedFile.info.rel_path, true);
        setAffectedFiles(r.affected_files);
        setConfirmDelete(true);
      } catch (e) {
        setDeleteErr(String(e));
      } finally {
        setDeleting(false);
      }
      return;
    }
    // 第 2 次: 真删
    setDeleting(true);
    setDeleteErr(null);
    try {
      await wikiDeleteFile(selectedFile.info.rel_path, false);
      // 删完清 selection, 刷新 list
      setAffectedFiles([]);
      await selectFile(null);
      await loadFiles();
    } catch (e) {
      setDeleteErr(String(e));
      setConfirmDelete(false);
      setAffectedFiles([]);
    } finally {
      setDeleting(false);
    }
  };

  const saveEdit = async () => {
    if (!selectedFile) return;
    setSaving(true);
    setSaveErr(null);
    try {
      // 拼 full content: --- frontmatter --- + body
      const fm = selectedFile.frontmatter.trim();
      const content = `---\n${fm}\n---\n\n${draftBody}\n`;
      await wikiUpdateFile(selectedFile.info.rel_path, content);
      await loadFiles();
      await selectFile(selectedFile.info.rel_path); // 重 fetch 看新 body
      setEditing(false);
    } catch (e) {
      setSaveErr(String(e));
    } finally {
      setSaving(false);
    }
  };

  // P3.3.18 share dialog — selectedFile 变时 reset state
  useEffect(() => {
    setShareDialogOpen(false);
    setShareAck(false);
    setShareWarnings([]);
    setShareError(null);
    setShareSuccess(null);
  }, [selectedFile?.info.rel_path]);

  // P3.3.18 Phase 4 P2 (6/10): 敏感词文件 onboarding state
  const [sensitiveTermsHint, setSensitiveTermsHint] = useState<string | null>(null);

  const openShareDialog = () => {
    if (!selectedFile) return;
    setShareDialogOpen(true);
    setShareAck(false);
    setShareWarnings([]);
    setShareError(null);
    setShareSuccess(null);
    setSensitiveTermsHint(null);
    // 不预填 namespace, 让员工 explicit 填 — manifesto 公理 3 (不静默自决)
  };

  const handleEnsureSensitiveTerms = async () => {
    try {
      const result = await wikiSensitiveTermsEnsure();
      if (result.created) {
        setSensitiveTermsHint(`✓ 已创建模板: ${result.path}. 用 Finder 打开编辑, 删 # 启用对应行.`);
      } else {
        setSensitiveTermsHint(`✓ 已存在: ${result.path}. 用 Finder 打开编辑.`);
      }
    } catch (e) {
      setSensitiveTermsHint(`✗ 创建失败: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const handleShare = async (acknowledgeWarnings: boolean) => {
    if (!selectedFile) return;
    const ns = shareNamespace.trim();
    if (!ns) {
      setShareError("namespace 必填 (例 dept/finance)");
      return;
    }
    if (!/^dept\/[a-z][a-z0-9_-]{0,40}$/.test(ns)) {
      setShareError("namespace 格式必须 'dept/<部门>' (小写字母数字 / - / _)");
      return;
    }
    if (!shareAck) {
      setShareError("必须勾选 '我知道已 pull 副本撤不回' 才能继续");
      return;
    }
    setSharing(true);
    setShareError(null);
    try {
      const res = await toolBridgeCallTool("catfish_wiki_publish", {
        wiki_rel_path: selectedFile.info.rel_path,
        namespace: ns,
        acknowledge_warnings: acknowledgeWarnings,
      });
      const result: any = res.result;
      if (res.ok && result?.ok) {
        setShareSuccess(`已 publish 到 ${ns} · file_id=${result.file_id}`);
        setShareWarnings([]);
      } else {
        // 看是不是 warnings (默认拒)
        const errResult = result || {};
        if (errResult.scan_phase === "warnings" && errResult.warnings?.length) {
          setShareWarnings(errResult.warnings);
          setShareError(null);
        } else {
          setShareError(
            errResult.error ||
              (typeof result === "string" ? result : JSON.stringify(result || res)),
          );
        }
      }
    } catch (e) {
      setShareError(e instanceof Error ? e.message : String(e));
    } finally {
      setSharing(false);
    }
  };

  // P39 (6/5 鸿波): 变更历史 collapsible — 检测 body 末尾 "## 变更历史" 段 (P19 LLM
  // merge 自动生成 section), 拆 main body + history section. UI 默认 collapsed.
  const { mainBody, historyBody } = useMemo(() => {
    if (!selectedFile) return { mainBody: "", historyBody: "" };
    const body = selectedFile.body;
    // 匹配 "## 变更历史" 或 "## 变更日志" / "## Changelog" — 兼容 LLM 生成的不同标题
    const re = /^(##\s+(?:变更历史|变更日志|更新历史|Changelog|Change Log)\s*)$/im;
    const match = body.match(re);
    if (!match || match.index === undefined) {
      return { mainBody: body, historyBody: "" };
    }
    return {
      mainBody: body.slice(0, match.index).trimEnd(),
      historyBody: body.slice(match.index),
    };
  }, [selectedFile]);

  // wikilink → 替换 custom html marker, react-markdown 透传
  const rendered = useMemo(() => {
    if (!selectedFile) return "";
    // [[name]] → special token <wikilink:name>, ReactMarkdown components.a hook 或 custom regex 处理
    return mainBody.replace(
      /\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]/g,
      (_match, name, alias) => {
        const display = alias || name;
        return `[${display}](catfish-wikilink://${encodeURIComponent(name)})`;
      }
    );
  }, [mainBody, selectedFile]);

  const [showHistory, setShowHistory] = useState(false);

  async function handleWikilinkClick(name: string) {
    // 找 title / slug match file
    const lower = name.toLowerCase().trim();
    const match = files.find(
      (f) =>
        f.title.toLowerCase() === lower ||
        f.slug.toLowerCase() === lower ||
        f.title.toLowerCase().includes(lower)
    );
    if (match) {
      void selectFile(match.rel_path);
      return;
    }
    // P3.5.114 (6/25 鸿波 catch "应该形成真文件才合理"): dangling → 真**自动建真文件**.
    // 默认 concept + subtype=system (顶级体系视觉一致, 后续可手动编辑改子类型).
    try {
      const result = await wikiCreateEntityOrConcept({
        kind: "concept",
        title: name,
        subtype: "system",
        tags: [],
        related: [],
        body: `# ${name}\n\n(由 catfish 自动建立 — 点 dangling wikilink 触发)\n\n本体系下属概念自动反推: WikiGraph 走 children related[0] 指向本体系.`,
      });
      setVirtualSystem(null);
      await loadFiles();
      await selectFile(result.rel_path);
    } catch (err) {
      // fail-safe: 已存在 / 网络失败 → fallback 虚拟态显
      console.warn(`[wiki] auto-create dangling "${name}" failed: ${err} → fallback virtual`);
      setVirtualSystem(name);
    }
  }

  if (selectedLoading) {
    return <div style={{ color: "var(--catfish-text-muted)", padding: 20 }}>加载中…</div>;
  }
  if (selectedError) {
    return (
      <div style={{ color: "var(--status-err)", padding: 20 }}>
        ✗ {selectedError}
      </div>
    );
  }
  if (!selectedFile) {
    return (
      <div className="wiki-preview__empty">
        <div className="wiki-preview__empty-icon">📄</div>
        <div className="wiki-preview__empty-title">左侧选 wiki 文件</div>
        <div>entity / concept / query 都行</div>
      </div>
    );
  }

  const info = selectedFile.info;
  const isDangling = (name: string) => {
    const lower = name.toLowerCase().trim();
    return !files.some(
      (f) =>
        f.title.toLowerCase() === lower ||
        f.slug.toLowerCase() === lower ||
        f.title.toLowerCase().includes(lower)
    );
  };

  const kind = info.kind;
  const kindLabel = KIND_LABEL[kind] || kind;

  return (
    <div className="wiki-preview">
      {/* metadata header — elevation shadow 替 1px border, kind badge 替 emoji */}
      <div className="wiki-preview__meta">
        <div className="wiki-preview__meta-title">
          <span className={`wiki-kind-badge wiki-kind-badge--${kind}`}>{kindLabel}</span>
          {info.title}
        </div>
        <div className="wiki-preview__meta-row">
          <span>类型 {info.subtype || info.kind}</span>
          <span>
            路径 <code>{info.rel_path}</code>
          </span>
          <span>{(info.size_bytes / 1024).toFixed(1)}KB</span>
        </div>
        {info.tags.length > 0 && (
          <div className="wiki-preview__meta-section">
            <span className="wiki-preview__meta-section-label">标签</span>
            {info.tags.map((t) => (
              <span key={t} className="wiki-preview__tag">
                #{t}
              </span>
            ))}
          </div>
        )}
        {info.related.length > 0 && (
          <div className="wiki-preview__meta-section">
            <span className="wiki-preview__meta-section-label">
              相关 ({info.related.length})
            </span>
            {info.related.map((r, i) => {
              // P3.5.132 #5: r 真 RelatedRef, 显 name + rel label
              const dangling = isDangling(r.name);
              const title = r.rel
                ? `${dangling ? "找不到 file (dangling link)" : "跳转到 " + r.name} · 关系: ${r.rel}`
                : (dangling ? "找不到 file (dangling link)" : "跳转到 " + r.name);
              return (
                <button
                  key={i}
                  className={
                    "wiki-preview__wikilink" +
                    (dangling ? " wiki-preview__wikilink--dangling" : "")
                  }
                  onClick={() => handleWikilinkClick(r.name)}
                  title={title}
                >
                  [[{r.name}]]
                  {r.rel && (
                    <span
                      style={{
                        marginLeft: 4,
                        fontSize: 10,
                        opacity: 0.7,
                        color: "var(--catfish-text-muted)",
                      }}
                    >
                      ({r.rel})
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        )}
        {info.sources.length > 0 && (
          <div className="wiki-preview__meta-section">
            <span className="wiki-preview__meta-section-label">来源</span>
            <span style={{ color: "var(--catfish-text-muted)", fontSize: 12 }}>
              {info.sources.join(", ")}
            </span>
          </div>
        )}
      </div>

      {/* P3.2 4 信号 相关推荐 — 渲染 in body 前, 先 build top-K */}
      <RelatedRecommend info={info} />

      {/* P3.3.18 Phase 4 P2 (6/10): 部门 wiki 已被原作者撤回的警告 banner — 选了
        wiki-shared/ 时后台 fetch hub 检查 stale_after_unpublish=true 时显. */}
      {hubStaleInfo?.stale && (
        <div
          style={{
            background: "#fef3c7",
            border: "1px solid #f59e0b",
            color: "#78350f",
            padding: "10px 12px",
            borderRadius: 6,
            fontSize: 13,
            lineHeight: 1.5,
            marginBottom: 10,
          }}
        >
          <strong>⚠ 原作者已撤回这条 wiki</strong>
          {hubStaleInfo.unpublished_at && (
            <span style={{ marginLeft: 6, fontSize: 11, opacity: 0.8 }}>
              ({hubStaleInfo.unpublished_at.slice(0, 10)})
            </span>
          )}
          <div style={{ marginTop: 4 }}>
            中央 hub body 已清零, 但你本机这份副本不动 (manifesto 公理 4 — 中央不强制清你本机).
            自己决定是否点 "🗑 卸载本机副本".
          </div>
          {hubStaleInfo.unpublished_reason && (
            <div style={{ marginTop: 4, fontStyle: "italic" }}>
              原作者撤回原因: {hubStaleInfo.unpublished_reason}
            </div>
          )}
        </div>
      )}

      {/* P35 (6/5): 编辑 toggle + action bar — 复用 banner btn 系列 */}
      {/* P3.3.4 (6/9): action bar 加删除按钮 (mv 到 wiki/.trash/<ts>-原名.md) */}
      {/* P3.3.18 Phase 4 (6/10): 已装部门 wiki (wiki-shared/) read-only, 不显编辑/删除/分享 */}
      <div className="wiki-preview__actions">
        {selectedFile?.info.rel_path.startsWith("wiki-shared/") && (
          <>
            <span
              style={{
                fontSize: 12,
                color: "var(--catfish-text-muted)",
                padding: "4px 10px",
                background: "rgba(124,58,237,0.08)",
                border: "1px solid rgba(124,58,237,0.2)",
                borderRadius: 4,
              }}
              title="来自部门 wiki-hub 的副本, 不能本机改也不能再 share. 原作者撤回时这里会显 stale 标."
            >
              📥 部门 wiki · read-only (来自 hub)
            </span>
            {/* P3.3.18 Phase 4 P2 (6/10): 卸载本机副本 — 5 秒 confirm */}
            <button
              className={
                confirmUninstall
                  ? "approval-banner__btn-deny"
                  : "approval-banner__btn-link"
              }
              onClick={handleUninstallClick}
              disabled={uninstalling}
              title={
                confirmUninstall
                  ? "再点一次确认卸载 (mv 到 wiki-shared/.trash/)"
                  : "卸载本机这份部门 wiki 副本 — 软删, 5 秒内再点确认. 中央 hub 那一份不动."
              }
            >
              {uninstalling
                ? "卸载中…"
                : confirmUninstall
                  ? "确认卸载"
                  : "🗑 卸载本机副本"}
            </button>
          </>
        )}
        {!editing && !selectedFile?.info.rel_path.startsWith("wiki-shared/") && (
          <>
            <button
              className="approval-banner__btn-link"
              onClick={startEdit}
              title="编辑 body markdown"
              disabled={deleting}
            >
              编辑
            </button>
            {/* P3.3.18 (6/10): 分享到部门 — 强警告 dialog 跟 manifesto 公理 4 提醒 */}
            <button
              className="approval-banner__btn-link"
              onClick={openShareDialog}
              disabled={deleting || sharing}
              title="把这条 wiki 发布到部门 wiki-hub — 部门同事能看到, 已 pull 副本撤不回"
            >
              📤 分享到部门
            </button>
            <button
              className={
                confirmDelete
                  ? "approval-banner__btn-deny"
                  : "approval-banner__btn-link"
              }
              onClick={handleDeleteClick}
              disabled={deleting}
              title={
                confirmDelete
                  ? affectedFiles.length > 0
                    ? `再点确认: 删了会让 ${affectedFiles.length} 处变 dangling`
                    : "再点一次确认删除 (mv 到 wiki/.trash/)"
                  : "删除这个 entity/concept/query — 软删, 先扫谁引用我"
              }
            >
              {deleting
                ? "扫描中…"
                : confirmDelete
                  ? affectedFiles.length > 0
                    ? `确认删 (留 ${affectedFiles.length} 处 dangling)`
                    : "确认删除"
                  : "删除"}
            </button>
          </>
        )}
        {editing && (
          <>
            <span className="wiki-preview__edit-hint">
              frontmatter 不动, 只改 body
            </span>
            <button
              className="approval-banner__btn-primary"
              onClick={saveEdit}
              disabled={saving}
            >
              {saving ? "保存中…" : "保存"}
            </button>
            <button
              className="approval-banner__btn-link"
              onClick={cancelEdit}
              disabled={saving}
            >
              取消
            </button>
          </>
        )}
      </div>

      {saveErr && (
        <div className="wiki-preview__save-err">保存失败: {saveErr}</div>
      )}
      {deleteErr && (
        <div className="wiki-preview__save-err">删除失败: {deleteErr}</div>
      )}
      {uninstallErr && (
        <div className="wiki-preview__save-err">卸载失败: {uninstallErr}</div>
      )}

      {/* P3.3.18 (6/10): 分享 dialog */}
      {shareDialogOpen && selectedFile && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.5)",
            zIndex: 1000,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 20,
          }}
          onClick={() => setShareDialogOpen(false)}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: "var(--catfish-bg-elevated)",
              border: "1px solid var(--catfish-border)",
              borderRadius: "var(--radius-md)",
              padding: 24,
              maxWidth: 560,
              width: "100%",
              maxHeight: "90vh",
              overflowY: "auto",
            }}
          >
            <h3 style={{ margin: "0 0 12px", display: "flex", alignItems: "center", gap: 8 }}>
              📤 分享 wiki 到部门
            </h3>
            <div style={{ fontSize: 13, color: "var(--catfish-text-muted)", marginBottom: 16 }}>
              要分享: <code>{selectedFile.info.rel_path}</code>
            </div>

            {/* 强警告 banner — 用户拍要的, manifesto 公理 4 提醒 */}
            <div
              style={{
                background: "#fef3c7",
                border: "1px solid #f59e0b",
                color: "#78350f",
                padding: 12,
                borderRadius: 6,
                fontSize: 13,
                lineHeight: 1.6,
                marginBottom: 16,
              }}
            >
              <strong>⚠️ 重要提醒</strong> (manifesto 公理 4)
              <ul style={{ margin: "6px 0 0", paddingLeft: 20 }}>
                <li>分享后, 部门所有同事能看到 / 装这条 wiki 到本机</li>
                <li>
                  你后面 <strong>哪怕撤回</strong>, 中央那一份会清零 + 标 stale, 但 <strong>已 pull 装本机的副本撤不回</strong>
                </li>
                <li>已扩散的信息在部门里"永久存在", 跟 OneNote / Confluence 一回事</li>
                <li>客户名 / 项目细节 / 关键人名 这种敏感内容, 决定前想清楚</li>
              </ul>
            </div>

            {/* P3.3.18 Phase 4 P2: 敏感词文件 onboarding hint */}
            <div
              style={{
                background: "rgba(74,158,255,0.08)",
                border: "1px solid rgba(74,158,255,0.2)",
                padding: "8px 12px",
                borderRadius: 4,
                fontSize: 12,
                marginBottom: 12,
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 8,
              }}
            >
              <span style={{ flex: 1 }}>
                💡 想扫客户名/项目代号? 配 <code>~/.catfish/wiki/sensitive_terms.txt</code>
              </span>
              <button
                onClick={() => void handleEnsureSensitiveTerms()}
                disabled={sharing}
                style={{
                  fontSize: 11,
                  padding: "3px 10px",
                  background: "transparent",
                  color: "var(--catfish-text)",
                  border: "1px solid var(--catfish-border)",
                  borderRadius: 3,
                  cursor: "pointer",
                  whiteSpace: "nowrap",
                }}
              >
                创建模板
              </button>
            </div>
            {sensitiveTermsHint && (
              <div
                style={{
                  fontSize: 11,
                  marginBottom: 12,
                  color: sensitiveTermsHint.startsWith("✗") ? "#dc2626" : "#16a34a",
                  padding: "4px 8px",
                  background: sensitiveTermsHint.startsWith("✗") ? "#fee2e2" : "#d1fae5",
                  borderRadius: 3,
                }}
              >
                {sensitiveTermsHint}
              </div>
            )}

            <div style={{ marginBottom: 16 }}>
              <label
                style={{
                  display: "block",
                  fontSize: 12,
                  color: "var(--catfish-text-muted)",
                  marginBottom: 6,
                }}
              >
                目标部门 namespace (格式 dept/&lt;部门&gt;)
              </label>
              <input
                type="text"
                value={shareNamespace}
                onChange={(e) => setShareNamespace(e.target.value)}
                placeholder="dept/finance"
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  fontSize: 13,
                  border: "1px solid var(--catfish-border)",
                  borderRadius: 4,
                  background: "var(--catfish-bg)",
                  color: "var(--catfish-text)",
                  fontFamily: "var(--font-mono)",
                }}
                disabled={sharing}
              />
              <div style={{ fontSize: 11, color: "var(--catfish-text-muted)", marginTop: 4 }}>
                例: dept/finance, dept/sales, dept/it. 当前只接受 dept/ 开头.
              </div>
            </div>

            <div style={{ marginBottom: 16 }}>
              <label style={{ fontSize: 13, display: "flex", gap: 8, cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={shareAck}
                  onChange={(e) => setShareAck(e.target.checked)}
                  disabled={sharing}
                />
                <span>
                  <strong>我知道</strong> 已 pull 副本撤不回, 信息会在部门里扩散
                </span>
              </label>
            </div>

            {/* warnings 显 (PII / 内网 / 敏感词命中) */}
            {shareWarnings.length > 0 && (
              <div
                style={{
                  background: "#fee2e2",
                  border: "1px solid #dc2626",
                  color: "#7f1d1d",
                  padding: 12,
                  borderRadius: 6,
                  fontSize: 12,
                  marginBottom: 16,
                }}
              >
                <strong>扫到 {shareWarnings.length} 类内容警告</strong>:
                <ul style={{ margin: "6px 0 0", paddingLeft: 20 }}>
                  {shareWarnings.map((w, i) => (
                    <li key={i}>
                      <strong>{w.category}</strong>: {w.advice} ({w.hits.length} 处)
                    </li>
                  ))}
                </ul>
                <div style={{ marginTop: 8, color: "#7f1d1d" }}>
                  确认要继续 share 这些内容到部门? 点"我看过了, 强发"再发一次.
                </div>
              </div>
            )}

            {shareError && (
              <div
                style={{
                  background: "#fee2e2",
                  border: "1px solid #dc2626",
                  color: "#7f1d1d",
                  padding: 10,
                  borderRadius: 4,
                  fontSize: 12,
                  marginBottom: 16,
                }}
              >
                {shareError}
              </div>
            )}

            {shareSuccess && (
              <div
                style={{
                  background: "#d1fae5",
                  border: "1px solid #16a34a",
                  color: "#14532d",
                  padding: 10,
                  borderRadius: 4,
                  fontSize: 12,
                  marginBottom: 16,
                }}
              >
                ✓ {shareSuccess}
              </div>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button
                className="approval-banner__btn-link"
                onClick={() => setShareDialogOpen(false)}
                disabled={sharing}
              >
                {shareSuccess ? "关闭" : "取消"}
              </button>
              {!shareSuccess && (
                <button
                  className="approval-banner__btn-primary"
                  onClick={() => void handleShare(shareWarnings.length > 0)}
                  disabled={sharing || !shareAck}
                >
                  {sharing
                    ? "分享中…"
                    : shareWarnings.length > 0
                      ? "我看过了, 强发"
                      : "确认分享"}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* P35 (6/5): editing → textarea; 否则 → markdown */}
      {editing ? (
        <textarea
          className="wiki-preview__textarea"
          value={draftBody}
          onChange={(e) => setDraftBody(e.target.value)}
          spellCheck={false}
        />
      ) : (
        /* markdown body */
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children }) => {
              if (href?.startsWith("catfish-wikilink://")) {
                const name = decodeURIComponent(
                  href.slice("catfish-wikilink://".length),
                );
                const dangling = isDangling(name);
                return (
                  <button
                    className={
                      "wiki-preview__wikilink" +
                      (dangling ? " wiki-preview__wikilink--dangling" : "")
                    }
                    onClick={() => handleWikilinkClick(name)}
                    title={dangling ? "dangling link" : "跳转"}
                  >
                    {children}
                  </button>
                );
              }
              return (
                <a href={href} target="_blank" rel="noreferrer">
                  {children}
                </a>
              );
            },
          }}
        >
          {rendered}
        </ReactMarkdown>
      )}

      {/* P39 (6/5 鸿波): 变更历史 collapsible — P19 LLM merge 自动生 ## 变更历史 段.
       *  E5 改造: 走 caret rotate (跟 .wiki-group 一致), 不再 emoji ▶/▼ swap. */}
      {!editing && historyBody && (
        <div
          className={
            "wiki-preview__history" +
            (showHistory ? " wiki-preview__history--open" : "")
          }
        >
          <button
            className="wiki-preview__history-toggle"
            onClick={() => setShowHistory(!showHistory)}
            aria-expanded={showHistory}
          >
            <span className="wiki-preview__history-caret">▶</span>
            变更历史
            <span className="wiki-preview__history-hint">
              (LLM merge 自动记录)
            </span>
          </button>
          {showHistory && (
            <div className="wiki-preview__history-body">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {historyBody}
              </ReactMarkdown>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function RelatedRecommend({
  info,
}: {
  info: import("../../lib/tauri").WikiFileInfo;
}) {
  const files = useWikiStore((s) => s.files);
  const selectFile = useWikiStore((s) => s.selectFile);

  const recommends = useMemo(() => topKRelated(info, files, 5), [info, files]);

  if (recommends.length === 0) return null;

  return (
    <div className="wiki-related">
      <div className="wiki-related__header">相关推荐 · 4 信号 ranked</div>
      <div className="wiki-related__sub">
        direct link (×3) · source overlap (×4) · Adamic-Adar (×1.5) · type affinity (×1)
      </div>
      <ul className="wiki-related__list">
        {recommends.map(({ file, breakdown }) => {
          const signals: string[] = [];
          if (breakdown.direct > 0) signals.push("↔");
          if (breakdown.sourceOverlap > 0) signals.push("◇");
          if (breakdown.adamicAdar > 0) signals.push("∗");
          if (breakdown.typeAffinity > 0) signals.push("≈");
          const fileKind = file.kind;
          const fileKindLabel = KIND_LABEL[fileKind] || fileKind;
          return (
            <li key={file.rel_path} className="wiki-related__item">
              <button
                className="wiki-related__link"
                onClick={() => void selectFile(file.rel_path)}
              >
                <span
                  className={`wiki-kind-badge wiki-kind-badge--${fileKind}`}
                >
                  {fileKindLabel}
                </span>
                {file.title}
              </button>
              <span className="wiki-related__score">
                score {breakdown.total.toFixed(2)} {signals.join(" ")}
                {breakdown.direct > 0 && ` · 直链`}
                {breakdown.sourceOverlap > 0 &&
                  ` · 共源 ${breakdown.sourceOverlap.toFixed(1)}`}
                {breakdown.adamicAdar > 0 &&
                  ` · 共邻 ${breakdown.adamicAdar.toFixed(1)}`}
                {breakdown.typeAffinity > 0 && ` · 同型`}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
