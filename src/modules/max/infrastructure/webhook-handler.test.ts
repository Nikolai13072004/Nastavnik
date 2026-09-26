import assert from "node:assert/strict";
import { test } from "node:test";
import { handleMaxWebhook } from "./webhook-handler";
import { parseBotStart, parseBotEvent, deliverNextBotMessage, type BotDeliveryRepository, type DeliveryOutcome } from "../application/bot-delivery";

const now = 1_790_000_000_000;
const config = { secret: "a".repeat(43), botUsername: "example_bot" };
const event = { update_type: "bot_started", timestamp: now, chat_id: 55, user: { user_id: 123 } };
const noEnqueue = { enqueue: async () => assert.fail("must not enqueue") };

function request(body: unknown = event, headers: HeadersInit = {}) {
  return new Request("http://localhost/api/max/webhook", {
    method: "POST", headers: { "Content-Type": "application/json", "X-Max-Bot-Api-Secret": config.secret, ...headers },
    body: JSON.stringify(body),
  });
}

test("webhook rejects unconfigured, missing or wrong secrets, regardless of LMS cookies", async () => {
  for (const settings of [{}, { ...config, secret: "short" }, { ...config, botUsername: "https://evil" }]) {
    assert.equal((await handleMaxWebhook(request(), settings, noEnqueue, now)).status, 503);
  }
  for (const secret of ["", "b".repeat(43), `${config.secret},other`]) {
    const response = await handleMaxWebhook(request(event, {
      "X-Max-Bot-Api-Secret": secret, Cookie: "authjs.session-token=admin",
    }), config, noEnqueue, now);
    assert.equal(response.status, 401);
    assert.equal(response.headers.get("cache-control"), "no-store");
  }
});

test("webhook only acknowledges successful durable persistence, strips unused private fields", async () => {
  let stored = false;
  const response = await handleMaxWebhook(request({ ...event, payload: "private-invitation", user: { ...event.user, name: "private-name" } }), config, {
    enqueue: async (bot, received) => {
      assert.equal(bot, "example_bot");
      assert.deepEqual(received, { userId: 123, chatId: 55, timestamp: now });
      stored = true;
    },
  }, now);
  assert.equal(stored, true);
  assert.equal(response.status, 200);
  const failure = await handleMaxWebhook(request(), config, { enqueue: async () => { throw new Error("database-secret"); } }, now);
  assert.equal(failure.status, 503);
  assert.ok(!(await failure.text()).includes("database-secret"));
});

test("webhook handles invalid JSON, media type and oversized chunked input", async () => {
  assert.equal((await handleMaxWebhook(request(event, { "Content-Type": "text/plain" }), config, noEnqueue, now)).status, 415);
  assert.equal((await handleMaxWebhook(request({ ...event, payload: "x".repeat(20500) }), config, noEnqueue, now)).status, 413);
  const invalid = new Request("http://localhost", { method: "POST", headers: request().headers, body: "{" });
  assert.equal((await handleMaxWebhook(invalid, config, noEnqueue, now)).status, 400);
  assert.equal((await handleMaxWebhook(request({}), config, noEnqueue, now)).status, 400);
});

test("event parsing ignores unsupported, stale and future events, rejects unsafe IDs", async () => {
  for (const data of [{ update_type: "message_created" }, { ...event, timestamp: now - 86_400_001 },
    { ...event, timestamp: now + 60_001 }, { ...event, user: { user_id: 123, is_bot: true } }]) {
    assert.equal(parseBotStart(data, now), "ignored");
    assert.equal((await handleMaxWebhook(request(data), config, noEnqueue, now)).status, 200);
  }
  for (const data of [null, [], { ...event, timestamp: "123" }, { ...event, chat_id: 2 ** 53 },
    { ...event, user: { user_id: -1 } }, { ...event, user: { user_id: 2 ** 53 } }]) {
    assert.equal(parseBotStart(data, now), null);
  }
  assert.notEqual(parseBotStart({ ...event, timestamp: now - 8 * 3600_000 }, now), "ignored");
});

