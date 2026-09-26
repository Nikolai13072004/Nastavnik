import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { after, before, test } from "node:test";
import prisma from "@/lib/prisma";
import { PERMISSIONS } from "@/lib/roles";
import { getMaxCourseDocuments, getMaxManagerDocuments, manageMaxCourseDocuments } from "./course-documents";
import { recordMaxDocumentTraining } from "./document-training";
import { prismaBotDeliveryRepository } from "../infrastructure/prisma-bot-delivery-repository";
import { deliverNextBotMessage } from "../application/bot-delivery";

const prefix = `max-revision-${randomUUID()}`;
const organizationId = `${prefix}-org`;
const foreignOrganizationId = `${prefix}-foreign-org`;
const managerId = `${prefix}-manager`;
const learnerId = `${prefix}-learner`;
const expiredId = `${prefix}-expired`;
const lateLearnerId = `${prefix}-late`;
const foreignId = `${prefix}-foreign`;
const courseId = `${prefix}-course`;
const roleName = `${prefix}-role`;
const groupId = `${prefix}-group`;

function person(userId: string, organizationId: string) {
  return { maxUserId: `${userId}-max`, userId, organizationId, linkedAt: "" };
}

async function linkedPerson(userId: string, organizationId: string) {
  const link = await prisma.maxAccountLink.findUniqueOrThrow({ where: { maxUserId: `${userId}-max` } });
  return { ...person(userId, organizationId), linkedAt: link.createdAt.toISOString() };
}

before(async () => {
  await prisma.organization.createMany({ data: [
    { id: organizationId, name: organizationId },
    { id: foreignOrganizationId, name: foreignOrganizationId },
  ] });
  await prisma.roleProfile.create({ data: { name: roleName, permissionsJson: JSON.stringify([
    PERMISSIONS.COURSES_MANAGE_ASSIGNMENTS, PERMISSIONS.REPORTS_VIEW,
  ]) } });
  await prisma.user.createMany({ data: [
    { id: managerId, login: managerId, name: "HR", role: roleName, passwordHash: "unused", organizationId },
    { id: learnerId, login: learnerId, name: "Ученик", passwordHash: "unused", organizationId },
    { id: expiredId, login: expiredId, name: "Просрочен", passwordHash: "unused", organizationId },
    { id: foreignId, login: foreignId, name: "Чужой", passwordHash: "unused", organizationId: foreignOrganizationId },
  ] });
  await prisma.maxAccountLink.createMany({ data: [managerId, learnerId, expiredId, foreignId].map((userId) => ({
    maxUserId: `${userId}-max`, userId,
    organizationId: userId === foreignId ? foreignOrganizationId : organizationId,
  })) });
  await prisma.course.create({ data: {
    id: courseId, organizationId, title: "Рабочие правила", status: "PUBLISHED",
  } });
  await prisma.courseUserAssignment.createMany({ data: [
    { courseId, userId: learnerId },
    { courseId, userId: expiredId, expiresAt: new Date(0) },
  ] });
  await prisma.group.create({ data: { id: groupId, name: groupId,
    memberships: { create: { userId: expiredId } },
    courseAssignments: { create: { courseId, assignedById: managerId } },
  } });
});

after(async () => {
  await prisma.maxBotDelivery.deleteMany({ where: { botUsername: `${prefix.replaceAll("-", "")}_bot` } });
  await prisma.auditLogEvent.deleteMany({ where: { actorId: managerId, objectType: "max_course_document" } });
  await prisma.maxCourseDocument.deleteMany({ where: { organizationId } });
  await prisma.course.deleteMany({ where: { id: courseId } });
  await prisma.group.deleteMany({ where: { id: groupId } });
  await prisma.user.deleteMany({ where: { id: { in: [managerId, learnerId, expiredId, lateLearnerId, foreignId] } } });
  await prisma.roleProfile.deleteMany({ where: { name: roleName } });
  await prisma.organization.deleteMany({ where: { id: { in: [organizationId, foreignOrganizationId] } } });
  await prisma.$disconnect();
});

