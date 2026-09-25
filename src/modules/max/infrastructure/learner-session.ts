import { createHmac, timingSafeEqual } from "node:crypto";
import type { MaxLearnerIdentity } from "../application/list-courses";

export type MaxSessionIdentity = MaxLearnerIdentity;

export type MaxSession = { token: string; expiresAt: string };
const PURPOSE = "prodigy:max:learner:v1";
const LIFETIME_SECONDS = 900;

export function createMaxSessionCodec(botToken: string) {
  if (!botToken) throw new Error("MAX_NOT_CONFIGURED");
  // Separate key/purpose from both MAX launch validation and NextAuth cookies.
  const key = createHmac("sha256", botToken).update(PURPOSE).digest();
  const sign = (payload: string) => createHmac("sha256", key).update(payload).digest();

  return {
    issue(identity: MaxSessionIdentity, now = Math.floor(Date.now() / 1000)): MaxSession {
      const expires = now + LIFETIME_SECONDS;
      const payload = Buffer.from(JSON.stringify({ ...identity, purpose: PURPOSE, iat: now, exp: expires })).toString("base64url");
      return { token: `${payload}.${sign(payload).toString("base64url")}`, expiresAt: new Date(expires * 1000).toISOString() };
    },
    verify(token: string, now = Math.floor(Date.now() / 1000)): MaxSessionIdentity | null {
      if (token.length > 4096) return null;
      const parts = token.split(".");
      if (parts.length !== 2 || !/^[\w-]+$/.test(parts[0]) || !/^[\w-]{43}$/.test(parts[1])) return null;
      const signature = Buffer.from(parts[1], "base64url");
      if (signature.length !== 32 || !timingSafeEqual(sign(parts[0]), signature)) return null;
      try {
        const claims = JSON.parse(Buffer.from(parts[0], "base64url").toString("utf8"));
        if (claims.purpose !== PURPOSE || !Number.isSafeInteger(claims.iat) || !Number.isSafeInteger(claims.exp) ||
            claims.exp - claims.iat !== LIFETIME_SECONDS || claims.iat > now + 30 || claims.exp <= now) return null;
        for (const field of ["maxUserId", "userId", "organizationId", "linkedAt"]) {
          if (typeof claims[field] !== "string" || claims[field].length === 0) return null;
        }
        return { maxUserId: claims.maxUserId, userId: claims.userId, organizationId: claims.organizationId, linkedAt: claims.linkedAt };
      } catch {
        return null;
      }
    },
  };
}
