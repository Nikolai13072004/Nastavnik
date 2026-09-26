import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { after, before, test } from "node:test";
import prisma from "@/lib/prisma";
import { createConnectKnowledgeDocument } from "../application/connect-knowledge-document";
import { prismaKnowledgeConnection } from "./prisma-knowledge-connection";
import { listMaxManagerDocuments } from "./prisma-max-course-documents";

const organizationId = `connection-${randomUUID()}`;
const courseId = `${organizationId}-course`;
const managerId = `${organizationId}-hr`;
const learnerId = `${organizationId}-learner`;
const contentText = "Утверждённая инструкция для сотрудников.";
const contentHash = createHash("sha256").update(contentText).digest("hex");
const snapshot = JSON.stringify({ title: "Учебный курс" });
const documentId = `${organizationId}-document`;
const source = { documentId: `${organizationId}-source`, documentHash: contentHash };
let manager: { maxUserId: string; userId: string; organizationId: string; linkedAt: string };
let learner: typeof manager;

before(async () => {
  await prisma.organization.create({ data: { id: organizationId, name: "Test organization" } });
  for (const [id, role] of [[managerId, "HR"], [learnerId, "EMPLOYEE"]]) {
    await prisma.user.create({ data: { id, login: id, name: id, role, passwordHash: "disabled", organizationId } });
    const link = await prisma.maxAccountLink.create({ data: { maxUserId: `${id}-max`, userId: id, organizationId } });
    const identity = { maxUserId: link.maxUserId, userId: id, organizationId, linkedAt: link.createdAt.toISOString() };
    if (id === managerId) manager = identity;
    else learner = identity;
  }
  await prisma.course.create({ data: {
    id: courseId, title: "Test course", organizationId, status: "PUBLISHED", publishedSnapshotJson: snapshot,
  } });
  await prisma.maxCourseDocument.create({ data: {
    id: documentId, organizationId, courseId, title: "Инструкция", sourceName: "instruction.md",
    contentText, contentHash, uploadedById: managerId,
  } });
});

after(async () => {
  await prisma.auditLogEvent.deleteMany({ where: { actorId: managerId, objectId: documentId } });
  await prisma.maxKnowledgeDocument.deleteMany({ where: { organizationId } });
  await prisma.maxCourseDocument.deleteMany({ where: { organizationId } });
  await prisma.course.deleteMany({ where: { id: courseId } });
  await prisma.user.deleteMany({ where: { id: { in: [managerId, learnerId] } } });
  await prisma.organization.deleteMany({ where: { id: organizationId } });
  await prisma.$disconnect();
});

test("HR connector rechecks permission, text and publication within the mapping transaction", async () => {
  assert.equal(await prismaKnowledgeConnection.load(manager, courseId, documentId), null);
  await prisma.maxCourseDocument.update({ where: { id: documentId }, data: { approvedAt: new Date() } });
  assert.equal(await prismaKnowledgeConnection.load(learner, courseId, documentId), null);
  assert.equal(await prismaKnowledgeConnection.load(manager, "foreign-course", documentId), null);
  assert.equal(await prismaKnowledgeConnection.load({ ...manager, organizationId: "foreign" }, courseId, documentId), null);

  const document = await prismaKnowledgeConnection.load(manager, courseId, documentId);
  assert.ok(document);
  assert.equal(document.contentHash, contentHash);
  assert.equal((await listMaxManagerDocuments(manager, courseId))?.[0].aiConnected, false);
  const approve = () => prismaKnowledgeConnection.approve(manager, courseId, documentId, source, document.publishedSnapshotHash);

  await prisma.user.update({ where: { id: managerId }, data: { role: "EMPLOYEE" } });
  assert.equal(await approve(), "FORBIDDEN");
  await prisma.user.update({ where: { id: managerId }, data: { role: "HR" } });
  await prisma.maxAccountLink.update({ where: { maxUserId: manager.maxUserId }, data: { createdAt: new Date(0) } });
  assert.equal(await approve(), "FORBIDDEN");
  await prisma.maxAccountLink.update({ where: { maxUserId: manager.maxUserId }, data: { createdAt: new Date(manager.linkedAt) } });

  await prisma.course.update({ where: { id: courseId }, data: { publishedSnapshotJson: "new snapshot" } });
  assert.equal(await approve(), "CONFLICT");
  await prisma.course.update({ where: { id: courseId }, data: { publishedSnapshotJson: snapshot } });
  await prisma.maxCourseDocument.update({ where: { id: documentId }, data: { revokedAt: new Date() } });
  assert.equal(await approve(), "NOT_APPROVED");
  await prisma.maxCourseDocument.update({ where: { id: documentId }, data: { revokedAt: null, contentText: "Изменённый текст" } });
  assert.equal(await approve(), "HASH_MISMATCH");
  await prisma.maxCourseDocument.update({ where: { id: documentId }, data: { contentText } });
  assert.equal(await prisma.maxKnowledgeDocument.count({ where: { organizationId } }), 0);

  const connect = createConnectKnowledgeDocument(prismaKnowledgeConnection, { findDocument: async () => source });
  assert.equal(await connect(manager, courseId, documentId), "APPROVED");
  assert.equal(await connect(manager, courseId, documentId), "APPROVED");
  assert.equal(await prisma.maxKnowledgeDocument.count({ where: { organizationId } }), 1);
  assert.equal((await listMaxManagerDocuments(manager, courseId))?.[0].aiConnected, true);
  assert.equal(await prisma.auditLogEvent.count({ where: { actorId: managerId, action: "max_document:connect_ai" } }), 2);
  await prisma.course.update({ where: { id: courseId }, data: { publishedSnapshotJson: "new publication" } });
  assert.equal((await listMaxManagerDocuments(manager, courseId))?.[0].aiConnected, false);
});
