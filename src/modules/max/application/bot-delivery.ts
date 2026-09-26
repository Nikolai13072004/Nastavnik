import type { ChatEvent } from "./chat";

export type BotStart = { userId: number; chatId: number; timestamp: number };
export type BotHelp = { kind: "HELP"; userId: number; timestamp: number };
export type BotEvent = BotStart | BotHelp | ChatEvent;
export type BotDelivery = {
  eventKey: string;
  maxUserId: string;
  kind?: "WELCOME" | "DOCUMENT_REVISION" | "HELP" | "CHAT";
  documentId?: string | null;
  chatInputCiphertext?: string | null;
};
export type DeliveryOutcome =
  | { status: "SENT"; messageId: string }
  | { status: "UNCERTAIN"; errorCode: string };

export interface BotDeliveryRepository {
  enqueue(botUsername: string, event: BotEvent): Promise<void>;
  claim(botUsername: string): Promise<BotDelivery | null>;
  finish(eventKey: string, outcome: DeliveryOutcome): Promise<void>;
}

export function parseBotStart(value: unknown, now: number): BotStart | "ignored" | null {
  if (!value || typeof value !== "object" || !("update_type" in value)
    || typeof value.update_type !== "string") return null;
  if (value.update_type !== "bot_started") return "ignored";
  if (!("timestamp" in value) || typeof value.timestamp !== "number"
    || !Number.isSafeInteger(value.timestamp) || value.timestamp <= 0
    || !("chat_id" in value) || typeof value.chat_id !== "number"
    || !Number.isSafeInteger(value.chat_id) || value.chat_id === 0
    || !("user" in value) || !value.user || typeof value.user !== "object"
    || !("user_id" in value.user) || typeof value.user.user_id !== "number"
    || !Number.isSafeInteger(value.user.user_id) || value.user.user_id <= 0) return null;
  // MAX retries for up to 8 hours. A day accommodates retries, but not indefinite replay.
  if (value.timestamp < now - 86_400_000 || value.timestamp > now + 60_000) return "ignored";
  if ("is_bot" in value.user && value.user.is_bot === true) return "ignored";
  return { userId: value.user.user_id, chatId: value.chat_id, timestamp: value.timestamp };
}

export function parseBotEvent(value: unknown, now: number): BotEvent | "ignored" | null {
  if (!value || typeof value !== "object" || !("update_type" in value)
    || value.update_type !== "message_created") return parseBotStart(value, now);
  if (!("timestamp" in value) || typeof value.timestamp !== "number"
    || !Number.isSafeInteger(value.timestamp) || value.timestamp <= 0) return "ignored";
  if (value.timestamp < now - 86_400_000 || value.timestamp > now + 60_000) return "ignored";
  if (!("message" in value) || !value.message || typeof value.message !== "object") return "ignored";
  const message = value.message;
  if (!("sender" in message) || !message.sender || typeof message.sender !== "object"
    || !("user_id" in message.sender) || typeof message.sender.user_id !== "number"
    || !Number.isSafeInteger(message.sender.user_id) || message.sender.user_id <= 0
    || !("is_bot" in message.sender) || message.sender.is_bot !== false) return "ignored";
  if (!("recipient" in message) || !message.recipient || typeof message.recipient !== "object"
    || !("chat_type" in message.recipient) || message.recipient.chat_type !== "dialog") return "ignored";
  if (!("body" in message) || !message.body || typeof message.body !== "object"
    || !("text" in message.body) || typeof message.body.text !== "string"
    || !message.body.text.trim()) return "ignored";
  // Only generic help is queued. Never persist message text, codes, names or attachments.
  return { kind: "HELP", userId: message.sender.user_id, timestamp: value.timestamp };
}

export async function deliverNextBotMessage(
  repository: BotDeliveryRepository,
  botUsername: string,
  send: (userId: number, botUsername: string) => Promise<string>,
  sendRevision: (userId: number, botUsername: string) => Promise<string> = async () => {
    throw new Error("Revision sender is not configured");
  },
  sendHelp: (userId: number, botUsername: string) => Promise<string> = async () => {
    throw new Error("Help sender is not configured");
  },
  sendChat: (job: BotDelivery) => Promise<string> = async () => {
    throw new Error("Chat sender is not configured");
  },
): Promise<"idle" | "sent" | "uncertain"> {
  const job = await repository.claim(botUsername);
  if (!job) return "idle";

  let outcome: DeliveryOutcome;
  try {
    const sender = job.kind === "DOCUMENT_REVISION" ? sendRevision : job.kind === "HELP" ? sendHelp : send;
    const messageId = job.kind === "CHAT" ? await sendChat(job) : await sender(Number(job.maxUserId), botUsername);
    outcome = { status: "SENT", messageId };
  } catch {
    // Even transport/5xx failures can follow a successful remote send. No automatic resend.
    outcome = { status: "UNCERTAIN", errorCode: "SEND_NOT_CONFIRMED" };
  }
  // A DB failure leaves SENDING and blocks further sends until an operator investigates.
  await repository.finish(job.eventKey, outcome);
  return outcome.status === "SENT" ? "sent" : "uncertain";
}
