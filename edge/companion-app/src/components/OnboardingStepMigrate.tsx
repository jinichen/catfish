/** 首次启动引导 · 登录之后一步: 「从旧电脑迁移?」(10/31 BL-L9 换机迁移)。
 *
 * 放在登录之后, 因为导入要核对「迁移包是不是本人的」。不迁就「下一步」, 跟别的步骤一样可跳过。
 * 导入成功后直接收尾引导 (旧电脑的设置都带过来了, 后面几步不用再填) 并重启 Companion。
 */
import * as React from "react";

import { Buttons } from "./OnboardingButtons";
import DeviceMigrationImport, { plainBtn } from "./DeviceMigrationImport";

export function StepMigrate({
  onNext,
  onBack,
  onSkip,
  onImported,
}: {
  onNext: () => void;
  onBack: () => void;
  onSkip: () => void;
  /** 导入成功、重启前: 标记引导完成 */
  onImported: () => void;
}) {
  const [open, setOpen] = React.useState(false);
  return (
    <>
      <h3 style={{ marginTop: 0 }}>🧳 换了新电脑?</h3>
      <p style={{ fontSize: 13, lineHeight: 1.6, color: "var(--catfish-text-muted)" }}>
        如果你在旧电脑上用过鲶鱼, 可以把那边的工作日志、记忆、知识库、技能、对话历史整个搬过来。
        先在旧电脑「设置 → 隐私 / 本机数据 → 换机迁移」导出迁移包, 再在这里导入。
      </p>
      {open ? (
        <DeviceMigrationImport beforeRestart={onImported} />
      ) : (
        <button type="button" style={plainBtn} onClick={() => setOpen(true)}>
          我有迁移包, 现在导入
        </button>
      )}
      <Buttons onNext={onNext} onBack={onBack} onSkip={onSkip} nextLabel="不用, 下一步 →" />
    </>
  );
}
