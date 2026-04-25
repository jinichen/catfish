/** 一键全启 / 全停 / 重启所有服务 */

import {
  gatewayStart,
  gatewayStop,
  chromeLaunch,
  chromeKill,
  localSearchStart,
  localSearchStop,
  toolBridgeStart,
  toolBridgeStop,
} from "../../lib/tauri";

async function startAll() {
  await Promise.allSettled([
    gatewayStart(),
    chromeLaunch(),
    localSearchStart(),
    toolBridgeStart(),
  ]);
}

async function stopAll() {
  await Promise.allSettled([
    gatewayStop(),
    chromeKill(),
    localSearchStop(),
    toolBridgeStop(),
  ]);
}

async function restartAll() {
  await stopAll();
  await new Promise((r) => setTimeout(r, 800));
  await startAll();
}

export default function ActionBar() {
  return (
    <div
      style={{
        display: "flex",
        gap: "var(--space-2)",
        alignItems: "center",
      }}
    >
      <button onClick={() => void startAll()}>一键全启</button>
      <button onClick={() => void stopAll()}>全停</button>
      <button onClick={() => void restartAll()}>重启</button>
    </div>
  );
}
