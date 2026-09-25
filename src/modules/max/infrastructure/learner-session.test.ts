import assert from "node:assert/strict";
import { test } from "node:test";
import { createMaxSessionCodec } from "./learner-session";
import { handleMaxCourses } from "./courses-handler";
import { createListMaxCourses } from "../application/list-courses";

const identity = { maxUserId: "123", userId: "employee", organizationId: "org", linkedAt: "2026-09-23T00:00:00Z" };
const codec = createMaxSessionCodec("test-only-bot");

test("learner session roundtrip, expiration boundary, clock skew and key rotation", () => {
  const session = codec.issue(identity, 1000);
  assert.deepEqual(codec.verify(session.token, 1000), identity);
  assert.ok(codec.verify(session.token, 1899));
  assert.equal(codec.verify(session.token, 1900), null);
  assert.equal(codec.verify(session.token, 969), null);
  assert.equal(createMaxSessionCodec("rotated-bot").verify(session.token, 1000), null);
  assert.throws(() => createMaxSessionCodec(""));
});

test("learner sessions reject modified claims, truncation, huge data and launch signatures", () => {
  const session = codec.issue(identity, 1000);
  const [payload, signature] = session.token.split(".");
  const changed = Buffer.from(JSON.stringify({ ...identity, userId: "administrator" })).toString("base64url");
  for (const token of [`${changed}.${signature}`, `${payload}.invalid`, session.token + ".extra", "x".repeat(4097), "auth_date=1000&hash=aaa"]) {
    assert.equal(codec.verify(token, 1000), null);
  }
});

test("HTTP course endpoint does not accept LMS cookies and never queries with invalid session", async () => {
  const cases: HeadersInit[] = [{}, { Cookie: "authjs.session-token=fake" }, { Authorization: "Bearer forged" }];
  for (const headers of cases) {
    const response = await handleMaxCourses(new Request("http://localhost/api/max/courses", { headers }), "test-only-bot", async () => assert.fail("must not query"));
    assert.equal(response.status, 401);
    assert.equal(response.headers.get("cache-control"), "no-store");
  }
});

test("HTTP handles revoked access, service failures and valid sessions without leaking errors", async () => {
  const request = () => new Request("http://localhost/api/max/courses", { headers: { Authorization: `Bearer ${codec.issue(identity).token}` } });
  assert.equal((await handleMaxCourses(request(), undefined, async () => [])).status, 503);
  assert.equal((await handleMaxCourses(request(), "test-only-bot", async () => null)).status, 401);
  const failure = await handleMaxCourses(request(), "test-only-bot", async () => { throw new Error("private connection data"); });
  assert.equal(failure.status, 503);
  assert.ok(!(await failure.text()).includes("private connection data"));
  const success = await handleMaxCourses(request(), "test-only-bot", async (received) => {
    assert.deepEqual(received, identity);
    return [];
  });
  assert.deepEqual(await success.json(), { courses: [] });
});

test("stale link, organization or user invalidates the session before course query", async () => {
  for (const changed of [null, { ...identity, organizationId: "other" }, { ...identity, userId: "other" }, { ...identity, linkedAt: "new-link" }]) {
    const list = createListMaxCourses({ findIdentity: async () => changed, findAssignedCourses: async () => assert.fail("must not query") });
    assert.equal(await list(identity), null);
  }
});

test("course projection uses enrollment priority, not an OR of active assignments", async () => {
  const list = createListMaxCourses({
    findIdentity: async () => identity,
    findAssignedCourses: async (userId, organizationId) => {
      assert.equal(userId, identity.userId);
      assert.equal(organizationId, identity.organizationId);
      return [
        { id: "direct", title: "Direct", directExpiries: [null], groupExpiries: [] },
        { id: "group", title: "Group", directExpiries: [], groupExpiries: [null] },
        { id: "expired-direct", title: "Hidden", directExpiries: [new Date(0)], groupExpiries: [null] },
        { id: "expired-group", title: "Hidden", directExpiries: [], groupExpiries: [new Date(0)] },
        { id: "unassigned", title: "Hidden", directExpiries: [], groupExpiries: [] },
      ];
    },
  });
  assert.deepEqual((await list(identity))?.map((course) => course.id), ["direct", "group"]);
});
