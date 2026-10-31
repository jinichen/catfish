/** SkillsMcpCard 的三个对话框 —— 容器 + 装 skill + 接 MCP。
 *
 * 8/15 从 SkillsMcpCard.tsx 搬出来。
 *
 * `Dialog` 是容器 (遮罩 + 标题 + ESC 关闭), 另外两个套在它里面。三个绑一起走,
 * 拆开的话改遮罩行为要跳两个文件。
 *
 * # 这里**只收输入, 不做事**
 *
 * 两个 Dialog 都只是把员工填的东西通过 onSubmit 交回主卡片, 真正调
 * installSkillFromUrl / addMcpServer 的是 SkillsMcpCard。这样"装一个 skill"
 * 到底发生了什么, 只有一个地方能看全 —— 装外部来源的东西是有风险的动作,
 * 不该分散在几个对话框里各写一半。
 */
import { useEffect, useState } from "react";

import type { McpAddRequest } from "../../lib/tauri_app";

/* ───────── modal 容器 + 安装/接入对话框 ───────── */

export function Dialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  // ESC 关
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="install-dialog__backdrop" onClick={onClose}>
      <div
        className="install-dialog"
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="install-dialog__header">
          <h4>{title}</h4>
          <button
            className="install-dialog__close"
            onClick={onClose}
            aria-label="关闭"
          >
            ×
          </button>
        </div>
        <div className="install-dialog__body">{children}</div>
      </div>
    </div>
  );
}

export function InstallSkillDialog({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (url: string) => void;
}) {
  const [url, setUrl] = useState("");
  const valid =
    url.startsWith("http://") ||
    url.startsWith("https://") ||
    url.startsWith("github:") ||
    url.startsWith("@");
  return (
    <Dialog title="安装 skill" onClose={onClose}>
      <div className="install-dialog__hint">
        从 GitHub URL 或 npm 包名安装. 走 <code>npx -y skills add</code>{" "}
        命令 (跟 terminal 装一样).
      </div>
      <label className="install-dialog__label">URL / 包名</label>
      <input
        className="install-dialog__input"
        type="text"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="https://github.com/Leonxlnx/taste-skill 或 @vercel/some-skill"
        autoFocus
      />
      {url && !valid && (
        <div className="install-dialog__warn">
          需 http(s):// / github: / @scope/pkg 开头
        </div>
      )}
      <div className="install-dialog__actions">
        <button className="approval-banner__btn-link" onClick={onClose}>
          取消
        </button>
        <button
          className="approval-banner__btn-primary"
          disabled={!valid}
          onClick={() => onSubmit(url)}
        >
          安装
        </button>
      </div>
    </Dialog>
  );
}

type Pair = [string, string];

/** 键值对编辑 (环境变量 / 请求头): 值一律按密钥处理, 输入框是密码样式 */
function PairRows({ label, hint, pairs, onChange, keyPlaceholder }: {
  label: string;
  hint: string;
  pairs: Pair[];
  onChange: (p: Pair[]) => void;
  keyPlaceholder: string;
}) {
  const set = (i: number, j: 0 | 1, v: string) =>
    onChange(pairs.map((p, k) => (k === i ? ((j === 0 ? [v, p[1]] : [p[0], v]) as Pair) : p)));
  return (
    <>
      <label className="install-dialog__label">{label}</label>
      {pairs.map((p, i) => (
        <div key={i} style={{ display: "flex", gap: 6, marginBottom: 6 }}>
          <input className="install-dialog__input" style={{ flex: 2 }} value={p[0]} placeholder={keyPlaceholder}
            onChange={(e) => set(i, 0, e.target.value)} />
          <input className="install-dialog__input" style={{ flex: 3 }} type="password" value={p[1]} placeholder="值 (按密钥保存)"
            onChange={(e) => set(i, 1, e.target.value)} />
          <button className="approval-banner__btn-link" onClick={() => onChange(pairs.filter((_, k) => k !== i))}>删</button>
        </div>
      ))}
      <button className="approval-banner__btn-link" onClick={() => onChange([...pairs, ["", ""]])}>+ 添加</button>
      <div className="install-dialog__hint">{hint}</div>
    </>
  );
}

