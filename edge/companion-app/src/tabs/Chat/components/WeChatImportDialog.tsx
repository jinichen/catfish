/** 微信聊天记录导入确认框 (9/23) —— 聊天框拖入 ZIP 后弹出。
 *
 * 只问这几件事, 而且尽量不用问:
 *   归到哪个群  —— 认出来就默认选中; 认不出来就新建, 群名先按发送人自动起好
 *   我是谁      —— 别的群里记过就自动带上; 一次记住, 以后不再问
 *   读文档      —— 包里有 pdf/docx 等才出现, 默认勾上 (二期)
 *   授权        —— 只在第一次 (或换了模型) 时出现, 取代原来看板上的授权卡片
 *
 * 规则 (能不能点导入) 在 lib/wechatImport.ts 的 choiceProblem, 这里只画。
 */
import { useState } from "react";
import type { CSSProperties } from "react";

import {
  choiceProblem,
  initialChoice,
  stageSummary,
  type WeChatImportChoice,
  type WeChatStage,
} from "../../../lib/wechatImport";

const NEW_GROUP = "__new__";
const SELF_UNSET = "__unset__";
const SELF_NONE = "__none__";

export default function WeChatImportDialog({
  stage,
  maxDocuments,
  busy,
  error,
  onConfirm,
  onCancel,
}: {
  stage: WeChatStage;
  /** 这条消息还能放几个文档附件 (lib/wechatImport.documentSlots) */
  maxDocuments: number;
  busy: boolean;
  error: string | null;
  onConfirm: (choice: WeChatImportChoice) => void;
  onCancel: () => void;
}) {
  const [choice, setChoice] = useState<WeChatImportChoice>(() => initialChoice(stage));
  const inspect = stage.inspect;
  const already = inspect.already_imported_group_id;
  const problem = choiceProblem(stage, choice);
  const groupOptions = inspect.candidates;
  const documents = inspect.documents ?? [];
  const readable = Math.min(documents.length, maxDocuments);
  const selfValue = choice.selfName === undefined
    ? SELF_UNSET
    : choice.selfName === "" ? SELF_NONE : choice.selfName;

  return (
    <div style={backdropStyle} role="dialog" aria-modal="true" aria-label="导入微信聊天记录">
      <div style={modalStyle}>
        <strong>导入微信聊天记录</strong>
        <p style={hintStyle}>{stageSummary(inspect)}</p>

        {already ? (
          <p style={hintStyle}>
            这份记录之前已经导入过 (「{groupOptions.find((c) => c.group_id === already)?.name
              || inspect.suggested_name}」), 直接拿来用, 不会重复保存。
          </p>
        ) : (
          <label style={fieldStyle}>
            <span>归到</span>
            <select
              style={inputStyle}
              value={choice.groupId ?? NEW_GROUP}
              disabled={busy}
              onChange={(e) => setChoice({
                ...choice,
                groupId: e.target.value === NEW_GROUP ? null : e.target.value,
              })}
            >
              {groupOptions.map((c) => (
                <option key={c.group_id} value={c.group_id}>
                  {c.name}{c.confident ? "" : ` (${c.shared} 人重合)`}
                </option>
              ))}
              <option value={NEW_GROUP}>新建一个群…</option>
            </select>
          </label>
        )}

        {!already && choice.groupId === null && (
          <label style={fieldStyle}>
            <span>群名</span>
            <input
              style={inputStyle}
              value={choice.newGroupName}
              disabled={busy}
              maxLength={200}
              onChange={(e) => setChoice({ ...choice, newGroupName: e.target.value })}
            />
          </label>
        )}

        <label style={fieldStyle}>
          <span>我是</span>
          <select
            style={inputStyle}
            value={selfValue}
            disabled={busy}
            onChange={(e) => {
              const v = e.target.value;
              setChoice({
                ...choice,
                selfName: v === SELF_UNSET ? undefined : v === SELF_NONE ? "" : v,
              });
            }}
          >
            <option value={SELF_UNSET}>先不指定</option>
            {inspect.senders.map((s) => (
              <option key={s.name} value={s.name}>{s.name} ({s.count} 条)</option>
            ))}
            <option value={SELF_NONE}>我不在这些人里</option>
          </select>
        </label>

        {documents.length > 0 && (
          <label style={consentStyle}>
            <input
              type="checkbox"
              checked={choice.includeDocuments && readable > 0}
              disabled={busy || readable === 0}
              onChange={(e) => setChoice({ ...choice, includeDocuments: e.target.checked })}
            />
            <span>
              同时读取包里的 {documents.length} 个文档 (pdf、docx 等; 图片不读)
              {readable < documents.length && (
                readable === 0
                  ? " —— 这条消息的附件已经放满了, 先删几个再导入"
                  : ` —— 这条消息只剩 ${readable} 个附件位置, 只读前 ${readable} 个`
              )}
            </span>
          </label>
        )}

        {stage.needsConsent && (
          <label style={consentStyle}>
            <input
              type="checkbox"
              checked={choice.consent}
              disabled={busy}
              onChange={(e) => setChoice({ ...choice, consent: e.target.checked })}
            />
            <span>
              同意把导入的聊天内容交给当前模型{stage.pickerModel ? ` (${stage.pickerModel})` : ""}分析。
              原包只存在本机, 可随时在「隐私 → 微信接入」里删除。换模型后会再问一次。
            </span>
          </label>
        )}

        {(error || problem) && <p style={error ? errStyle : hintStyle}>{error || problem}</p>}

        <div style={buttonsStyle}>
          <button type="button" style={btnStyle} disabled={busy} onClick={onCancel}>取消</button>
          <button
            type="button"
            style={{ ...primaryBtnStyle, opacity: problem || busy ? 0.5 : 1 }}
            disabled={Boolean(problem) || busy}
            onClick={() => onConfirm(choice)}
          >
            {busy ? (choice.includeDocuments && readable > 0 ? "导入并读取文档…" : "导入中…") : "导入"}
          </button>
        </div>
      </div>
    </div>
  );
}

