import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { test } from "node:test";
import { verifyMaxInitData } from "./verify-init-data";
import { handleMaxIdentity } from "./identity-handler";

const token = "test-only-max-token";
const now = Math.floor(Date.now() / 1000);

function signed(overrides: Record<string, string> = {}) {
  const values = {
    auth_date: String(now),
    user: JSON.stringify({ id: 123456, first_name: "Анна + 100%" }),
    ...overrides,
  };
  const data = Object.entries(values).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
    .map(([key, value]) => `${key}=${value}`).join("\n");
  const secret = createHmac("sha256", "WebAppData").update(token).digest();
  const hash = createHmac("sha256", secret).update(data).digest("hex");
  return new URLSearchParams({ ...values, hash }).toString();
}

test("verifies MAX signature and decodes Unicode/percent/plus exactly once", () => {
  assert.deepEqual(verifyMaxInitData(signed(), token, now), { id: "123456", firstName: "Анна + 100%" });
});

test("rejects tampering, wrong bot token, missing or malformed hash", () => {
  const valid = signed();
  for (const invalid of [valid.replace("123456", "123457"), valid.replace(/hash=.*/, "hash=bad"), valid.replace(/&hash=.*/, "")]) {
    assert.equal(verifyMaxInitData(invalid, token, now), null);
  }
  assert.equal(verifyMaxInitData(valid, "different-bot", now), null);
  assert.equal(verifyMaxInitData(valid, "", now), null);
});

test("rejects duplicate parameters including encoded names", () => {
  for (const extra of ["&hash=" + "0".repeat(64), "&user={}", "&%75ser={}", "&auth_date=1"]) {
    assert.equal(verifyMaxInitData(signed() + extra, token, now), null);
  }
});

test("requires a recent integer timestamp with bounded future clock skew", () => {
  for (const auth_date of [String(now - 3601), String(now + 31), "NaN", "", "1.5", "9007199254740993"]) {
    assert.equal(verifyMaxInitData(signed({ auth_date }), token, now), null);
  }
  for (const auth_date of [String(now - 3600), String(now + 30)]) {
    assert.ok(verifyMaxInitData(signed({ auth_date }), token, now));
  }
});

test("requires a safe positive user ID, never trusts client roles", () => {
  for (const user of ["null", "{}", "not json", "[]", '{"id":0}', '{"id":-1}', '{"id":1.5}', '{"id":"123"}', '{"id":9007199254740993}']) {
    assert.equal(verifyMaxInitData(signed({ user }), token, now), null);
  }
  assert.deepEqual(verifyMaxInitData(signed({ user: '{"id":42,"role":"ADMIN","orgId":"other"}' }), token, now), { id: "42", firstName: "" });
});

test("rejects oversized initData", () => {
  assert.equal(verifyMaxInitData(signed({ extra: "x".repeat(16_384) }), token, now), null);
});

function request(body: string, contentType = "application/json") {
  return new Request("http://localhost/api/max/identity", { method: "POST", headers: { "Content-Type": contentType }, body });
}

test("identity endpoint issues no session, permissions, or raw launch data", async () => {
  const response = await handleMaxIdentity(request(JSON.stringify({ initData: signed(), role: "ADMIN" })), token);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("set-cookie"), null);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.deepEqual(await response.json(), { status: "identity_verified", firstName: "Анна + 100%", employeeAccess: false });
});

test("identity endpoint fails closed when unconfigured and handles invalid requests", async () => {
  const cases: [string, string, string | undefined, number][] = [
    ["{}", "application/json", undefined, 503],
    ["{}", "text/plain", token, 415],
    ["{", "application/json", token, 400],
    ["null", "application/json", token, 400],
    ['{"initData":12}', "application/json", token, 400],
    ['{"initData":"forged"}', "application/json", token, 401],
    ["x".repeat(20_481), "application/json", token, 413],
  ];
  for (const [body, contentType, configuredToken, expectedStatus] of cases) {
    const response = await handleMaxIdentity(request(body, contentType), configuredToken);
    assert.equal(response.status, expectedStatus);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.ok(!(await response.text()).includes(token));
  }
});

test("session issuance requires both a valid MAX launch and an active linked employee", async () => {
  let issued = 0;
  const linking = {
    accept: async () => assert.fail("unexpected link mutation"),
    findEmployee: async () => null as { name: string; organizationName: string } | null,
    issueSession: async () => {
      issued++;
      return { token: "test-session", expiresAt: "2026-09-23T00:00:00Z" };
    },
  };
  await handleMaxIdentity(request(JSON.stringify({ initData: "forged" })), token, linking);
  const unlinked = await handleMaxIdentity(request(JSON.stringify({ initData: signed() })), token, linking);
  assert.equal((await unlinked.json()).session, null);
  assert.equal(issued, 0);
  linking.findEmployee = async () => ({ name: "Employee", organizationName: "Company" });
  const linked = await handleMaxIdentity(request(JSON.stringify({ initData: signed() })), token, linking);
  const data = await linked.json();
  assert.equal(data.employeeAccess, true);
  assert.equal(data.session.token, "test-session");
  assert.equal(issued, 1);
  assert.equal(linked.headers.get("set-cookie"), null);
});
