export type ProbeFailure = "cert" | "timeout" | "other";

/** Native TLS errors vary by OS and language; Schannel often has no English "cert". */
export function classifyProbeFailure(error: unknown): ProbeFailure {
  const message = String(error).toLowerCase();
  if (/certificate|cert|tls|ssl|handshake|self.signed|unknownissuer|not trusted|untrusted_root|0x80090325|证书|颁发机构/.test(message)) return "cert";
  if (/timeout|timed out|超时|aborterror/.test(message)) return "timeout";
  return "other";
}
