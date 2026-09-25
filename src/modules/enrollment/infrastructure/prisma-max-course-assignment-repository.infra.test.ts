import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { after, test } from "node:test";
import prisma from "@/lib/prisma";
import { createAssignMaxCourse } from "../application/assign-max-course";
import { prismaMaxCourseAssignmentRepository } from "./prisma-max-course-assignment-repository";

const prefix = `max-assignment-${randomUUID()}`;
const ownOrg = `${prefix}-own`;
const otherOrg = `${prefix}-other`;
const actorId = `${prefix}-hr`;
const ownLearnerId = `${prefix}-learner`;
const otherLearnerId = `${prefix}-other-learner`;
const blockedLearnerId = `${prefix}-blocked`;
const ownCourseId = `${prefix}-course`;
const otherCourseId = `${prefix}-other-course`;
const draftCourseId = `${prefix}-draft`;
const maxUserId = `${prefix}-max`;
const botUsername = `${prefix.replaceAll("-", "")}_bot`;

after(async () => {
  await prisma.maxBotDelivery.deleteMany({ where: { botUsername } });
  await prisma.auditLogEvent.deleteMany({ where: { actorId, objectId: ownCourseId } });
  await prisma.user.deleteMany({ where: { id: { in: [actorId, ownLearnerId, otherLearnerId, blockedLearnerId] } } });
  await prisma.course.deleteMany({ where: { id: { in: [ownCourseId, otherCourseId, draftCourseId] } } });
  await prisma.organization.deleteMany({ where: { id: { in: [ownOrg, otherOrg] } } });
  await prisma.$disconnect();
});

test("MAX HR assigns only within its organization and repeated requests stay idempotent", async (t) => {
  const previousBotUsername = process.env.MAX_BOT_USERNAME;
  t.after(() => {
    if (previousBotUsername === undefined) delete process.env.MAX_BOT_USERNAME;
    else process.env.MAX_BOT_USERNAME = previousBotUsername;
  });
  process.env.MAX_BOT_USERNAME = botUsername;
  await prisma.organization.createMany({ data: [
    { id: ownOrg, name: ownOrg },
    { id: otherOrg, name: otherOrg },
  ] });
  await prisma.user.createMany({ data: [
    { id: actorId, login: actorId, name: "HR", role: "HR", passwordHash: "test", organizationId: ownOrg },
    { id: ownLearnerId, login: ownLearnerId, name: "Employee", passwordHash: "test", organizationId: ownOrg },
    { id: otherLearnerId, login: otherLearnerId, name: "Foreign", passwordHash: "test", organizationId: otherOrg },
    { id: blockedLearnerId, login: blockedLearnerId, name: "Blocked", passwordHash: "test", organizationId: ownOrg, status: "BLOCKED" },
  ] });
  await prisma.course.createMany({ data: [
    { id: ownCourseId, title: "Own", organizationId: ownOrg, status: "PUBLISHED" },
    { id: otherCourseId, title: "Foreign", organizationId: otherOrg, status: "PUBLISHED" },
    { id: draftCourseId, title: "Draft", organizationId: ownOrg, status: "DRAFT" },
  ] });
  const link = await prisma.maxAccountLink.create({ data: {
    maxUserId,
    userId: actorId,
    organizationId: ownOrg,
  } });
  await prisma.maxAccountLink.create({ data: {
    maxUserId: `${prefix}-learner-max`, userId: ownLearnerId, organizationId: ownOrg,
  } });
  const identity = {
    maxUserId,
    userId: actorId,
    organizationId: ownOrg,
    linkedAt: link.createdAt.toISOString(),
  };
  const assign = createAssignMaxCourse(prismaMaxCourseAssignmentRepository);

  const revision = await prisma.maxCourseDocument.create({ data: {
    organizationId: ownOrg,
    courseId: ownCourseId,
    title: "Обновлённый документ",
    sourceName: "rules.txt",
    contentText: "Новая редакция",
    contentHash: "test-hash",
    supersedesId: `${prefix}-previous`,
    versionNumber: 2,
    checkQuestion: "Что изменилось?",
    checkOptionsJson: JSON.stringify(["Первое", "Второе", "Третье"]),
    checkCorrectIndex: 1,
    approvedAt: new Date(),
    uploadedById: actorId,
  } });

  assert.deepEqual(await assign({ ...identity, linkedAt: "stale" }, ownCourseId, ownLearnerId), { status: "FORBIDDEN" });
  assert.deepEqual(await assign(identity, otherCourseId, ownLearnerId), { status: "NOT_FOUND" });
  assert.deepEqual(await assign(identity, draftCourseId, ownLearnerId), { status: "NOT_FOUND" });
  assert.deepEqual(await assign(identity, ownCourseId, otherLearnerId), { status: "NOT_FOUND" });
  assert.deepEqual(await assign(identity, ownCourseId, blockedLearnerId), { status: "NOT_FOUND" });

  assert.deepEqual(await assign(identity, ownCourseId, ownLearnerId), { status: "ASSIGNED" });
  assert.deepEqual(await assign(identity, ownCourseId, ownLearnerId), { status: "ALREADY_ASSIGNED" });
  assert.equal(await prisma.courseUserAssignment.count({ where: { courseId: ownCourseId, userId: ownLearnerId } }), 1);
  assert.equal(await prisma.auditLogEvent.count({ where: { actorId, objectId: ownCourseId } }), 1);
  assert.equal(await prisma.maxDocumentTraining.count({
    where: { documentId: revision.id, userId: ownLearnerId },
  }), 1);
  assert.equal(await prisma.maxBotDelivery.count({
    where: { documentId: revision.id, maxUserId: `${prefix}-learner-max` },
  }), 1);

  await prisma.courseUserAssignment.update({
    where: { courseId_userId: { courseId: ownCourseId, userId: ownLearnerId } },
    data: { expiresAt: new Date(0) },
  });
  assert.deepEqual(await assign(identity, ownCourseId, ownLearnerId), { status: "RENEWED" });
  const renewed = await prisma.courseUserAssignment.findUniqueOrThrow({
    where: { courseId_userId: { courseId: ownCourseId, userId: ownLearnerId } },
  });
  assert.equal(renewed.expiresAt, null);
  assert.equal(await prisma.auditLogEvent.count({ where: { actorId, objectId: ownCourseId } }), 2);
  assert.equal(await prisma.maxDocumentTraining.count({
    where: { documentId: revision.id, userId: ownLearnerId },
  }), 1);
  assert.equal(await prisma.maxBotDelivery.count({
    where: { documentId: revision.id, maxUserId: `${prefix}-learner-max` },
  }), 1);

  await prisma.user.update({ where: { id: actorId }, data: { role: "USER" } });
  assert.deepEqual(await assign(identity, ownCourseId, ownLearnerId), { status: "FORBIDDEN" });
});
