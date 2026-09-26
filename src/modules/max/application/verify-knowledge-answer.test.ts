import assert from "node:assert/strict";
import test from "node:test";
import { verifyKnowledgeAnswer, type KnowledgeAnswer } from "./verify-knowledge-answer";

const answer: KnowledgeAnswer = {
  answer: "Изменения подтверждает ответственный сотрудник.", refused: false,
  sources: [{
    documentId: "doc-1", documentHash: "version-2", title: "Регламент",
    section: "1", pageStart: 1, pageEnd: 1, snippet: "Подтверждает ответственный сотрудник.",
  }],
};

test("preserves an answer only when every source is in the authorized version", () => {
  const approved = { id: "doc-1", contentHash: "version-2", contentText: "Подтверждает ответственный сотрудник." };
  assert.deepEqual(verifyKnowledgeAnswer(answer, [approved]), answer);
  assert.equal(verifyKnowledgeAnswer(answer, [{ ...approved, contentHash: "version-1" }]).refused, true);
  assert.equal(verifyKnowledgeAnswer(answer, [{ ...approved, id: "doc-2" }]).sources.length, 0);
  assert.equal(verifyKnowledgeAnswer(answer, []).answer.includes("Изменения подтверждает"), false);
});

test("one foreign source invalidates the whole answer", () => {
  const mixed = { ...answer, sources: [...answer.sources, { ...answer.sources[0], documentId: "doc-foreign" }] };
  assert.equal(verifyKnowledgeAnswer(mixed, [{
    id: "doc-1", contentHash: "version-2", contentText: "Подтверждает ответственный сотрудник.",
  }]).refused, true);
});

test("refuses a fabricated passage even with the correct document ID and hash", () => {
  const approved = { id: "doc-1", contentHash: "version-2", contentText: "Другой текст регламента." };
  assert.equal(verifyKnowledgeAnswer(answer, [approved]).refused, true);
  assert.equal(verifyKnowledgeAnswer(answer, [{ ...approved, contentText: "" }]).refused, true);
});

test("matches a full passage with its section label and PDF line wrapping", () => {
  const cited = {
    ...answer,
    sources: [{ ...answer.sources[0], section: "Правило", snippet: "[Правило] Ответственный подтверждает изменения." }],
  };
  const approved = {
    id: "doc-1",
    contentHash: "version-2",
    contentText: "Ответственный под-\nтверждает изменения.",
  };
  assert.deepEqual(verifyKnowledgeAnswer(cited, [approved]), cited);
});

test("source links use the exact local mapping, not a document ID supplied by the model", () => {
  const forged = {
    ...answer,
    sources: [{ ...answer.sources[0], courseDocumentId: "foreign-document" }],
  };
  const approved = {
    id: "doc-1", contentHash: "version-2", contentText: "Подтверждает ответственный сотрудник.",
    courseDocumentId: "local-approved-document",
  };
  const result = verifyKnowledgeAnswer(forged, [approved]);
  assert.equal(result.sources[0].courseDocumentId, "local-approved-document");
  assert.equal(verifyKnowledgeAnswer(forged, [{ ...approved, courseDocumentId: undefined }])
    .sources[0].courseDocumentId, undefined);
  assert.equal(forged.sources[0].courseDocumentId, "foreign-document");
});

test("multiple source links keep their own file mappings", () => {
  const secondSource = { ...answer.sources[0], documentId: "doc-2", documentHash: "version-3" };
  const result = verifyKnowledgeAnswer({ ...answer, sources: [answer.sources[0], secondSource] }, [
    { id: "doc-1", contentHash: "version-2", contentText: answer.sources[0].snippet, courseDocumentId: "file-1" },
    { id: "doc-2", contentHash: "version-3", contentText: secondSource.snippet, courseDocumentId: "file-2" },
  ]);
  assert.deepEqual(result.sources.map((source) => source.courseDocumentId), ["file-1", "file-2"]);
});