test("HR confirms a revision audience and sees the repeated-learning result", async (t) => {
  const previousBotUsername = process.env.MAX_BOT_USERNAME;
  t.after(() => {
    if (previousBotUsername === undefined) delete process.env.MAX_BOT_USERNAME;
    else process.env.MAX_BOT_USERNAME = previousBotUsername;
  });
  process.env.MAX_BOT_USERNAME = `${prefix.replaceAll("-", "")}_bot`;
  const manager = await linkedPerson(managerId, organizationId);
  const learner = await linkedPerson(learnerId, organizationId);
  const foreign = await linkedPerson(foreignId, foreignOrganizationId);
  const base = { courseId, title: "Регламент", sourceName: "rules.txt", contentText: "Старая редакция." };

  assert.equal(await manageMaxCourseDocuments.upload(manager, base), "CREATED");
  const previous = await prisma.maxCourseDocument.findFirstOrThrow({ where: { courseId } });
  assert.equal(await manageMaxCourseDocuments.setPublished(manager, courseId, previous.id, true), "UPDATED");

  const revisionDraft = {
    ...base,
    contentText: "Новая редакция: заявку подтверждает руководитель.",
    supersedesId: previous.id,
    changeSummary: "Теперь заявку подтверждает руководитель.",
    checkQuestion: "Кто подтверждает заявку?",
    checkOptions: ["Сам сотрудник", "Руководитель", "Никто"],
    checkCorrectIndex: 1,
  };
  assert.equal(await manageMaxCourseDocuments.upload(manager, { ...revisionDraft,
    contentText: base.contentText }), "CONFLICT");
  assert.equal(await manageMaxCourseDocuments.upload(manager, revisionDraft), "CREATED");
  let revision = await prisma.maxCourseDocument.findFirstOrThrow({ where: { supersedesId: previous.id } });
  assert.equal(await manageMaxCourseDocuments.setPublished(manager, courseId, revision.id, false), "UPDATED");
  assert.equal(await prisma.maxCourseDocument.count({ where: { id: revision.id } }), 0);
  assert.equal(await manageMaxCourseDocuments.upload(manager, revisionDraft), "CREATED");
  revision = await prisma.maxCourseDocument.findFirstOrThrow({ where: { supersedesId: previous.id } });
  assert.equal(revision.versionNumber, 2);
  const audience = (await getMaxManagerDocuments(manager, courseId)).audience;
  assert.deepEqual(audience?.map(({ id }) => id), [learnerId]);

  assert.equal(await manageMaxCourseDocuments.setPublished(manager, courseId, revision.id, true, []), "AUDIENCE_CHANGED");
  assert.equal((await prisma.maxCourseDocument.findUniqueOrThrow({ where: { id: previous.id } })).revokedAt, null);
  assert.equal(await manageMaxCourseDocuments.setPublished(manager, courseId, revision.id, true, [learnerId]), "UPDATED");
  assert.ok((await prisma.maxCourseDocument.findUniqueOrThrow({ where: { id: previous.id } })).revokedAt);
  assert.deepEqual((await getMaxCourseDocuments(learner, courseId)).documents?.map(({ id }) => id), [revision.id]);
  assert.deepEqual(await getMaxCourseDocuments(learner, courseId, previous.id), { error: "NOT_FOUND" });
  assert.deepEqual(await getMaxCourseDocuments(foreign, courseId, revision.id), { error: "FORBIDDEN" });

  const detail = (await getMaxCourseDocuments(learner, courseId, revision.id)).document;
  assert.equal(detail?.checkQuestion, "Кто подтверждает заявку?");
  assert.deepEqual(detail?.checkOptions, ["Сам сотрудник", "Руководитель", "Никто"]);
  assert.equal("checkCorrectIndex" in (detail ?? {}), false);
  assert.equal(await prisma.maxDocumentTraining.count({ where: { documentId: revision.id } }), 1);
  assert.equal((await recordMaxDocumentTraining(learner, courseId, revision.id, "answer", 1)).status, "READ_FIRST");
  assert.equal((await recordMaxDocumentTraining(learner, courseId, revision.id, "view")).status, "VIEWED");
  assert.equal((await recordMaxDocumentTraining(learner, courseId, revision.id, "answer", 0)).status, "INCORRECT");
  assert.equal((await recordMaxDocumentTraining(learner, courseId, revision.id, "answer", 1)).status, "PASSED");
  assert.equal((await recordMaxDocumentTraining(learner, courseId, revision.id, "answer", 1)).attempts, 2);
  assert.equal((await recordMaxDocumentTraining(foreign, courseId, revision.id, "view")).status, "FORBIDDEN");
  const report = await getMaxManagerDocuments(manager, courseId);
  const row = report.documents?.find(({ id }) => id === revision.id);
  assert.equal(row?.trainingRecipients[0].passedAt instanceof Date, true);

  await prisma.user.create({ data: {
    id: lateLearnerId,
    login: lateLearnerId,
    name: "Позднее назначение",
    passwordHash: "unused",
    organizationId,
  } });
  await prisma.maxAccountLink.create({ data: {
    maxUserId: `${lateLearnerId}-max`,
    userId: lateLearnerId,
    organizationId,
  } });
  await prisma.groupMembership.create({ data: { groupId, userId: lateLearnerId } });
  const lateLearner = await linkedPerson(lateLearnerId, organizationId);
  assert.equal(await prisma.maxDocumentTraining.count({ where: { documentId: revision.id, userId: lateLearnerId } }), 0);
  assert.equal((await getMaxCourseDocuments(lateLearner, courseId, revision.id)).document?.training?.viewedAt, null);
  assert.equal((await recordMaxDocumentTraining(lateLearner, courseId, revision.id, "view")).status, "VIEWED");
  await getMaxCourseDocuments(lateLearner, courseId);
  assert.equal(await prisma.maxDocumentTraining.count({ where: { documentId: revision.id, userId: lateLearnerId } }), 1);
  assert.equal(await prisma.maxBotDelivery.count({
    where: { documentId: revision.id, kind: "DOCUMENT_REVISION" },
  }), 2);
  await prisma.maxBotDelivery.updateMany({
    where: { documentId: revision.id, maxUserId: lateLearner.maxUserId },
    data: { createdAt: new Date(0) },
  });
  const sends: number[] = [];
  assert.equal(await deliverNextBotMessage(prismaBotDeliveryRepository, process.env.MAX_BOT_USERNAME,
    async () => assert.fail("welcome should not be sent"),
    async (userId) => { sends.push(userId); return "revision-mid"; }), "sent");
  assert.equal(sends.length, 1);
  const lateDelivery = await prisma.maxBotDelivery.findFirstOrThrow({
    where: { documentId: revision.id, maxUserId: lateLearner.maxUserId },
  });
  assert.equal(lateDelivery.status, "SENT");
  assert.equal(await prisma.maxBotDelivery.count({
    where: { documentId: revision.id, maxUserId: learner.maxUserId, status: "PENDING" },
  }), 1);

  await prisma.maxBotDelivery.update({
    where: { eventKey: lateDelivery.eventKey },
    data: { finishedAt: new Date(0) },
  });
  assert.equal(await deliverNextBotMessage(prismaBotDeliveryRepository, process.env.MAX_BOT_USERNAME,
    async () => assert.fail("welcome should not be sent"),
    async (userId) => { sends.push(userId); return "original-learner-mid"; }), "sent");
  assert.equal(sends.length, 2);
  assert.equal(await prisma.maxBotDelivery.count({
    where: { documentId: revision.id, status: "SENT" },
  }), 2);
});
