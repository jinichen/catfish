/** 模型下拉选择 —— 从 catalog 里读所有模型,显示为 dropdown
 *
 * ## 8/1 修的三件事
 *
 * 1. **普通模型不再被 Codex 运行时挡住。** 原来 onChange 无条件先
 *    `await codexBackendSelectModel(next)`, 失败就不调 onChange。而那条命令
 *    要起 hermes python 子进程, hermes 没装就直接报错 —— 于是内网上 hermes
 *    bootstrap 没跑完的机器, 连纯 gateway 模型都换不了。那些机器上聊天本来
 *    是能用的 (chat.ts 拿不到 hermes 配置会降级走 gateway 直连), 唯独选择器
 *    整个失效。现在只有"目标是 Codex 模型"才把对齐当成必要条件。
 *
 * 2. **报错说实话。** 原来无论什么原因失败, 界面固定显示"切换失败，请检查
 *    Codex 登录"。切 DeepSeek 失败也这么说, 把人往完全无关的方向带。
 *
 * 3. **Hermes 说要新会话时告诉员工。** 后端如实回传 requiresNewSession 之后
 *    (见 codex_backend.rs 那段注释), 这里得把它显示出来 —— 否则那一位又是
 *    传了没人看, 跟没传一样。
 *
 * 顺带把模型可达性指示补回来: 原来有 ●/◐/○ 三态, 重做界面时删掉了, 于是
 * "没配 key / 连不上"的模型和正常模型长得一模一样, 员工要选中发一条才知道。
 */

import { useCatalog } from "../../hooks/useCatalog";
import { savePickerState } from "../../lib/picker_state";
import { codexBackendSelectModel } from "../../lib/tauri";
import { useChatStore } from "../../store/chat";
import type { CatalogModel } from "../../types/catalog";
import { useEffect, useState } from "react";

interface Props {
  current: string;
  onChange: (modelId: string, pickedByUser?: boolean, runtimeAlreadySynced?: boolean) => void;
  /** 8/3: 「要新建会话才生效」提示里那个按钮 —— 让员工在原地就能照做,
   *  而不是读完一句话再自己去左上角找 +。ChatTab 的 handleNew。 */
  onNewChat?: () => void;
}

function modelLabel(m: CatalogModel): string {
  // 用 display_name 但截短: "Qwen3.5 122B (A10B MoE) · 主力" → "Qwen3.5 122B"
  const base = m.display_name.split(" · ")[0] || m.display_name;
  return base.length > 30 ? `${base.slice(0, 28)}…` : base;
}

/** 可达性三态。判据跟 Dashboard 的 CatalogCard 同一份 (types/catalog.ts 文件头)。
 *  下拉框里塞不下颜色, 用符号: ● 通 / ◐ 配了 key 但探不通 / ○ 没配 key。 */
function reachDot(m: CatalogModel): string {
  if (!m.api_key_configured) return "○ ";
  return m.is_reachable === true ? "" : "◐ ";
}

