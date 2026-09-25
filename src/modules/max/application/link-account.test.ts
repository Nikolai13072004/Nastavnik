import assert from "node:assert/strict";
import { test } from "node:test";
import { createMaxAccountLinking, MaxLinkError, type MaxLinkRepository } from "./link-account";

test("issues a 15-minute code while storing only its hash", async () => {
  const now = new Date("2026-09-23T00:00:00Z");
  const repository: MaxLinkRepository = {
    issue: async (input) => {
      assert.deepEqual(input, { userId: "employee", tokenHash: "hashed", now, expiresAt: new Date("2026-09-23T00:15:00Z") });
    },
    accept: async () => assert.fail("unexpected acceptance"),
    findEmployee: async () => null,
  };
  const linking = createMaxAccountLinking({ repository, now: () => now, createToken: () => "secret", hashToken: () => "hashed" });
  assert.deepEqual(await linking.issue("employee"), { token: "secret", expiresAt: "2026-09-23T00:15:00.000Z" });
});

test("rejects malformed codes before touching the repository", async () => {
  const repository: MaxLinkRepository = {
    issue: async () => assert.fail("unexpected issue"),
    accept: async () => assert.fail("unexpected acceptance"),
    findEmployee: async () => null,
  };
  const linking = createMaxAccountLinking({ repository, now: () => new Date(), createToken: () => "", hashToken: () => "" });
  for (const token of ["", "short", "x".repeat(33), "!".repeat(32)]) {
    await assert.rejects(linking.accept(token, "123"), (error) => error instanceof MaxLinkError && error.code === "INVALID_INVITE");
  }
});
