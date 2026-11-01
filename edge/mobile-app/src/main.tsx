/** 小鲶 iOS App 入口: 读钥匙串 → 没填服务器就先填 → 手机页 (central/web/src/mobile, 跟网页版同一份界面)。 */
import React, { useState } from "react";
import ReactDOM from "react-dom/client";

import { MobileRoot } from "../../../central/web/src/mobile/MobileApp";
import { setPlatform } from "../../../central/web/src/mobile/platform";
import { iosPlatform, loadKeychain, server, setServer } from "./platform_ios";
import { ServerSetup } from "./ServerSetup";

function Root() {
  const [hasServer, setHasServer] = useState(() => !!server());
  if (!hasServer) {
    return <ServerSetup onDone={(url) => { setServer(url); setHasServer(true); }} />;
  }
  return <MobileRoot />;
}

void loadKeychain().then(() => {
  setPlatform(iosPlatform);
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <Root />
    </React.StrictMode>,
  );
});
