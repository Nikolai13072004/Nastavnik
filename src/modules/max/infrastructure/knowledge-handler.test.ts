import assert from "node:assert/strict";
import test from "node:test";
import type { MaxLearnerIdentity } from "../application/list-courses";
import { createMaxSessionCodec } from "./learner-session";
import { handleMaxKnowledgeRequest } from "./knowledge-handler";

const botToken = "test-max-bot-token";
const identity: MaxLearnerIdentity = {
  maxUserId: "max-1", userId: "user-1", organizationId: "org-1", linkedAt: "2026-09-24T00:00:00Z",
};
const session = createMaxSessionCodec(botToken).issue(identity).token;

function request(body: unknown, authorization = `Bearer ${session}`) {
  return new Request("https://example.test/api/max/knowledge", {
    method: "POST",
    headers: { Authorization: authorization, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

test("closed route never checks a session or calls the service", async () => {
  const response = await handleMaxKnowledgeRequest(request({ courseId: "course-1", question: "Кто утверждает?" }),
    botToken, false, async () => { throw new Error("must not call"); });
  assert.equal(response.status, 404);
  assert.equal(response.headers.get("Cache-Control"), "no-store");
});

test("requires a MAX bearer session and valid request before calling Vedomo", async () => {
  let calls = 0;
  const ask = async () => { calls++; return null; };
  assert.equal((await handleMaxKnowledgeRequest(request({ courseId: "course-1", question: "Кто утверждает?" }, ""),
    botToken, true, ask)).status, 401);
  assert.equal((await handleMaxKnowledgeRequest(request({ courseId: "course-1", question: "x" }),
    botToken, true, ask)).status, 400);
  assert.equal((await handleMaxKnowledgeRequest(request({ courseId: "../other", question: "Кто утверждает?" }),
    botToken, true, ask)).status, 400);
  assert.equal(calls, 0);
});

test("returns a verified answer without caching or exposing service internals", async () => {
  const answer = { answer: "Утверждает сотрудник.", refused: false, sources: [{
    documentId: "doc-1", documentHash: "hash-1", title: "Правило", section: "",
    pageStart: null, pageEnd: null, snippet: "Утверждает сотрудник.",
  }] };
  const response = await handleMaxKnowledgeRequest(
    request({ courseId: "course-1", question: " Кто утверждает? " }),
    botToken,
    true,
    async (current, courseId, question) => {
      assert.deepEqual(current, identity);
      assert.equal(courseId, "course-1");
      assert.equal(question, "Кто утверждает?");
      return answer;
    },
  );
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.deepEqual(await response.json(), answer);
});

test("missing authorization and upstream failures fail closed", async () => {
  const input = { courseId: "course-1", question: "Кто утверждает?" };
  assert.equal((await handleMaxKnowledgeRequest(request(input), botToken, true, async () => null)).status, 403);
  const response = await handleMaxKnowledgeRequest(request(input), botToken, true,
    async () => { throw new Error("secret upstream response"); });
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "TEMPORARILY_UNAVAILABLE" });
});
