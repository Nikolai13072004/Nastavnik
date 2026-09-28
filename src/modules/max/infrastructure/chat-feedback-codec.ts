import {
  createCipheriv,
  createDecipheriv,
  createHmac,
  randomBytes,
} from "node:crypto";
import type { ChatState } from "../application/chat";

export function createChatFeedbackCodec(botToken: string) {
  if (!botToken) throw new Error("MAX chat feedback not configured");
  const key = createHmac("sha256", botToken)
    .update("prodigy:max:chat-feedback:v1")
    .digest();
  return {
    seal(value: ChatState["aiFeedback"], sessionKey: string) {
      const nonce = randomBytes(12);
      const cipher = createCipheriv("aes-256-gcm", key, nonce);
      cipher.setAAD(Buffer.from(sessionKey));
      const data = Buffer.concat([
        cipher.update(JSON.stringify(value), "utf8"),
        cipher.final(),
      ]);
      return Buffer.concat([nonce, cipher.getAuthTag(), data]).toString(
        "base64url",
      );
    },
    open(value: string, sessionKey: string): ChatState["aiFeedback"] {
      if (value.length > 128000) throw new Error("Invalid feedback snapshot");
      const data = Buffer.from(value, "base64url");
      const decipher = createDecipheriv(
        "aes-256-gcm",
        key,
        data.subarray(0, 12),
      );
      decipher.setAAD(Buffer.from(sessionKey));
      decipher.setAuthTag(data.subarray(12, 28));
      return JSON.parse(
        Buffer.concat([
          decipher.update(data.subarray(28)),
          decipher.final(),
        ]).toString("utf8"),
      );
    },
  };
}
