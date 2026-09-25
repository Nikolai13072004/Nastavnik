import { createHmac, timingSafeEqual } from "node:crypto";

export type MaxIdentity = { id: string; firstName: string };

/** Verifies a MAX launch, not LMS membership or permission to access courses. */
export function verifyMaxInitData(
  initData: string,
  botToken: string,
  nowSeconds = Math.floor(Date.now() / 1000),
): MaxIdentity | null {
  if (!botToken || !initData || Buffer.byteLength(initData, "utf8") > 16_384) {
    return null;
  }

  const params = new URLSearchParams(initData);
  const keys = [...params.keys()];
  if (new Set(keys).size !== keys.length) return null;

  const hash = params.get("hash");
  if (!hash || !/^[a-fA-F0-9]{64}$/.test(hash)) return null;
  params.delete("hash");
  params.sort();
  const signedData = [...params].map(([key, value]) => `${key}=${value}`).join("\n");
  const secret = createHmac("sha256", "WebAppData").update(botToken).digest();
  const expected = createHmac("sha256", secret).update(signedData).digest();
  if (!timingSafeEqual(expected, Buffer.from(hash, "hex"))) return null;

  const authDate = params.get("auth_date");
  if (!authDate || !/^\d+$/.test(authDate)) return null;
  const timestamp = Number(authDate);
  if (!Number.isSafeInteger(timestamp) || timestamp > nowSeconds + 30 || nowSeconds - timestamp > 3600) {
    return null;
  }

  try {
    const user: unknown = JSON.parse(params.get("user") ?? "null");
    if (!user || typeof user !== "object" || !("id" in user)) return null;
    if (typeof user.id !== "number" || !Number.isSafeInteger(user.id) || user.id <= 0) return null;
    const firstName = "first_name" in user && typeof user.first_name === "string"
      ? user.first_name.trim().slice(0, 100)
      : "";
    return { id: String(user.id), firstName };
  } catch {
    return null;
  }
}
