import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { after, test } from "node:test";
import prisma from "@/lib/prisma";
import { prismaMaxKnowledgeDocuments } from "./prisma-max-knowledge-documents";

const prefix = `max-knowledge-test-${randomUUID()}`;
const organizationId = `${prefix}-org`;
const foreignOrganizationId = `${prefix}-foreign-org`;
const courseId = `${prefix}-course`;
const foreignCourseId = `${prefix}-foreign-course`;
const snapshotHash = "published-snapshot";
const ownText = "Утверждает ответственный сотрудник.";
const ownHash = createHash("sha256").update(ownText).digest("hex");

after(async () => {
  await prisma.maxKnowledgeDocument.deleteMany({ where: { organizationId } });
  await prisma.maxCourseDocument.deleteMany({ where: { organizationId: { in: [organizationId, foreignOrganizationId] } } });
  await prisma.course.deleteMany({ where: { id: { in: [courseId, foreignCourseId] } } });
  await prisma.organization.deleteMany({ where: { id: { in: [organizationId, foreignOrganizationId] } } });
  await prisma.$disconnect();
});

test("knowledge sources require an approved document in the same organization and course", async () => {
  await prisma.organization.createMany({ data: [
    { id: organizationId, name: organizationId },
    { id: foreignOrganizationId, name: foreignOrganizationId },
  ] });
  await prisma.course.createMany({ data: [
    { id: courseId, title: "Курс", organizationId, status: "PUBLISHED" },
    { id: foreignCourseId, title: "Чужой курс", organizationId: foreignOrganizationId, status: "PUBLISHED" },
  ] });

  const own = await prisma.maxCourseDocument.create({ data: {
    organizationId,
    courseId,
    title: "Правило",
    sourceName: "rule.txt",
    contentText: ownText,
    contentHash: ownHash,
    uploadedById: "test-actor",
    approvedAt: new Date(),
  } });
  const foreign = await prisma.maxCourseDocument.create({ data: {
    organizationId: foreignOrganizationId,
    courseId: foreignCourseId,
    title: "Чужое правило",
    sourceName: "rule.txt",
    contentText: "Секрет другой организации.",
    contentHash: "foreign-text-hash",
    uploadedById: "test-actor",
    approvedAt: new Date(),
  } });
  await prisma.maxKnowledgeDocument.createMany({ data: [
    {
      organizationId, courseId, publishedSnapshotHash: snapshotHash,
      vedomoDocumentId: "vedomo-own", vedomoDocumentHash: ownHash,
      courseDocumentId: own.id,
    },
    {
      organizationId, courseId, publishedSnapshotHash: snapshotHash,
      vedomoDocumentId: "vedomo-foreign", vedomoDocumentHash: "vedomo-hash-foreign",
      courseDocumentId: foreign.id,
    },
    {
      organizationId, courseId, publishedSnapshotHash: snapshotHash,
      vedomoDocumentId: "vedomo-unlinked", vedomoDocumentHash: "vedomo-hash-unlinked",
    },
  ] });

  assert.deepEqual(
    await prismaMaxKnowledgeDocuments.listApproved(organizationId, courseId, snapshotHash),
    [{ id: "vedomo-own", contentHash: ownHash, contentText: own.contentText }],
  );
  assert.deepEqual(await prismaMaxKnowledgeDocuments.listApproved(organizationId, courseId, "old-snapshot"), []);

  await prisma.maxCourseDocument.update({ where: { id: own.id }, data: { contentText: "Изменённый текст." } });
  assert.deepEqual(await prismaMaxKnowledgeDocuments.listApproved(organizationId, courseId, snapshotHash), []);
  await prisma.maxCourseDocument.update({ where: { id: own.id }, data: { contentText: ownText } });

  await prisma.maxKnowledgeDocument.update({
    where: { organizationId_courseId_vedomoDocumentId: {
      organizationId, courseId, vedomoDocumentId: "vedomo-own",
    } },
    data: { vedomoDocumentHash: "wrong-hash" },
  });
  assert.deepEqual(await prismaMaxKnowledgeDocuments.listApproved(organizationId, courseId, snapshotHash), []);

  await prisma.maxCourseDocument.update({ where: { id: own.id }, data: { revokedAt: new Date() } });
  assert.deepEqual(await prismaMaxKnowledgeDocuments.listApproved(organizationId, courseId, snapshotHash), []);
});
