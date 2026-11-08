/** 提醒条「看结果」—— 定时任务这一次的结果全文 (11/09 小鲶秘书 第 1 层)。
 *
 * 直接读存档 (~/.hermes/cron/output/<id>/<ts>.md), 切掉提示词那半 (lib/cronWatch.outputBody), 按 markdown 显示。
 * 不过 LLM: 看结果不该花一次模型调用。想让小鲶接着处理, 点「让小鲶接着处理」才新开对话。
 */
import { useEffect, useState } from "react";

import { ModalShell } from "../../components/ModalShell";
import { outputBody, runAtLabel } from "../../lib/cronWatch";
import { Markdown } from "../../lib/markdown";
import type { Reminder } from "../../lib/reminders";
import { cronJobOutputRead } from "../../lib/tauri";

type Load = { phase: "loading" } | { phase: "ok"; body: string } | { phase: "error"; message: string };

export default function CronResultDialog({
  reminder,
  agentName,
  onClose,
  onContinue,
}: {
  reminder: Reminder;
  agentName: string;
  onClose: () => void;
  /** 新开对话把结果交给小鲶 */
  onContinue: () => void;
}) {
  const run = reminder.cron!;
  const [load, setLoad] = useState<Load>({ phase: "loading" });

  useEffect(() => {
    let cancelled = false;
    if (!run.outputTs) {
      setLoad({ phase: "error", message: "这次运行没有留下结果文件" });
      return;
    }
    cronJobOutputRead(run.jobId, run.outputTs)
      .then((archive) => {
        if (cancelled) return;
        const body = outputBody(archive);
        setLoad(body ? { phase: "ok", body } : { phase: "error", message: "这次没有新内容 (任务自己说没什么要报的)" });
      })
      .catch((e) => {
        if (!cancelled) setLoad({ phase: "error", message: e instanceof Error ? e.message : String(e) });
      });
    return () => {
      cancelled = true;
    };
  }, [run.jobId, run.outputTs]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const when = runAtLabel(run.runAt);

  return (
    <ModalShell onClose={onClose} maxWidth={760} themed>
      <div className="cron-result" role="dialog" aria-label={`定时任务结果：${run.jobName}`}>
        <header className="cron-result__head">
          <h3 className="cron-result__title">{run.jobName}</h3>
          <p className="cron-result__sub">
            {when} 跑完
            {reminder.meta && run.status === "delivery_failed" && (
              <span className="cron-result__warn"> · {reminder.meta.split(" · ").slice(-1)[0]}</span>
            )}
          </p>
        </header>
        <div className="cron-result__body">
          {load.phase === "loading" && <p className="cron-result__muted">读取中…</p>}
          {load.phase === "error" && <p className="cron-result__muted">{load.message}</p>}
          {load.phase === "ok" && <Markdown text={load.body} />}
        </div>
        <footer className="cron-result__actions">
          <button type="button" className="reminder-bar__secondary" onClick={onClose}>
            关闭
          </button>
          <button type="button" className="reminder-bar__primary" onClick={onContinue}>
            {run.prep ? `让${agentName}过一遍` : `让${agentName}接着处理`}
          </button>
        </footer>
      </div>
    </ModalShell>
  );
}
