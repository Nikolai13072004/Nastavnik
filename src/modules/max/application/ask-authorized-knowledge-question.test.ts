import assert from "node:assert/strict";
import test from "node:test";
import { createAskAuthorizedKnowledgeQuestion } from "./ask-authorized-knowledge-question";
import type { MaxLearnerIdentity } from "./list-courses";
import type { AuthorizedDocument, KnowledgeAnswer } from "./verify-knowledge-answer";

const identity: MaxLearnerIdentity = {
  maxUserId: "max-1", userId: "user-1", organizationId: "org-1", linkedAt: "2026-09-24T00:00:00Z",
};
const approved: AuthorizedDocument[] = [{
  id: "doc-1", contentHash: "version-1", contentText: "Подтверждает ответственный сотрудник.",
}];
const answer: KnowledgeAnswer = {
  answer: "Подтверждает ответственный сотрудник.",
  refused: false,
  sources: [{
    documentId: "doc-1", documentHash: "version-1", title: "Регламент", section: "1",
    pageStart: 1, pageEnd: 1, snippet: "Подтверждает ответственный сотрудник.",
  }],
};

test("never calls Vedomo without a current approved document", async () => {
  let calls = 0;
  const ask = createAskAuthorizedKnowledgeQuestion(
    { list: async () => [] },
    { ask: async () => { calls++; return answer; } },
  );
  assert.equal(await ask(identity, "course-1", "Кто подтверждает?"), null);
  assert.equal(calls, 0);
});

test("uses the linked organization and returns an answer with a source approved both times", async () => {
  const calls: string[] = [];
  const ask = createAskAuthorizedKnowledgeQuestion(
    { list: async (current, courseId) => {
      assert.equal(current, identity);
      assert.equal(courseId, "course-1");
      return approved;
    } },
    { ask: async (organizationId, courseId, question, documentIds) => {
      assert.deepEqual(documentIds, ["doc-1"]);
      calls.push(organizationId, courseId, question);
      return answer;
    } },
  );
  assert.deepEqual(await ask(identity, "course-1", "Кто подтверждает?"), answer);
  assert.deepEqual(calls, ["org-1", "course-1", "Кто подтверждает?"]);
});

test("discards an answer when access is revoked during the Vedomo request", async () => {
  let reads = 0;
  const ask = createAskAuthorizedKnowledgeQuestion(
    { list: async () => ++reads === 1 ? approved : null },
    { ask: async () => answer },
  );
  assert.equal(await ask(identity, "course-1", "Кто подтверждает?"), null);
});

test("refuses an answer when its document version changes during the request", async () => {
  let reads = 0;
  const ask = createAskAuthorizedKnowledgeQuestion(
    { list: async () => ++reads === 1 ? approved : [{ ...approved[0], contentHash: "version-2" }] },
    { ask: async () => answer },
  );
  const result = await ask(identity, "course-1", "Кто подтверждает?");
  assert.equal(result?.refused, true);
  assert.deepEqual(result?.sources, []);
});

test("refuses an answer based on a foreign document", async () => {
  const ask = createAskAuthorizedKnowledgeQuestion(
    { list: async () => approved },
    { ask: async () => ({
      ...answer,
      sources: [{ ...answer.sources[0], documentId: "foreign-doc" }],
    }) },
  );
  assert.equal((await ask(identity, "course-1", "Кто подтверждает?"))?.refused, true);
});

test("refuses an answer if the approved text changes while Vedomo is answering", async () => {
  let reads = 0;
  const ask = createAskAuthorizedKnowledgeQuestion(
    { list: async () => ++reads === 1 ? approved : [{ ...approved[0], contentText: "Новая редакция." }] },
    { ask: async () => answer },
  );
  assert.equal((await ask(identity, "course-1", "Кто подтверждает?"))?.refused, true);
});
