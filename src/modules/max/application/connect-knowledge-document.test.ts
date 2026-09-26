import assert from "node:assert/strict";
import test from "node:test";
import { createConnectKnowledgeDocument, type KnowledgeConnectionRepository } from "./connect-knowledge-document";

const identity = { maxUserId: "max-1", userId: "hr-1", organizationId: "org-1", linkedAt: "2026-09-26T00:00:00Z" };
const document = { sourceName: "policy.md", contentHash: "a".repeat(64), publishedSnapshotHash: "snapshot-v1" };
const source = { documentId: "source-1", documentHash: document.contentHash };

test("only an authorized published document can reach Vedomo", async () => {
  const connect = createConnectKnowledgeDocument({
    load: async () => null,
    approve: async () => { throw new Error("must not approve"); },
  }, { findDocument: async () => { throw new Error("must not query"); } });
  assert.equal(await connect(identity, "course-1", "document-1"), "NOT_FOUND");
});

test("PDF is not silently matched to an unrelated original file", async () => {
  const connect = createConnectKnowledgeDocument({
    load: async () => ({ ...document, sourceName: "policy.pdf" }),
    approve: async () => { throw new Error("must not approve"); },
  }, { findDocument: async () => { throw new Error("must not query"); } });
  assert.equal(await connect(identity, "course-1", "document-1"), "UNSUPPORTED_FORMAT");
});

test("looks up exact content in the linked course and rechecks publication on approval", async () => {
  const repository: KnowledgeConnectionRepository = {
    load: async () => document,
    approve: async (...args) => {
      assert.deepEqual(args, [identity, "course-1", "document-1", source, "snapshot-v1"]);
      return "APPROVED";
    },
  };
  const connect = createConnectKnowledgeDocument(repository, {
    findDocument: async (...args) => {
      assert.deepEqual(args, ["org-1", "course-1", document.contentHash]);
      return source;
    },
  });
  assert.equal(await connect(identity, "course-1", "document-1"), "APPROVED");
});

test("missing or mismatched remote content cannot create a mapping", async () => {
  const repository: KnowledgeConnectionRepository = {
    load: async () => document,
    approve: async () => { throw new Error("must not approve"); },
  };
  const missing = createConnectKnowledgeDocument(repository, { findDocument: async () => null });
  assert.equal(await missing(identity, "course-1", "document-1"), "SOURCE_NOT_READY");
  const mismatch = createConnectKnowledgeDocument(repository, {
    findDocument: async () => ({ ...source, documentHash: "b".repeat(64) }),
  });
  assert.equal(await mismatch(identity, "course-1", "document-1"), "HASH_MISMATCH");
});

test("lost HR access or changed publication during lookup fails closed", async () => {
  for (const result of ["FORBIDDEN", "NOT_APPROVED", "CONFLICT"] as const) {
    const connect = createConnectKnowledgeDocument({
      load: async () => document,
      approve: async () => result,
    }, { findDocument: async () => source });
    assert.equal(await connect(identity, "course-1", "document-1"), result);
  }
});
