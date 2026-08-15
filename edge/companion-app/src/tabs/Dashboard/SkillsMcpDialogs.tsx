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

export function AddMcpDialog({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (name: string, command: string, args: string[]) => void;
}) {
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [argsText, setArgsText] = useState("");
  const nameValid =
    name.length > 0 &&
    !name.startsWith("catfish-") &&
    name !== "catfish-tools";
  const commandValid = command.length > 0;
  const valid = nameValid && commandValid;
  return (
    <Dialog title="接入 MCP" onClose={onClose}>
      <div className="install-dialog__hint">
        写 <code>~/.hermes/config.yaml</code> 加 <code>mcp_servers.{"<name>"}</code> 段.
        hermes daemon 重启后生效.
      </div>
      <label className="install-dialog__label">名字</label>
      <input
        className="install-dialog__input"
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="my-mcp (不能用 catfish-* 前缀)"
        autoFocus
      />
      {name && !nameValid && (
        <div className="install-dialog__warn">
          catfish-* 是核心命名保留, 用别的
        </div>
      )}
      <label className="install-dialog__label">command (stdio 启动命令绝对路径)</label>
      <input
        className="install-dialog__input"
        type="text"
        value={command}
        onChange={(e) => setCommand(e.target.value)}
        placeholder="/usr/local/bin/my-mcp-server"
      />
      <label className="install-dialog__label">args (空格分隔, 可空)</label>
      <input
        className="install-dialog__input"
        type="text"
        value={argsText}
        onChange={(e) => setArgsText(e.target.value)}
        placeholder="--port 9000 --token xxx"
      />
      <div className="install-dialog__actions">
        <button className="approval-banner__btn-link" onClick={onClose}>
          取消
        </button>
        <button
          className="approval-banner__btn-primary"
          disabled={!valid}
          onClick={() =>
            onSubmit(
              name,
              command,
              argsText.split(/\s+/).filter((s) => s.length > 0),
            )
          }
        >
          接入
        </button>
      </div>
    </Dialog>
  );
}