test("worker records success, uncertainty without retry, and propagates DB failures", async () => {
  const outcomes: DeliveryOutcome[] = [];
  const repo: BotDeliveryRepository = {
    enqueue: async () => undefined,
    claim: async () => ({ eventKey: "one", maxUserId: "123" }),
    finish: async (_key, outcome) => { outcomes.push(outcome); },
  };
  assert.equal(await deliverNextBotMessage(repo, "example_bot", async () => "mid"), "sent");
  let sends = 0;
  assert.equal(await deliverNextBotMessage(repo, "example_bot", async () => {
    sends++;
    throw new Error("private-data");
  }), "uncertain");
  assert.equal(sends, 1);
  assert.deepEqual(outcomes, [{ status: "SENT", messageId: "mid" }, { status: "UNCERTAIN", errorCode: "SEND_NOT_CONFIRMED" }]);
  assert.equal(await deliverNextBotMessage({ ...repo, claim: async () => null }, "example_bot", async () => assert.fail()), "idle");
  await assert.rejects(deliverNextBotMessage({ ...repo, finish: async () => { throw new Error("db-unavailable"); } }, "example_bot", async () => "mid"));
});

const textEvent = {
  update_type: "message_created", timestamp: now,
  message: {
    sender: { user_id: 123, is_bot: false, name: "private-name" },
    recipient: { chat_type: "dialog", chat_id: 55 },
    body: { mid: "message-id", text: "private-code" },
  },
};

test("private text queues generic help without retaining text, codes or sender names", async () => {
  assert.deepEqual(parseBotEvent(textEvent, now), { kind: "HELP", userId: 123, timestamp: now });
  const response = await handleMaxWebhook(request(textEvent), config, {
    enqueue: async (_bot, received) => {
      assert.deepEqual(received, { kind: "HELP", userId: 123, timestamp: now });
      assert.ok(!JSON.stringify(received).includes("private-"));
    },
  }, now);
  assert.equal(response.status, 200);
});

test("help ignores groups, channels, bot messages, attachments and stale or unsafe events", () => {
  for (const message of [
    { ...textEvent.message, sender: { user_id: 123, is_bot: true } },
    { ...textEvent.message, sender: { user_id: 2 ** 53, is_bot: false } },
    { ...textEvent.message, sender: { user_id: 123 } },
    { ...textEvent.message, recipient: { chat_type: "chat" } },
    { ...textEvent.message, recipient: { chat_type: "channel" } },
    { ...textEvent.message, body: { attachments: [] } },
    { ...textEvent.message, body: { text: " " } },
  ]) assert.equal(parseBotEvent({ ...textEvent, message }, now), "ignored");
  for (const timestamp of [now - 86_400_001, now + 60_001, 2 ** 53, "123"]) {
    assert.equal(parseBotEvent({ ...textEvent, timestamp }, now), "ignored");
  }
});

test("help delivery uses its own sender and remains uncertain without automatic retry", async () => {
  const outcomes: DeliveryOutcome[] = [];
  const repo: BotDeliveryRepository = {
    enqueue: async () => undefined,
    claim: async () => ({ eventKey: "help", maxUserId: "123", kind: "HELP" }),
    finish: async (_key, outcome) => { outcomes.push(outcome); },
  };
  const forbidden = async () => assert.fail("must not send welcome or revision");
  assert.equal(await deliverNextBotMessage(repo, "example_bot", forbidden, forbidden, async (id, bot) => {
    assert.equal(id, 123);
    assert.equal(bot, "example_bot");
    return "help-mid";
  }), "sent");
  assert.equal(await deliverNextBotMessage(repo, "example_bot", forbidden, forbidden, async () => {
    throw new Error("private upstream body");
  }), "uncertain");
  assert.deepEqual(outcomes, [
    { status: "SENT", messageId: "help-mid" },
    { status: "UNCERTAIN", errorCode: "SEND_NOT_CONFIRMED" },
  ]);
});
