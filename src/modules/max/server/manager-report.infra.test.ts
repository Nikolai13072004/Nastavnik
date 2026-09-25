import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { after, test } from "node:test";
import prisma from "@/lib/prisma";
import { canViewMaxManagerReport, getMaxManagerReport } from "./manager-report";

const prefix = `max-report-${randomUUID()}`;
const ownOrg = `${prefix}-own`;
const otherOrg = `${prefix}-other`;
const managerId = `${prefix}-manager`;
const ownLearnerId = `${prefix}-learner`;
const activeGroupLearnerId = `${prefix}-active-group`;
const expiredDirectLearnerId = `${prefix}-expired-direct`;
const expiredGroupLearnerId = `${prefix}-expired-group`;
const otherLearnerId = `${prefix}-other-learner`;
const otherOwnerId = `${prefix}-other-owner`;
const ownCourseId = `${prefix}-course`;
const otherCourseId = `${prefix}-other-course`;
const unscopedCourseId = `${prefix}-unscoped-course`;
const maxUserId = `${prefix}-max`;
const groupId = `${prefix}-group`;
const expiredGroupId = `${prefix}-expired-group-assignment`;

after(async () => {
  await prisma.user.deleteMany({ where: { id: { in: [
    managerId, ownLearnerId, activeGroupLearnerId, expiredDirectLearnerId,
    expiredGroupLearnerId, otherLearnerId, otherOwnerId,
  ] } } });
  await prisma.course.deleteMany({ where: { id: { in: [ownCourseId, otherCourseId, unscopedCourseId] } } });
  await prisma.group.deleteMany({ where: { id: { in: [groupId, expiredGroupId] } } });
  await prisma.organization.deleteMany({ where: { id: { in: [ownOrg, otherOrg] } } });
  await prisma.$disconnect();
});

test("MAX HR report limits both courses and learner results to the current organization", async () => {
  await prisma.organization.createMany({ data: [
    { id: ownOrg, name: ownOrg },
    { id: otherOrg, name: otherOrg },
  ] });
  await prisma.user.createMany({ data: [
    { id: managerId, login: managerId, name: "HR", passwordHash: "test", role: "HR", organizationId: ownOrg },
    { id: ownLearnerId, login: ownLearnerId, name: "Our employee", passwordHash: "test", organizationId: ownOrg },
    { id: activeGroupLearnerId, login: activeGroupLearnerId, name: "Active group", passwordHash: "test", organizationId: ownOrg },
    { id: expiredDirectLearnerId, login: expiredDirectLearnerId, name: "Expired direct", passwordHash: "test", organizationId: ownOrg },
    { id: expiredGroupLearnerId, login: expiredGroupLearnerId, name: "Expired group", passwordHash: "test", organizationId: ownOrg },
    { id: otherLearnerId, login: otherLearnerId, name: "Foreign employee", passwordHash: "test", organizationId: otherOrg },
    { id: otherOwnerId, login: otherOwnerId, name: "Foreign author", passwordHash: "test", organizationId: otherOrg },
  ] });
  await prisma.maxAccountLink.create({ data: { maxUserId, userId: managerId, organizationId: ownOrg } });
  await prisma.course.createMany({ data: [
    { id: ownCourseId, title: "Our course", status: "PUBLISHED", ownerId: managerId, organizationId: ownOrg },
    { id: otherCourseId, title: "Foreign course", status: "PUBLISHED", ownerId: otherOwnerId, organizationId: otherOrg },
    { id: unscopedCourseId, title: "Unscoped course", status: "PUBLISHED", ownerId: managerId },
  ] });
  await prisma.group.createMany({ data: [
    { id: groupId, name: groupId },
    { id: expiredGroupId, name: expiredGroupId },
  ] });
  await prisma.groupMembership.createMany({ data: [
    { groupId, userId: activeGroupLearnerId },
    { groupId, userId: expiredDirectLearnerId },
    { groupId: expiredGroupId, userId: expiredGroupLearnerId },
  ] });
  await prisma.courseGroupAssignment.createMany({ data: [
    { groupId, courseId: ownCourseId, expiresAt: null },
    { groupId: expiredGroupId, courseId: ownCourseId, expiresAt: new Date(0) },
  ] });
  await prisma.courseUserAssignment.createMany({ data: [
    { courseId: ownCourseId, userId: ownLearnerId },
    { courseId: ownCourseId, userId: expiredDirectLearnerId, expiresAt: new Date(0) },
    { courseId: ownCourseId, userId: otherLearnerId },
    { courseId: otherCourseId, userId: otherLearnerId },
  ] });

  const link = await prisma.maxAccountLink.findUniqueOrThrow({ where: { maxUserId } });
  const identity = { maxUserId, userId: managerId, organizationId: ownOrg, linkedAt: link.createdAt.toISOString() };
  assert.equal(await canViewMaxManagerReport(maxUserId), true);

  const result = await getMaxManagerReport(identity, ownCourseId);
  assert.deepEqual(result.courses, [{ id: ownCourseId, title: "Our course" }]);
  assert.deepEqual(result.report?.learners.map(({ id }) => id).sort(), [activeGroupLearnerId, ownLearnerId].sort());
  assert.equal(result.report?.assignedCount, 2);
  assert.equal(result.report?.truncated, false);
  assert.deepEqual(await getMaxManagerReport(identity, otherCourseId), { error: "NOT_FOUND" });
  assert.deepEqual(await getMaxManagerReport(identity, unscopedCourseId), { error: "NOT_FOUND" });

  await prisma.user.update({ where: { id: managerId }, data: { role: "USER" } });
  assert.equal(await canViewMaxManagerReport(maxUserId), false);
  assert.deepEqual(await getMaxManagerReport(identity, ownCourseId), { error: "FORBIDDEN" });

  await prisma.user.update({ where: { id: managerId }, data: { role: "HR", organizationId: otherOrg } });
  assert.deepEqual(await getMaxManagerReport(identity, ownCourseId), { error: "FORBIDDEN" });
});
