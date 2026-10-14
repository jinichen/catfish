/** 大图缩小后再进对话 (10/2 从 attachmentHelpers 起; 10/14 搬到 lib, 会话恢复时也用)。 */

/** 照片超过这个大小才缩 (手机拍的发票 3-8MB; 截图一般 < 1MB, 不动) */
export const IMAGE_SHRINK_ABOVE_BYTES = 1.5 * 1024 * 1024;
/** 缩到长边这么多像素: 发票上的小字在 2000px 下仍清楚, 体积约原来的 1/10 */
export const IMAGE_MAX_EDGE = 2000;

/** 等比缩放后的尺寸, 长边不超过 maxEdge (本来就小就不变)。 */
export function shrinkTarget(w: number, h: number, maxEdge = IMAGE_MAX_EDGE): { w: number; h: number } {
  const long = Math.max(w, h);
  if (long <= maxEdge) return { w, h };
  const k = maxEdge / long;
  return { w: Math.round(w * k), h: Math.round(h * k) };
}

/** 大照片 → 长边 2000px 的 JPEG (base64)。小图 / 不大的 gif svg / 缩完反而更大 → null (用原图)。
 *  10/14: 超过 IMAGE_WIRE_MAX_BYTES 的 gif 也缩 (取第一帧), 不然整份几十 MB 进对话。 */
export async function shrinkImage(file: Blob): Promise<{ base64: string; mimeType: string } | null> {
  if (file.size <= IMAGE_SHRINK_ABOVE_BYTES) return null;
  if (/gif|svg/.test(file.type) && file.size <= IMAGE_WIRE_MAX_BYTES) return null;
  try {
    const bmp = await createImageBitmap(file);
    const { w, h } = shrinkTarget(bmp.width, bmp.height);
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.fillStyle = "#fff"; // 透明 PNG 转 JPEG 不要变黑底
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(bmp, 0, 0, w, h);
    bmp.close();
    const uri = canvas.toDataURL("image/jpeg", 0.85);
    const base64 = uri.slice(uri.indexOf(",") + 1);
    return base64.length * 0.75 < file.size ? { base64, mimeType: "image/jpeg" } : null;
  } catch (e) {
    console.warn("图片缩小失败, 用原图:", e);
    return null;
  }
}

/** 发给模型的图片不能超过这个大小 —— 更大的必须能缩 (缩不了就请员工转格式) */
export const IMAGE_WIRE_MAX_BYTES = 20 * 1024 * 1024;
