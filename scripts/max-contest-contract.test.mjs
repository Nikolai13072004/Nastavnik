import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const Ajv = require("ajv");
const yaml = require("js-yaml");
const contract = JSON.parse(readFileSync(new URL("../docs/contest/openapi.json", import.meta.url), "utf8"));
const checks = yaml.load(readFileSync(new URL("../docs/contest/DATA-API.yaml", import.meta.url), "utf8"));
const fixtures = JSON.parse(readFileSync(new URL("../docs/contest/test-data.json", import.meta.url), "utf8"));
const validator = new Ajv({ nullable: true, allErrors: true });
validator.addSchema(contract, "max-contest-api");

test("the contest contract describes existing public MAX methods with current session guards", () => {
  assert.equal(contract.openapi, "3.0.3");
  assert.equal(contract.servers[0].url, checks.base_url);
  assert.ok(checks.base_url.startsWith("https://"));
  assert.deepEqual(contract.security, [{ maxSession: [] }]);
  const operationIds = new Set();
  for (const [path, operations] of Object.entries(contract.paths)) {
    const route = readFileSync(new URL(`../src/app/api/max${path}/route.ts`, import.meta.url), "utf8");
    for (const [method, operation] of Object.entries(operations)) {
      assert.match(route, new RegExp(`export async function ${method.toUpperCase()}\\(`));
      assert.ok(operation.operationId && !operationIds.has(operation.operationId));
      operationIds.add(operation.operationId);
      assert.ok(operation.responses["401"]);
      assert.ok(operation.responses["200"] || operation.responses["201"]);
      assert.deepEqual(operation.security ?? contract.security, path === "/identity" ? [] : contract.security);
    }
  }
  for (const name of Object.keys(contract.components.schemas)) {
    assert.ok(validator.getSchema(`max-contest-api#/components/schemas/${name}`));
  }
});

test("DATA-API checks use documented operations and valid response schemas without credentials", () => {
  assert.equal(checks.schema_version, "1.0");
  const ids = new Set();
  for (const check of checks.checks) {
    assert.ok(check.id && !ids.has(check.id));
    ids.add(check.id);
    const operation = contract.paths[check.path]?.[check.method.toLowerCase()];
    assert.ok(operation);
    assert.ok(["anonymous", "learner", "hr"].includes(check.role));
    assert.equal(check.response.content_type, "application/json");
    assert.ok(validator.getSchema(`max-contest-api${check.response.schema}`));
    for (const status of check.expected_status_codes) {
      assert.ok(operation.responses[String(status)]);
    }
    for (const field of check.response.required_fields) {
      const schema = contract.components.schemas[check.response.schema.split("/").at(-1)];
      assert.ok(schema.required.includes(field));
    }
    if (check.role !== "anonymous") {
      assert.match(check.headers.Authorization, /^Bearer \$\{(LEARNER|HR)_SESSION\}$/);
    }
  }
  assert.equal(fixtures.synthetic, true);
  assert.equal(fixtures.courseId, checks.variables.COURSE_ID);
  assert.equal(fixtures.documentId, checks.variables.DOCUMENT_ID);
  assert.equal(Object.keys(fixtures.quizAnswersByPrompt).length, 3);
});

test("API schemas accept explicit refusal and nullable expiry but reject incomplete results", () => {
  const courseList = validator.getSchema("max-contest-api#/components/schemas/Courses");
  assert.equal(courseList({ courses: [{ id: fixtures.courseId, title: "Demo", expiresAt: null }] }), true);
  assert.equal(courseList({ courses: [{ id: fixtures.courseId }] }), false);
  const answer = validator.getSchema("max-contest-api#/components/schemas/KnowledgeAnswer");
  assert.equal(answer({ answer: "Нет подтверждённого ответа.", refused: true, sources: [] }), true);
  assert.equal(answer({ answer: "Ответ без обязательного списка источников.", refused: false }), false);
  const source = validator.getSchema("max-contest-api#/components/schemas/DocumentDetail");
  assert.equal(source({ document: { id: fixtures.documentId, title: "Demo", sourceName: "example.md",
    contentText: "Demo", contentHash: fixtures.documentSha256, hasOriginal: false } }), true);
  assert.equal(source({ document: { id: fixtures.documentId, contentText: "Demo" } }), false);
});
