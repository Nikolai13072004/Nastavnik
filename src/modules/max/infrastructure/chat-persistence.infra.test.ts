import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { after, test } from "node:test";
import prisma from "@/lib/prisma";
import { prismaBotDeliveryRepository as queue } from "./prisma-bot-delivery-repository";
import { createChatInputCodec } from "./chat-input-codec";
import { createPrismaChatSessions } from "./prisma-chat-session-repository";
import type { ChatEvent } from "../application/chat";

const prefix = `chat_test_${randomUUID().replaceAll("-", "")}`;
const bots: string[] = [];
const users: string[] = [];
const previousToken = process.env.MAX_BOT_TOKEN;
process.env.MAX_BOT_TOKEN = "fixture-chat-secret";
const bot = () => {
  const value = `${prefix}_${bots.length}`;
  bots.push(value);
  return value;
};
const event: ChatEvent = { kind: "CHAT", userId: 123, timestamp: Date.now(),
  input: { type: "text", messageId: "mid", text: "Private question" } };
after(async () => {
  if (previousToken === undefined) delete process.env.MAX_BOT_TOKEN;
  else process.env.MAX_BOT_TOKEN = previousToken;
  await prisma.maxBotDelivery.deleteMany({ where: { botUsername: { in: bots } } });
  await prisma.user.deleteMany({ where: { id: { in: users } } });
  await prisma.$disconnect();
});

test("chat intake deduplicates exact messages, encrypts content and wipes it after delivery", async () => {
  const username = bot();
  await Promise.all(Array.from({ length: 8 }, () => queue.enqueue(username, event)));
  const rows = await prisma.maxBotDelivery.findMany({ where: { botUsername: username } });
  assert.equal(rows.length, 1);
  assert.ok(!JSON.stringify(rows).includes("Private question"));
  const claimed = await queue.claim(username);
  assert.ok(claimed?.chatInputCiphertext);
  assert.deepEqual(createChatInputCodec("fixture-chat-secret").open(claimed.chatInputCiphertext, claimed.eventKey), event.input);
  assert.equal(await queue.claim(username), null);
  await queue.finish(claimed.eventKey, { status: "SENT", messageId: "outgoing" });
  const finished = await prisma.maxBotDelivery.findUniqueOrThrow({ where: { eventKey: claimed.eventKey } });
  assert.equal(finished.chatInputCiphertext, null);
  await queue.enqueue(username, event);
  assert.equal(await prisma.maxBotDelivery.count({ where: { botUsername: username } }), 1);
});

test("expired pending input is skipped, not sent; callbacks deduplicate by their event ID", async () => {
  const username = bot();
  await queue.enqueue(username, { ...event, timestamp: Date.now() - 301_000 });
  assert.equal(await queue.claim(username), null);
  const expired = await prisma.maxBotDelivery.findFirstOrThrow({ where: { botUsername: username } });
  assert.equal(expired.status, "SKIPPED");
  assert.equal(expired.chatInputCiphertext, null);
  const callback: ChatEvent = { ...event, input: { type: "callback", callbackId: "callback-event",
    messageId: "original", payload: "chat:0000000000000001:progress" } };
  await queue.enqueue(username, callback);
  await queue.enqueue(username, callback);
  assert.equal(await prisma.maxBotDelivery.count({ where: { botUsername: username, status: "PENDING" } }), 1);
});

test("concurrent intake rate is bounded per user, without preventing another bot from working", async () => {
  const username = bot();
  await Promise.all(Array.from({ length: 36 }, (_, index) => queue.enqueue(username, {
    ...event, input: { type: "text", text: "question", messageId: `mid-${index}` },
  })));
  assert.equal(await prisma.maxBotDelivery.count({ where: { botUsername: username } }), 30);
  const other = bot();
  await queue.enqueue(other, event);
  assert.ok(await queue.claim(other));
});

test("chat session is bound to organization, user and link generation; expired sessions never resume", async () => {
  const username = bot();
  const userId = `${prefix}_user`;
  await prisma.user.create({ data: { id: userId, login: userId, name: "Fixture", passwordHash: "disabled", role: "Ученик" } });
  users.push(userId);
  const identity = { maxUserId: "123", userId, organizationId: "fixture-org", linkedAt: new Date(0).toISOString() };
  const sessions = createPrismaChatSessions(username);
  const state = { version: "0000000000000001", courseId: "course" };
  await sessions.save(identity, state);
  assert.deepEqual(await sessions.load(identity), state);
  for (const changed of [{ userId: "other" }, { organizationId: "other" }, { linkedAt: new Date(1).toISOString() }]) {
    assert.equal(await sessions.load({ ...identity, ...changed }), null);
  }
  await prisma.maxChatSession.update({ where: { botUsername_maxUserId: { botUsername: username, maxUserId: "123" } },
    data: { expiresAt: new Date(0) } });
  assert.equal(await sessions.load(identity), null);
  await sessions.clear("123");
  assert.equal(await prisma.maxChatSession.count({ where: { botUsername: username } }), 0);
});
