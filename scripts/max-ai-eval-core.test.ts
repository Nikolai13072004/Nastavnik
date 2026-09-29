import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { DEMO_CASES, DEMO_DOCUMENT_HASH, screenEvalAnswer, validateDemoDocument } from "./max-ai-eval-core";

const documentId = "vedomo-1";
const courseDocumentId = "course-1";
const documentText = "Код нельзя пересылать другому человеку.";
const answer = {
  answer: "Нет, код нельзя пересылать другому человеку.",
  refused: false,
  sources: [{ documentId, documentHash: DEMO_DOCUMENT_HASH, courseDocumentId, snippet: documentText }],
};

test("demo cases have unique IDs and a meaningful mix", () => {
  assert.equal(new Set(DEMO_CASES.map((item) => item.id)).size, DEMO_CASES.length);
  assert.ok(DEMO_CASES.some((item) => item.expected === "answer"));
  assert.ok(DEMO_CASES.some((item) => item.expected === "refusal"));
  for (const item of DEMO_CASES) {
    assert.ok(item.expected === "refusal" || (item.evidence?.length && item.answerSignals?.length));
  }
});

test("source check rejects wrong edition, wrong document and unsupported quote", () => {
  const item = DEMO_CASES.find((candidate) => candidate.id === "share-code")!;
  assert.deepEqual(screenEvalAnswer(item, answer, documentId, courseDocumentId, documentText), []);
  assert.ok(screenEvalAnswer(item, { ...answer, sources: [{ ...answer.sources[0], documentHash: "old" }] },
    documentId, courseDocumentId, documentText).includes("wrong_source"));
  assert.ok(screenEvalAnswer(item, { ...answer, sources: [{ ...answer.sources[0], snippet: "invented" }] },
    documentId, courseDocumentId, documentText).includes("unsupported_snippet"));
});

test("refusal cannot receive a citation or credit for an invented answer", () => {
  const item = DEMO_CASES.find((candidate) => candidate.id === "unknown-salary")!;
  assert.deepEqual(screenEvalAnswer(item, { answer: "Нет сведений.", refused: true, sources: [] },
    documentId, courseDocumentId, documentText), []);
  assert.deepEqual(screenEvalAnswer(item, answer, documentId, courseDocumentId, documentText),
    ["expected_refusal", "refusal_has_sources"]);
});

test("changed document is not silently scored against old expectations", () => {
  assert.ok(validateDemoDocument("different edition").includes("document_hash_changed"));
  const approvedText = readFileSync(new URL("../docs/drafts/onboarding-policy.md", import.meta.url), "utf8");
  assert.deepEqual(validateDemoDocument(approvedText), []);
});
