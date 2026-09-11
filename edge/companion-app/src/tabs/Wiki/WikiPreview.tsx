/** BL-CATFISH-WIKI-MODE P3.3.4 (6/4) — markdown preview + wikilink clickable.
 *
 * react-markdown + remark-gfm render body, [[wikilink]] regex 转 clickable button →
 *   点击 → find target file (按 slug / title match) → store.selectFile.
 * 顶部 frontmatter metadata 块 (title / type / tags / related / sources / mtime).
 *
 * E5 (6/6 taste-skill 改造): 走 className `.wiki-preview*`.
 * 7 类 anti-pattern 修法见 globals.css 顶 `.wiki-preview` block 注释.
 */

import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useChatStore } from "../../store/chat";
import { useWikiStore } from "../../store/wiki";
import {
  wikiDeleteFile,
  wikiUpdateFile,
  toolBridgeCallTool,
  wikiUninstallShared,
  wikiSensitiveTermsEnsure,
} from "../../lib/tauri";
// P3.5.172 Phase C (7/3 鸿波): 🔗 扫描关联 AI 建议 modal.
// 严格 audit "组织架构" orphan root cause = 员工上传 md 无 [[wikilink]] → 系统扫
// 不出关系. 加 button + LLM 扫 body + 现有 title list → 建议 → 员工确认 → body
// 末尾加"## 关联概念"段落 (员工主权, 不动老 body).
import WikiLinkSuggestModal from "./WikiLinkSuggestModal";
// P3.3.18 Phase 4 P2 (6/10): 检 hub 是否 stale
import { config } from "../../lib/env";
import { fetchWithAuth } from "../../lib/me";
import WikiShareDialog from "./WikiShareDialog";
import WikiActionPanel from "./WikiActionPanel";
// 8/15: 下面三段是从本文件搬出去的 JSX section, 不是新组件。挑它们是因为
// props 少 (1 / 4 / 5 个)。action bar 那 119 行要 18 个 props, 所以留在原地 ——
// 理由写在 WikiPreviewSections.tsx 的模块 docstring 里。
import {
  WikiHistorySection,
  WikiHubStaleBanner,
  WikiMetaHeader,
} from "./WikiPreviewSections";
import { wikiKindLabel } from "./wikiLabels";
import { isExternalLink } from "../../lib/linkBehavior";
import { wikiMarkdownUrl } from "../../lib/wikiMarkdownUrl";
import { resolveWikiRefOrNull } from "../../lib/wikiResolve";

/** Hide the legacy generated `## Related` block from older wiki documents.
 *
 * Confirmed relationships already have one canonical presentation in the
 * metadata section. This only removes the old, machine-generated list when it
 * contains wikilink bullets; a human-authored prose section remains untouched.
 */
