import { createCipheriv, createDecipheriv, createHmac, randomBytes } from "node:crypto";
import type { ChatInput } from "../application/chat";

export function createChatInputCodec(botToken: string) {
  if (!botToken) throw new Error("MAX chat not configured");
  const key = createHmac("sha256", botToken).update("prodigy:max:chat-input:v1").digest();
  return {
    seal(input: ChatInput, eventKey: string): string {
      const nonce = randomBytes(12);
      const cipher = createCipheriv("aes-256-gcm", key, nonce);
      cipher.setAAD(Buffer.from(eventKey));
      const ciphertext = Buffer.concat([cipher.update(JSON.stringify(input), "utf8"), cipher.final()]);
      return Buffer.concat([nonce, cipher.getAuthTag(), ciphertext]).toString("base64url");
    },
    open(value: string, eventKey: string): ChatInput {
      if (value.length > 8192) throw new Error("Invalid chat input");
      const buffer = Buffer.from(value, "base64url");
      const decipher = createDecipheriv("aes-256-gcm", key, buffer.subarray(0, 12));
      decipher.setAuthTag(buffer.subarray(12, 28));
      decipher.setAAD(Buffer.from(eventKey));
      const input: unknown = JSON.parse(Buffer.concat([
        decipher.update(buffer.subarray(28)), decipher.final(),
      ]).toString("utf8"));
      if (!input || typeof input !== "object" || !("type" in input) || !("messageId" in input) ||
          typeof input.messageId !== "string" || input.messageId.length > 256) throw new Error("Invalid chat input");
      if (input.type === "text" && "text" in input && typeof input.text === "string" && input.text.length <= 500) {
        return { type: "text", messageId: input.messageId, text: input.text };
      }
      if (input.type === "callback" && "callbackId" in input && typeof input.callbackId === "string" &&
          input.callbackId.length <= 256 && "payload" in input && typeof input.payload === "string" && input.payload.length <= 256) {
        return { type: "callback", messageId: input.messageId, callbackId: input.callbackId, payload: input.payload };
      }
      throw new Error("Invalid chat input");
    },
  };
}
