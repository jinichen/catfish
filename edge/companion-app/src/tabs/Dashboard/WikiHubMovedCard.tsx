/** 设置 → 技能 →「部门知识库」(11/12 起挪到 资料库 →「同事分享」)。
 *
 * 同事分享的知识原来只在这里, 找知识的人会去「资料库」找, 不会想到翻设置里的「技能」。
 * 这里先留一阵, 只放一句话 + 一个按钮跳过去, 老用户按老习惯点进来也找得到。
 */
import { useUIStore } from "../../store/ui";

export default function WikiHubMovedCard() {
  const openWikiShared = useUIStore((s) => s.openWikiShared);
  return (
    <div style={{
      background: "var(--catfish-bg-elevated)", border: "1px solid var(--catfish-border)", borderRadius: "var(--radius-md)",
      padding: "var(--space-4)", display: "flex", alignItems: "center", gap: "var(--space-3)", flexWrap: "wrap",
    }}>
      <span style={{ flex: 1, minWidth: 240, fontSize: 13, lineHeight: 1.7, color: "var(--catfish-text)" }}>
        📚 同事分享的知识已挪到左边「资料库」的「同事分享」分页 —— 预览、收进资料库都在那里。
      </span>
      <button type="button" onClick={openWikiShared}
              style={{ fontSize: 13, padding: "6px 14px", borderRadius: "var(--radius-sm)", cursor: "pointer",
                       border: "1px solid var(--catfish-cyan)", background: "var(--catfish-cyan)", color: "var(--catfish-bg)" }}>
        去资料库看
      </button>
    </div>
  );
}
