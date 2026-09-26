import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { unstable_doesMiddlewareMatch as doesProxyMatch } from "next/experimental/testing/server";

// Read the literal config without initializing NextAuth and its database adapter.
const source = readFileSync(new URL("./proxy.ts", import.meta.url), "utf8");
const literal = source.match(/matcher:\s*(\[[^\n]+\])/);
assert.ok(literal, "Proxy must retain a statically analyzable matcher");
const config = { matcher: JSON.parse(literal[1]) as string[] };

test("MAX routes reach their own session guards without an LMS login", () => {
  for (const url of ["/max", "/api/max/identity", "/api/max/courses", "/api/max/documents",
    "/api/max/documents/knowledge", "/api/max/documents/knowledge/"]) {
    assert.equal(doesProxyMatch({ config, nextConfig: {}, url }), false, url);
  }
});

test("unknown MAX paths and LMS invitation issuance retain the LMS guard", () => {
  for (const url of ["/connect-max", "/api/max/invitation", "/api/max/documents/knowledge/extra",
    "/api/max/documents-extra", "/max/extra", "/admin"]) {
    assert.equal(doesProxyMatch({ config, nextConfig: {}, url }), true, url);
  }
});
