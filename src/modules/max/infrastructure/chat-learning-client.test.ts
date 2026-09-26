import assert from "node:assert/strict";
import { test } from "node:test";
import { createChatLearningClient } from "./chat-learning-client";
import { createMaxSessionCodec } from "./learner-session";
import { ChatLearningError } from "../application/chat";

const identity = { maxUserId: "123", userId: "learner", organizationId: "org", linkedAt: new Date(0).toISOString() };
test("chat uses the shared authenticated assessment endpoint once, without exposing credentials in URL", async () => {
  let calls = 0;
  const client = createChatLearningClient("fixture-secret", "http://web:3000", async (url, init) => {
    calls++;
    assert.equal(String(url), "http://web:3000/api/max/quiz");
    const token = new Headers(init?.headers).get("Authorization")!.slice(7);
    assert.deepEqual(createMaxSessionCodec("fixture-secret").verify(token), identity);
    assert.equal(init?.redirect, "error");
    assert.equal(init?.cache, "no-store");
    assert.deepEqual(JSON.parse(String(init?.body)), { action: "start", courseId: "course", quizId: "quiz" });
    return Response.json({ error: "MATERIAL_REQUIRED" }, { status: 403 });
  });
  await assert.rejects(client.start(identity, "course", "quiz"), (error) => error instanceof ChatLearningError && error.code === "MATERIAL_REQUIRED");
  assert.equal(calls, 1);
});
test("internal transport errors never retry a write or reveal response details", async () => {
  let calls = 0;
  const client = createChatLearningClient("secret", "http://web:3000", async () => { calls++; throw new Error("private-details"); });
  await assert.rejects(client.start(identity, "course", "quiz"), (error) => error instanceof ChatLearningError && !error.message.includes("private"));
  assert.equal(calls, 1);
  assert.throws(() => createChatLearningClient("secret", "https://user:password@example.com"));
});
