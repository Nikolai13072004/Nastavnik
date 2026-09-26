import assert from "node:assert/strict";
import test from "node:test";
import { retryBotClaim } from "./retry-bot-claim";

test("a transient database error retries acquisition with bounded backoff", async () => {
  let calls = 0;
  const waits: number[] = [];
  const codes: string[] = [];
  const job = { eventKey: "event", maxUserId: "123" };
  const result = await retryBotClaim(async () => {
    calls++;
    if (calls < 3) throw { code: "P2028", message: "private details" };
    return job;
  }, async (milliseconds) => { waits.push(milliseconds); }, (code) => { codes.push(code); });
  assert.equal(result, job);
  assert.deepEqual(waits, [500, 1000]);
  assert.deepEqual(codes, ["P2028", "P2028"]);
});

test("a lost claim response does not resend a guarded SENDING job", async () => {
  let claimed = false;
  const result = await retryBotClaim(async () => {
    if (claimed) return null;
    claimed = true;
    throw { code: "P1017" };
  }, async () => {}, () => {});
  assert.equal(result, null);
});

test("unrecognized and persistent errors stop rather than looping forever", async () => {
  let calls = 0;
  await assert.rejects(retryBotClaim(async () => {
    calls++;
    throw new Error("state conflict");
  }, async () => {}, () => {}), /state conflict/);
  assert.equal(calls, 1);
  calls = 0;
  await assert.rejects(retryBotClaim(async () => {
    calls++;
    throw Object.assign(new Error("database unavailable"), { code: "P1001" });
  }, async () => {}, () => {}), /database unavailable/);
  assert.equal(calls, 3);
});