/** 接入 MCP (10/31: 本机程序 / 远程网址两种; 密钥写进 Hermes 的 .env, 配置文件里只留引用) */
export function AddMcpDialog({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (req: McpAddRequest) => void;
}) {
  const [mode, setMode] = useState<"local" | "remote">("local");
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [argsText, setArgsText] = useState("");
  const [url, setUrl] = useState("");
  const [env, setEnv] = useState<Pair[]>([]);
  const [headers, setHeaders] = useState<Pair[]>([]);
  const nameValid = name.length > 0 && !name.startsWith("catfish-") && name !== "catfish-tools";
  const pairsValid = (ps: Pair[]) => ps.every(([k]) => k.trim().length > 0);
  const valid =
    nameValid &&
    (mode === "local"
      ? command.trim().length > 0 && pairsValid(env)
      : /^https?:\/\//.test(url.trim()) && pairsValid(headers));
  const tab = (m: "local" | "remote", text: string) => (
    <button
      className={mode === m ? "approval-banner__btn-primary" : "approval-banner__btn-link"}
      onClick={() => setMode(m)}
    >
      {text}
    </button>
  );
  return (
    <Dialog title="接入 MCP" onClose={onClose}>
      <div className="install-dialog__hint">
        接入后会先试连一次 (第一次可能要下载, 等 1–2 分钟); 连上了小鲶就能用, 连不上会撤销并告诉你原因.
      </div>
      <div style={{ display: "flex", gap: 8, margin: "4px 0 8px" }}>
        {tab("local", "本机程序 (npx / uvx / 程序路径)")}
        {tab("remote", "远程网址")}
      </div>
      <label className="install-dialog__label">名字</label>
      <input
        className="install-dialog__input"
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="如 12306、amap (不能用 catfish-* 前缀)"
        autoFocus
      />
      {name && !nameValid && <div className="install-dialog__warn">catfish-* 是核心命名保留, 用别的</div>}
      {mode === "local" ? (
        <>
          <label className="install-dialog__label">启动命令 (如 npx / uvx, 或程序的完整路径)</label>
          <input className="install-dialog__input" type="text" value={command}
            onChange={(e) => setCommand(e.target.value)} placeholder="npx" />
          <label className="install-dialog__label">参数 (空格分隔, 建议写上版本号)</label>
          <input className="install-dialog__input" type="text" value={argsText}
            onChange={(e) => setArgsText(e.target.value)} placeholder="-y 12306-mcp@0.3.10" />
          <PairRows
            label="环境变量 (要填 API Key 的 MCP 在这里填)"
            hint="值存在 Hermes 的 .env (只有你能读), 配置文件里只留引用, 不写明文."
            pairs={env}
            onChange={setEnv}
            keyPlaceholder="如 AMAP_MAPS_API_KEY"
          />
        </>
      ) : (
        <>
          <label className="install-dialog__label">网址</label>
          <input className="install-dialog__input" type="text" value={url}
            onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/mcp" />
          <PairRows
            label="请求头 (要鉴权的填, 如 Authorization)"
            hint="值存在 Hermes 的 .env (只有你能读), 配置文件里只留引用, 不写明文."
            pairs={headers}
            onChange={setHeaders}
            keyPlaceholder="如 Authorization"
          />
        </>
      )}
      <div className="install-dialog__actions">
        <button className="approval-banner__btn-link" onClick={onClose}>
          取消
        </button>
        <button
          className="approval-banner__btn-primary"
          disabled={!valid}
          onClick={() =>
            onSubmit(
              mode === "local"
                ? { name: name.trim(), command: command.trim(), args: argsText.split(/\s+/).filter((x) => x.length > 0), env }
                : { name: name.trim(), url: url.trim(), headers },
            )
          }
        >
          接入
        </button>
      </div>
    </Dialog>
  );
}
