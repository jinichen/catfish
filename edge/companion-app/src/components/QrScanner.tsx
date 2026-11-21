/** 用电脑摄像头扫二维码 (11/21 换机直传: 旧电脑扫新电脑屏幕上的码)。
 *
 * getUserMedia 开摄像头 → 每帧画到画布 → jsQR 认码 → 认到就关摄像头、回调。
 * macOS 正式包要 Info.plist 的 NSCameraUsageDescription + 签名的 camera entitlement (build-sign-notarize-mac.sh),
 * 否则拿到的是黑画面 / 直接报错; 打不开摄像头就提示改输配对码。
 */
import * as React from "react";
import jsQR from "jsqr";

export function cameraError(e: unknown): string {
  const name = e instanceof DOMException ? e.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") return "没有摄像头权限: 到系统设置 → 隐私与安全 → 摄像头里允许鲶鱼, 或者改输配对码";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "这台电脑没有摄像头, 请改输配对码";
  if (name === "NotReadableError") return "摄像头被别的程序占着 (会议软件?), 关掉它再试, 或者改输配对码";
  return `打不开摄像头 (${name || String(e)}), 请改输配对码`;
}

export default function QrScanner({ onResult, onError }: { onResult: (text: string) => void; onError: (msg: string) => void }) {
  const video = React.useRef<HTMLVideoElement>(null);
  const canvas = React.useRef<HTMLCanvasElement>(null);
  const cb = React.useRef({ onResult, onError });
  cb.current = { onResult, onError };

  React.useEffect(() => {
    let stream: MediaStream | null = null;
    let raf = 0;
    let stopped = false;
    const stop = () => {
      stopped = true;
      cancelAnimationFrame(raf);
      stream?.getTracks().forEach((t) => t.stop());
    };
    const tick = () => {
      if (stopped) return;
      const v = video.current;
      const c = canvas.current;
      if (v && c && v.readyState >= 2 && v.videoWidth > 0) {
        c.width = v.videoWidth;
        c.height = v.videoHeight;
        const ctx = c.getContext("2d", { willReadFrequently: true });
        if (ctx) {
          ctx.drawImage(v, 0, 0, c.width, c.height);
          const img = ctx.getImageData(0, 0, c.width, c.height);
          const hit = jsQR(img.data, img.width, img.height, { inversionAttempts: "dontInvert" });
          if (hit?.data) {
            stop();
            cb.current.onResult(hit.data);
            return;
          }
        }
      }
      raf = requestAnimationFrame(tick);
    };
    if (!navigator.mediaDevices?.getUserMedia) {
      cb.current.onError("这里用不了摄像头, 请改输配对码");
      return stop;
    }
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "user", width: { ideal: 1280 } }, audio: false })
      .then((s) => {
        if (stopped) {
          s.getTracks().forEach((t) => t.stop());
          return;
        }
        stream = s;
        if (video.current) {
          video.current.srcObject = s;
          void video.current.play().catch(() => {});
        }
        raf = requestAnimationFrame(tick);
      })
      .catch((e) => cb.current.onError(cameraError(e)));
    return stop;
  }, []);

  return (
    <div>
      <video ref={video} muted playsInline style={{ width: 320, maxWidth: "100%", borderRadius: 4, background: "#000", transform: "scaleX(-1)" }} />
      <canvas ref={canvas} style={{ display: "none" }} />
      <div style={{ fontSize: 11, color: "var(--catfish-text-muted)" }}>把新电脑屏幕上的二维码对准摄像头</div>
    </div>
  );
}