export default function ChatModelPicker({ current, onChange, onNewChat }: Props) {
  const { catalog } = useCatalog();
  const models = catalog?.models ?? [];
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needNewSession, setNeedNewSession] = useState(false);
  // setModel 的自动路径 (启动恢复 / catalog.default 联动) 对齐失败也要在这里
  // 说出来 —— 那几条路径没有别的界面入口。
  const runtimeSyncError = useChatStore((s) => s.runtimeSyncError);
  const persistedSessionId = useChatStore((s) => s.persistedSessionId);

  // 8/3 鸿波"搞得我都不知道怎么回事": 这条提示原来不会消失。
  //
  // needNewSession 只在下一次切模型时被重置 (handleChange 开头), 而
  // ChatModelPicker 挂在 header 上, 换会话不重挂 —— 于是员工照做、新建了对话,
  // 那句"要新建会话才生效"还挂在那儿。提示活得比它描述的状态还久, 人就没法
  // 判断自己到底做没做对。
  //
  // 会话一换 (新建 / 切到别的) 这条提示就该消失, 因为它说的事已经成立了。
  useEffect(() => {
    setNeedNewSession(false);
  }, [persistedSessionId]);

  const gatewayModels = models.filter((model) => model.source !== "codex");
  const codexModels = models.filter((model) => model.source === "codex");

  const options = (items: CatalogModel[]) =>
    items.map((m) => (
      <option
        key={`${m.source ?? "gateway"}:${m.id}`}
        value={m.id}
        disabled={m.selectable === false}
        title={m.status_reason || undefined}
      >
        {m.selectable === false ? "不可用 · " : reachDot(m)}
        {modelLabel(m)}
      </option>
    ));

  const handleChange = async (next: string) => {
    // 目标是不是 Codex 模型, 决定运行时对齐是"必要条件"还是"顺手做的事"。
    // catalog 没加载出来时 find 是 undefined —— 那就当普通模型处理, 不拿一个
    // 不确定的判断去挡住员工换模型。
    const nextIsCodex = models.find((m) => m.id === next)?.source === "codex";
    setSwitching(true);
    setError(null);
    setNeedNewSession(false);
    try {
      // 先完成 runtime 对齐再更新 store，避免员工刚选 Codex 就立即
      // 发送时，请求抢在 config 写入前到达 Hermes。
      const status = await codexBackendSelectModel(next);
      if (status.requiresNewSession) setNeedNewSession(true);
    } catch (value) {
      const detail = value instanceof Error ? value.message : String(value);
      if (nextIsCodex) {
        // Codex 模型**必须**有运行时才能跑, 对齐失败就不能假装切成功了。
        setError(detail);
        setSwitching(false);
        return;
      }
      // 普通 gateway 模型不依赖 Codex 运行时, 照切。但也不能当无事发生 ——
      // 对齐失败通常意味着上一次选的 Codex shim 还挂着。
      setError(`已切到 ${next}，但 Codex 运行时没能同步：${detail}`);
    }
    savePickerState(next);
    onChange(next, true, true);
    window.dispatchEvent(new CustomEvent("catfish:catalog-refresh"));
    setSwitching(false);
  };

  return (
    <div className="chat-model-picker">
      <select
        value={current}
        disabled={switching}
        aria-label="选择聊天模型"
        onChange={(e) => void handleChange(e.target.value)}
        className="chat-model-picker__select"
        data-error={!!error || undefined}
        data-switching={switching || undefined}
      >
        {models.length === 0 && <option value={current}>{current}</option>}
        {current && !models.some((model) => model.id === current) && (
          <option value={current}>{current}</option>
        )}
        {gatewayModels.length > 0 && <optgroup label="公司 / 通用模型">{options(gatewayModels)}</optgroup>}
        {codexModels.length > 0 && <optgroup label="Codex · ChatGPT">{options(codexModels)}</optgroup>}
      </select>
      {/* 原文照显, 不再拿一句写死的"请检查 Codex 登录"盖住真实原因。
          title 留全文 —— 后端的错误可能带 Python traceback 尾巴。 */}
      {error && (
        <span title={error} className="chat-model-picker__error">
          {error.length > 60 ? `${error.slice(0, 58)}…` : error}
        </span>
      )}
      {!error && runtimeSyncError && (
        <span title={runtimeSyncError} className="chat-model-picker__error">
          运行时没跟上，界面显示的模型可能不是实际在跑的 —— 重选一次试试
        </span>
      )}
      {/* persistedSessionId 为空 = 现在就是一个还没落地的新对话, 下一条消息
          本来就会创建新 session (ensureSessionId → X-Hermes-Session-Id),
          模型自然生效。这种时候这条提示纯属噪音, 不显示。 */}
      {needNewSession && persistedSessionId && (
        <span className="chat-model-picker__hint">
          <span>
            这个模型要新开一个对话才会生效 —— 在当前对话继续发，跑的还是原来那个。
          </span>
          {onNewChat && (
            <button
              type="button"
              onClick={onNewChat}
              className="chat-model-picker__hint-action"
            >
              新建对话
            </button>
          )}
        </span>
      )}
    </div>
  );
}
