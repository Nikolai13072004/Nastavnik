import assert from "node:assert/strict";
import test from "node:test";
import { handleKnowledgeConnection } from "./knowledge-connection-handler";
import { createMaxSessionCodec } from "./learner-session";

const botToken = "connection-test-bot-token";
const identity = { maxUserId: "max-1", userId: "hr-1", organizationId: "org-1", linkedAt: "2026-09-26T00:00:00Z" };
const token = createMaxSessionCodec(botToken).issue(identity).token;
const input = { courseId: "course-1", documentId: "document-1" };

function request(body: unknown = input, authorization = `Bearer ${token}`) {
  return new Request("https://example.test/api/max/documents/knowledge", {
    method: "POST",
    headers: { Authorization: authorization, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

test("closed integration and unauthenticated requests never call the connector", async () => {
  const connect = async (): Promise<never> => { throw new Error("must not connect"); };
  assert.equal((await handleKnowledgeConnection(request(), botToken, false, connect)).status, 404);
  assert.equal((await handleKnowledgeConnection(request(input, ""), botToken, true, connect)).status, 401);
  assert.equal((await handleKnowledgeConnection(request(input, "Bearer wrong"), botToken, true, connect)).status, 401);
});

test("rejects invalid IDs and client supplied hash or organization", async () => {
  for (const body of [{ ...input, courseId: "../foreign" }, { ...input, documentId: "" },
    { ...input, organizationId: "foreign" }, { ...input, documentHash: "a".repeat(64) }, [], null]) {
    const response = await handleKnowledgeConnection(request(body), botToken, true,
      async () => { throw new Error("must not connect"); });
    assert.equal(response.status, 400);
  }
});

test("bounds streamed request bodies and rejects non JSON", async () => {
  const oversized = request({ ...input, padding: "x".repeat(3000) });
  const connect = async (): Promise<never> => { throw new Error("must not connect"); };
  assert.equal((await handleKnowledgeConnection(oversized, botToken, true, connect)).status, 400);
  const wrongType = request();
  wrongType.headers.set("Content-Type", "text/plain");
  assert.equal((await handleKnowledgeConnection(wrongType, botToken, true, connect)).status, 415);
});

test("returns only connection status and uses identity from the signed session", async () => {
  const response = await handleKnowledgeConnection(request(), botToken, true, async (...args) => {
    assert.deepEqual(args, [identity, "course-1", "document-1"]);
    return "APPROVED";
  });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.equal(response.headers.get("Vary"), "Authorization");
  assert.deepEqual(await response.json(), { status: "APPROVED" });
});

test("maps denied, unsupported and stale documents to explicit failures", async () => {
  for (const [result, status] of [["FORBIDDEN", 403], ["NOT_FOUND", 404], ["UNSUPPORTED_FORMAT", 400],
    ["NOT_APPROVED", 409], ["HASH_MISMATCH", 409], ["CONFLICT", 409], ["SOURCE_NOT_READY", 409]] as const) {
    const response = await handleKnowledgeConnection(request(), botToken, true, async () => result);
    assert.equal(response.status, status);
    assert.deepEqual(await response.json(), { status: result });
  }
});

test("malformed JSON and upstream failures never expose service secrets", async () => {
  const malformed = new Request("https://example.test", {
    method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: "{",
  });
  assert.equal((await handleKnowledgeConnection(malformed, botToken, true, async () => "APPROVED")).status, 400);
  const response = await handleKnowledgeConnection(request(), botToken, true,
    async () => { throw new Error("secret upstream token"); });
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "TEMPORARILY_UNAVAILABLE" });
});

test("returns pending without approving a partial index and accepts explicit retry only", async () => {
  const response = await handleKnowledgeConnection(request({ ...input, retry: true }), botToken, true,
    async (...args) => {
      assert.deepEqual(args, [identity, "course-1", "document-1", true]);
      return "PROCESSING";
    });
  assert.equal(response.status, 202);
  assert.deepEqual(await response.json(), { status: "PROCESSING" });
  const invalid = await handleKnowledgeConnection(request({ ...input, retry: "yes" }), botToken, true,
    async () => { throw new Error("must not connect"); });
  assert.equal(invalid.status, 400);
});
