import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { after, test } from "node:test";
import prisma from "@/lib/prisma";
import { prismaBotDeliveryRepository as repository } from "./prisma-bot-delivery-repository";
import { deliverNextBotMessage } from "../application/bot-delivery";
import { handleMaxWebhook } from "./webhook-handler";

const bots: string[] = [];
function bot() {
  const username = `test_${randomUUID().replaceAll("-", "")}_bot`;
  bots.push(username);
  return username;
}
const event = { userId: 123, chatId: 55, timestamp: Date.now() };

after(async () => {
  await prisma.maxBotDelivery.deleteMany({ where: { botUsername: { in: bots } } });
  await prisma.$disconnect();
});

test("concurrent redelivery persists one job and only the minimal event projection", async () => {
  const username = bot();
  await Promise.all(Array.from({ length: 8 }, () => repository.enqueue(username, event)));
  const rows = await prisma.maxBotDelivery.findMany({ where: { botUsername: username } });
  assert.equal(rows.length, 1);
  assert.equal(rows[0].maxUserId, "123");
  assert.equal(rows[0].status, "PENDING");
  assert.equal(rows[0].eventKey.length, 64);
});

test("concurrent workers claim only one job; SENDING blocks even after a worker restart", async () => {
  const username = bot();
  await repository.enqueue(username, event);
  await repository.enqueue(username, { ...event, timestamp: event.timestamp + 1 });
  const claims = await Promise.all(Array.from({ length: 5 }, () => repository.claim(username)));
  const claimed = claims.filter((value) => value !== null);
  assert.equal(claimed.length, 1);
  assert.equal(await repository.claim(username), null);
  assert.equal(await prisma.maxBotDelivery.count({ where: { botUsername: username, status: "SENDING" } }), 1);
  await repository.finish(claimed[0]!.eventKey, { status: "SENT", messageId: "fixture-mid" });
  assert.equal(await repository.claim(username), null, "cooldown also applies to a different worker");
  await prisma.maxBotDelivery.update({ where: { eventKey: claimed[0]!.eventKey }, data: { finishedAt: new Date(Date.now() - 5000) } });
  assert.ok(await repository.claim(username));
});

test("completed and uncertain deliveries never requeue on repeated webhook delivery", async () => {
  for (const fails of [false, true]) {
    const username = bot();
    await repository.enqueue(username, event);
    let sends = 0;
    const send = async () => {
      sends++;
      if (fails) throw new Error("secret upstream details");
      return "fixture-mid";
    };
    assert.equal(await deliverNextBotMessage(repository, username, send), fails ? "uncertain" : "sent");
    await repository.enqueue(username, event);
    assert.equal(await deliverNextBotMessage(repository, username, send), "idle");
    assert.equal(sends, 1);
    const job = await prisma.maxBotDelivery.findFirstOrThrow({ where: { botUsername: username } });
    assert.equal(job.status, fails ? "UNCERTAIN" : "SENT");
    assert.ok(!JSON.stringify(job).includes("secret upstream"));
  }
});

test("event identity includes bot, user, chat and event time; another bot can progress independently", async () => {
  const first = bot();
  const second = bot();
  await repository.enqueue(first, event);
  await repository.enqueue(first, { ...event, userId: 456 });
  await repository.enqueue(first, { ...event, chatId: 77 });
  await repository.enqueue(first, { ...event, timestamp: event.timestamp + 1 });
  await repository.enqueue(second, event);
  assert.equal(await prisma.maxBotDelivery.count({ where: { botUsername: first } }), 4);
  assert.ok(await repository.claim(first));
  assert.ok(await repository.claim(second));
});

test("authenticated HTTP receipt -> durable queue -> welcome -> duplicate acknowledgement", async () => {
  const username = bot();
  const config = { secret: "test-only-secret-".repeat(3), botUsername: username };
  const request = () => new Request("http://localhost/api/max/webhook", {
    method: "POST", headers: { "Content-Type": "application/json", "X-Max-Bot-Api-Secret": config.secret },
    body: JSON.stringify({ update_type: "bot_started", timestamp: event.timestamp, chat_id: 55, user: { user_id: 123 }, payload: "private-code" }),
  });
  assert.equal((await handleMaxWebhook(request(), config, repository)).status, 200);
  assert.equal(await deliverNextBotMessage(repository, username, async (id, targetBot) => {
    assert.equal(id, 123);
    assert.equal(targetBot, username);
    return "fixture-mid";
  }), "sent");
  assert.equal((await handleMaxWebhook(request(), config, repository)).status, 200);
  assert.equal(await prisma.maxBotDelivery.count({ where: { botUsername: username } }), 1);
});
