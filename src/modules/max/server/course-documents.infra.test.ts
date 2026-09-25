import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { after, before, test } from "node:test";
import prisma from "@/lib/prisma";
import { getMaxCourseDocuments, getMaxManagerDocuments, manageMaxCourseDocuments } from "./course-documents";

const prefix = `max-document-test-${randomUUID()}`;
const ownOrg = `${prefix}-own`;
const otherOrg = `${prefix}-other`;
const managerId = `${prefix}-manager`;
const learnerId = `${prefix}-learner`;
const otherLearnerId = `${prefix}-other-learner`;
const ownCourse = `${prefix}-course`;
const otherCourse = `${prefix}-foreign-course`;
const managerMax = `${prefix}-manager-max`;
const learnerMax = `${prefix}-learner-max`;
const otherMax = `${prefix}-other-max`;

async function identity(maxUserId: string) {
  const link = await prisma.maxAccountLink.findUniqueOrThrow({ where: { maxUserId } });
  return {
    maxUserId,
    userId: link.userId,
    organizationId: link.organizationId,
    linkedAt: link.createdAt.toISOString(),
  };
}

before(async () => {
  await prisma.organization.createMany({ data: [
    { id: ownOrg, name: ownOrg },
    { id: otherOrg, name: otherOrg },
  ] });
  await prisma.user.createMany({ data: [
    { id: managerId, login: managerId, name: "HR", role: "HR", passwordHash: "disabled", organizationId: ownOrg },
    { id: learnerId, login: learnerId, name: "Learner", passwordHash: "disabled", organizationId: ownOrg },
    { id: otherLearnerId, login: otherLearnerId, name: "Other", passwordHash: "disabled", organizationId: otherOrg },
  ] });
  await prisma.maxAccountLink.createMany({ data: [
    { maxUserId: managerMax, userId: managerId, organizationId: ownOrg },
    { maxUserId: learnerMax, userId: learnerId, organizationId: ownOrg },
    { maxUserId: otherMax, userId: otherLearnerId, organizationId: otherOrg },
  ] });
  await prisma.course.createMany({ data: [
    { id: ownCourse, title: "Our course", ownerId: managerId, organizationId: ownOrg, status: "PUBLISHED" },
    { id: otherCourse, title: "Other course", ownerId: otherLearnerId, organizationId: otherOrg, status: "PUBLISHED" },
  ] });
  await prisma.courseUserAssignment.create({ data: { courseId: ownCourse, userId: learnerId } });
});

after(async () => {
  await prisma.auditLogEvent.deleteMany({ where: { actorId: managerId, objectType: "max_course_document" } });
  await prisma.maxCourseDocument.deleteMany({ where: { organizationId: { in: [ownOrg, otherOrg] } } });
  await prisma.course.deleteMany({ where: { id: { in: [ownCourse, otherCourse] } } });
  await prisma.user.deleteMany({ where: { id: { in: [managerId, learnerId, otherLearnerId] } } });
  await prisma.organization.deleteMany({ where: { id: { in: [ownOrg, otherOrg] } } });
  await prisma.$disconnect();
});

test("HR publishes a document only to active learners of the same organization", async () => {
  const manager = await identity(managerMax);
  const learner = await identity(learnerMax);
  const foreign = await identity(otherMax);
  const draft = { courseId: ownCourse, title: "Правила работы", sourceName: "rules.txt", contentText: "Проверенный рабочий текст." };

  assert.equal(await manageMaxCourseDocuments.upload(learner, draft), "FORBIDDEN");
  assert.equal(await manageMaxCourseDocuments.upload(manager, { ...draft, courseId: otherCourse }), "NOT_FOUND");
  assert.equal(await manageMaxCourseDocuments.upload(manager, draft), "CREATED");

  const stored = await prisma.maxCourseDocument.findFirstOrThrow({ where: { organizationId: ownOrg, courseId: ownCourse } });
  assert.equal(stored.approvedAt, null);
  assert.equal(stored.contentText, draft.contentText);
  assert.deepEqual(await getMaxCourseDocuments(learner, ownCourse), { documents: [] });
  assert.deepEqual(await getMaxCourseDocuments(learner, ownCourse, stored.id), { error: "NOT_FOUND" });
  assert.deepEqual(await getMaxCourseDocuments(foreign, ownCourse), { error: "FORBIDDEN" });
  assert.equal((await getMaxManagerDocuments(manager, ownCourse, stored.id)).document?.contentText, draft.contentText);
  assert.deepEqual(await getMaxManagerDocuments(learner, ownCourse), { error: "NOT_FOUND" });

  assert.equal(await manageMaxCourseDocuments.setPublished(manager, ownCourse, stored.id, true), "UPDATED");
  assert.equal((await getMaxCourseDocuments(learner, ownCourse)).documents?.length, 1);
  assert.equal((await getMaxCourseDocuments(learner, ownCourse, stored.id)).document?.contentText, draft.contentText);
  assert.equal(await manageMaxCourseDocuments.setPublished(manager, otherCourse, stored.id, false), "NOT_FOUND");

  await prisma.courseUserAssignment.update({
    where: { courseId_userId: { courseId: ownCourse, userId: learnerId } },
    data: { expiresAt: new Date(0) },
  });
  assert.deepEqual(await getMaxCourseDocuments(learner, ownCourse, stored.id), { error: "FORBIDDEN" });

  assert.equal(await manageMaxCourseDocuments.setPublished(manager, ownCourse, stored.id, false), "UPDATED");
  assert.equal(await prisma.maxCourseDocument.count({ where: { id: stored.id, revokedAt: { not: null } } }), 1);
  assert.equal(await manageMaxCourseDocuments.setPublished(manager, ownCourse, stored.id, true), "NOT_FOUND");
  assert.equal(await prisma.auditLogEvent.count({ where: { actorId: managerId, objectId: stored.id } }), 3);

  await prisma.user.update({ where: { id: managerId }, data: { organizationId: otherOrg } });
  assert.equal(await manageMaxCourseDocuments.upload(manager, draft), "FORBIDDEN");
});
