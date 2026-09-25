import assert from "node:assert/strict";
import test from "node:test";
import { createListAuthorizedKnowledgeDocuments } from "./authorized-knowledge-documents";
import type { MaxLearnerIdentity } from "./list-courses";

const identity: MaxLearnerIdentity = {
  maxUserId: "max-1", userId: "user-1", organizationId: "org-1", linkedAt: "2026-09-24T00:00:00Z",
};

test("requires current learner access before reading any knowledge mapping", async () => {
  let reads = 0;
  const list = createListAuthorizedKnowledgeDocuments(
    { publishedSnapshotHash: async () => null },
    { listApproved: async () => { reads++; return []; } },
  );
  assert.equal(await list(identity, "course-1"), null);
  assert.equal(reads, 0);
});

test("scopes lookup by linked organization, course and exact publication hash", async () => {
  const calls: string[][] = [];
  const list = createListAuthorizedKnowledgeDocuments(
    { publishedSnapshotHash: async (current, courseId) => {
      assert.equal(current, identity);
      assert.equal(courseId, "course-1");
      return "snapshot-v2";
    } },
    { listApproved: async (...args) => {
      calls.push(args);
      return [{ id: "document-1", contentHash: "document-v2", contentText: "Подтверждённый текст." }];
    } },
  );
  assert.deepEqual(await list(identity, "course-1"), [{
    id: "document-1", contentHash: "document-v2", contentText: "Подтверждённый текст.",
  }]);
  assert.deepEqual(calls, [["org-1", "course-1", "snapshot-v2"]]);
});