const backdropStyle: CSSProperties = { position: "fixed", inset: 0, zIndex: 1000, display: "grid", placeItems: "center", background: "rgba(0,0,0,.28)" };
const modalStyle: CSSProperties = { width: "min(460px, calc(100vw - 32px))", display: "flex", flexDirection: "column", gap: 12, padding: 20, borderRadius: 12, background: "var(--catfish-bg)", boxShadow: "0 12px 40px rgba(0,0,0,.25)" };
const hintStyle: CSSProperties = { margin: 0, color: "var(--catfish-text-muted)", fontSize: 12, lineHeight: 1.5 };
const errStyle: CSSProperties = { margin: 0, fontSize: 12, color: "var(--catfish-danger, #c0392b)", whiteSpace: "pre-wrap" };
const fieldStyle: CSSProperties = { display: "grid", gridTemplateColumns: "48px 1fr", alignItems: "center", gap: 8, fontSize: 13 };
const inputStyle: CSSProperties = { minWidth: 0, padding: "6px 9px", border: "1px solid var(--catfish-border)", borderRadius: 6, background: "transparent", color: "var(--catfish-text)", fontSize: 13 };
const consentStyle: CSSProperties = { display: "flex", gap: 8, alignItems: "flex-start", fontSize: 12, lineHeight: 1.5 };
const buttonsStyle: CSSProperties = { display: "flex", justifyContent: "flex-end", gap: 8 };
const btnStyle: CSSProperties = { padding: "6px 12px", border: "1px solid var(--catfish-border)", borderRadius: 6, background: "transparent", color: "var(--catfish-text)", cursor: "pointer", fontSize: 12 };
const primaryBtnStyle: CSSProperties = { ...btnStyle, borderColor: "var(--catfish-cyan)", background: "var(--catfish-cyan)", color: "white" };