function removeLegacyRelatedSection(body: string): string {
  const header = body.match(/^##\s+Related\s*$/im);
  if (!header || header.index === undefined) return body;

  const start = header.index;
  const afterHeader = start + header[0].length;
  const rest = body.slice(afterHeader);
  const nextHeading = rest.match(/^#{1,6}\s+/m);
  const end = nextHeading?.index === undefined
    ? body.length
    : afterHeader + nextHeading.index;
  const section = body.slice(start, end);
  if (!/^\s*[-*]\s+.*\[\[[^\]]+\]\]/m.test(section)) return body;

  const before = body.slice(0, start).trimEnd();
  const after = body.slice(end).trimStart();
  return [before, after].filter(Boolean).join("\n\n");
}

export default function WikiPreview() {
  const selectedFile = useWikiStore((s) => s.selectedFile);
  const selectedLoading = useWikiStore((s) => s.selectedLoading);
  const selectedError = useWikiStore((s) => s.selectedError);
  const files = useWikiStore((s) => s.files);
  const selectFile = useWikiStore((s) => s.selectFile);
  const loadFiles = useWikiStore((s) => s.loadFiles);
  // 缺失链接只进入虚拟态，不自动落盘伪概念。真正的知识条目必须由员工
  // 明确新建，或由蒸馏流程写入。
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

  // P3.5.172 Phase C (7/3 鸿波): 🔗 扫描关联 AI modal state.
  //
  // ## 7/30 改: 直接用员工在 picker 上选的那个 model
  //
  // 原本是「读 picker_state.json, 读不到就用写死的 catfish-public-deepseek-flash」。
  // 两处都不对:
  //
  //   · picker_state.json 是**滞后的派生副本** —— 它由 chat.ts 在**发送消息前**
  //     fire-and-forget 写入 (见 lib/picker_state.ts 的设计说明: 它存在的目的是
  //     给 hermes memory plugin 读, 因为 sync_turn 的签名拿不到请求头)。
  //     而且 getPickerState 的注释白纸黑字写着"调试用"。
  //     后果: 员工切了 model 但还没发过聊天 → wiki 用的是**上一个** model。
  //
  //   · 写死兜底违反 6/29 那次清理确立的原则 (ChatTab.tsx:36):
  //     "model 为空 比静默兜底更清晰, 客户改 catalog 后不会出现
  //      '看着正常但其实走老 model' 的鬼影 bug"
  //     而模型现在还能在中央门户界面上被删掉 —— 写死的那个删了就会调一个
  //     不存在的模型。
  //
  // 现在直接读 useChatStore 的 model, 也就是 ChatModelPicker 的 onChange
  // 直接写入的那个值 (ChatTab.tsx:382 → setModel)。没有中转、没有滞后,
  // 员工选了哪个就是哪个。
  const [suggestOpen, setSuggestOpen] = useState(false);
  const chatModel = useChatStore((s) => s.model);

  function openLinkSuggest() {
    // model 为空时不要带着空名字去调 LLM —— 那会得到一个来自 gateway 的
    // "model 为空" 报错, 出现在扫描结果的位置上, 员工得先看懂那句话才知道
    // 该去聊天页选模型。在入口直接说清楚更省事。
    //
    // 空的成因: 员工从没选过 model 且 catalog 也没给出 default
    // (ChatTab.tsx:181 会在 catalog.default 到位后注入)。
    if (!chatModel) {
      window.alert(
        "还没有选定模型。\n\n" +
          "🔗 扫描关联要调用大模型，用的是你在「聊天」页选的那个模型。\n" +
          "请先去聊天页选一个，再回来扫描。",
      );
      return;
    }
    setSuggestOpen(true);
  }

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
      setDraftBody(removeLegacyRelatedSection(selectedFile.body));
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
    setDraftBody(removeLegacyRelatedSection(selectedFile.body));
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
    const body = removeLegacyRelatedSection(selectedFile.body);
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
    // Metadata already displays the title; omit only an identical leading H1.
    const leadingTitle = mainBody.match(/^\s*#\s+([^\n]+)\n?/);
    const body = leadingTitle?.[1].trim() === selectedFile.info.title.trim()
      ? mainBody.slice(leadingTitle[0].length).trimStart()
      : mainBody;
    return body.replace(
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
    const match = resolveWikiRefOrNull(name, files);
    if (match) {
      void selectFile(match.rel_path);
      return;
    }
    // 缺失链接只显示虚拟体系；否则一次点击会把 raw/sources 路径或普通
    // 文本误写成概念，污染后续图谱。
    setVirtualSystem(name);
    void selectFile(null);
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
    return !resolveWikiRefOrNull(name, files);
  };

  const kind = info.kind;
  const kindLabel = wikiKindLabel(kind);

  return (
    <div className="wiki-preview">
      <WikiMetaHeader
        info={info}
        kind={kind}
        kindLabel={kindLabel}
        isDangling={isDangling}
        handleWikilinkClick={handleWikilinkClick}
      />

      <WikiHubStaleBanner hubStaleInfo={hubStaleInfo} />

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
            {/* P3.5.172 Phase C (7/3 鸿波): 🔗 扫描关联 — LLM 扫 body 找现有节点
                建议加 wikilink. 只对 entity/concept 显 (query 不适合关联). */}
            {(selectedFile?.info.kind === "concept" ||
              selectedFile?.info.kind === "entity") && (
              <button
                className="approval-banner__btn-link"
                onClick={openLinkSuggest}
                title="AI 扫描 body 找可能对应现有 wiki 节点的名字, 建议加关联 (你确认后加入)"
                disabled={deleting}
              >
                🔗 扫描关联
              </button>
            )}
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
        <WikiShareDialog
          selectedFile={selectedFile}
          shareNamespace={shareNamespace}
          setShareNamespace={setShareNamespace}
          shareAck={shareAck}
          setShareAck={setShareAck}
          sharing={sharing}
          shareWarnings={shareWarnings}
          shareError={shareError}
          shareSuccess={shareSuccess}
          sensitiveTermsHint={sensitiveTermsHint}
          handleShare={handleShare}
          handleEnsureSensitiveTerms={handleEnsureSensitiveTerms}
          setShareDialogOpen={setShareDialogOpen}
        />
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
          urlTransform={wikiMarkdownUrl}
          components={{
            a: ({ href, children }) => {
              if (href?.startsWith("catfish-wikilink://")) {
                const name = decodeURIComponent(
                  href.slice("catfish-wikilink://".length),
                );
                const dangling = isDangling(name);
                return (
                  <button
                    type="button"
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
              const external = isExternalLink(href);
              return (
                <a
                  href={href}
                  target={external ? "_blank" : undefined}
                  rel={external ? "noreferrer" : undefined}
                  onClick={(event) => {
                    if (!external) event.preventDefault();
                  }}
                >
                  {children}
                </a>
              );
            },
          }}
        >
          {rendered}
        </ReactMarkdown>
      )}

      <WikiHistorySection
        editing={editing}
        historyBody={historyBody}
        showHistory={showHistory}
        setShowHistory={setShowHistory}
      />
      <details className="wiki-preview__action-details" key={info.rel_path}>
        <summary>关联行动 · 查看与配置</summary>
        <WikiActionPanel
          selectedFile={selectedFile}
          readOnly={info.rel_path.startsWith("wiki-shared/")}
        />
      </details>

      {/* P3.5.172 Phase C (7/3 鸿波): 🔗 扫描关联 modal. Portal-style render, 独
          立于 preview 内容, ModalShell 已包 fixed overlay. 员工确认后 onApplied
          → loadFiles + selectFile 重刷 body (拿到新 "## 关联概念" 段落). */}
      {suggestOpen && selectedFile && (
        <WikiLinkSuggestModal
          currentTitle={selectedFile.info.title}
          currentRelPath={selectedFile.info.rel_path}
          currentBody={selectedFile.body}
          currentFrontmatter={selectedFile.frontmatter}
          model={chatModel}
          onClose={() => setSuggestOpen(false)}
          onApplied={() => {
            // 严格 reload files 拿新 related edges → WikiGraph 自动更新图
            void loadFiles();
            // 严格 reselect 拿新 body (含 "## 关联概念" 段落)
            if (selectedFile) selectFile(selectedFile.info.rel_path);
          }}
        />
      )}
    </div>
  );
}
