import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { after, test } from "node:test";
import prisma from "@/lib/prisma";
import { approveMaxKnowledgeDocument, revokeMaxKnowledgeDocument } from "./approve-max-knowledge-document";

const organizationId = `mapping-org-${randomUUID()}`;
const courseId = `mapping-course-${randomUUID()}`;
const text = "Регламент подтверждён ответственным сотрудником.";
const contentHash = createHash("sha256").update(text).digest("hex");
const snapshot = JSON.stringify({ title: "Демо" });

after(async () => {
  await prisma.maxKnowledgeDocument.deleteMany({ where: { organizationId } });
  await prisma.maxCourseDocument.deleteMany({ where: { organizationId } });
  await prisma.course.deleteMany({ where: { id: courseId } });
  await prisma.organization.deleteMany({ where: { id: organizationId } });
  await prisma.$disconnect();
});

test("approval requires the same published, approved text and refuses conflicting mappings", async () => {
  await prisma.organization.create({ data: { id: organizationId, name: organizationId } });
  await prisma.course.create({ data: {
    id: courseId,
    organizationId,
    title: "Демо",
    status: "PUBLISHED",
    publishedSnapshotJson: snapshot,
  } });
  const document = await prisma.maxCourseDocument.create({ data: {
    organizationId,
    courseId,
    title: "Регламент",
    sourceName: "rule.txt",
    contentText: text,
    contentHash,
    uploadedById: "operator",
  } });
  const input = {
    courseId,
    courseDocumentId: document.id,
    vedomoDocumentId: "vedomo-rule",
    vedomoDocumentHash: contentHash,
  };

  assert.equal(await approveMaxKnowledgeDocument(prisma, input), "NOT_APPROVED");
  await prisma.maxCourseDocument.update({ where: { id: document.id }, data: { approvedAt: new Date() } });
  assert.equal(await approveMaxKnowledgeDocument(prisma, { ...input, vedomoDocumentHash: "0".repeat(64) }), "HASH_MISMATCH");
  assert.equal(await approveMaxKnowledgeDocument(prisma, input), "APPROVED");
  assert.equal(await approveMaxKnowledgeDocument(prisma, { ...input, vedomoDocumentId: "other" }), "CONFLICT");

  const mapping = await prisma.maxKnowledgeDocument.findFirstOrThrow({
    where: { organizationId, courseId, vedomoDocumentId: input.vedomoDocumentId },
  });
  assert.equal(mapping.publishedSnapshotHash, createHash("sha256").update(snapshot).digest("hex"));
  assert.equal(mapping.courseDocumentId, document.id);
  assert.equal(await revokeMaxKnowledgeDocument(prisma, courseId, input.vedomoDocumentId), "REVOKED");
  assert.equal(await revokeMaxKnowledgeDocument(prisma, courseId, input.vedomoDocumentId), "NOT_FOUND");
});
