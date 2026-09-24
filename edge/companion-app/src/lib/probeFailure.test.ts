import { describe, expect, it } from "vitest";
import { classifyProbeFailure } from "./probeFailure";
describe("native TLS diagnostics", () => {
  it.each([
    "schannel: SEC_E_UNTRUSTED_ROOT (0x80090325)",
    "证书链是由不受信任的颁发机构颁发的",
    "invalid peer certificate: UnknownIssuer",
    "SSL certificate problem: self-signed certificate",
  ])("recognizes %s", (message) => expect(classifyProbeFailure(message)).toBe("cert"));
  it("does not confuse timeout or refusal with certificate failure", () => {
    expect(classifyProbeFailure("operation timed out")).toBe("timeout");
    expect(classifyProbeFailure("connection refused")).toBe("other");
  });
});
