import "server-only";

import { Prisma } from "@prisma/client";
import prisma from "@/lib/prisma";
import { getPermissionsForRoleNames } from "@/lib/role-profiles";
import { PERMISSIONS } from "@/lib/roles";
import { enqueueMaxRevisionNotifications } from "@/modules/max/infrastructure/prisma-revision-notifications";
import type {
  MaxAssignmentRepository,
  MaxAssignmentTransaction,
} from "../application/assign-max-course";

function createTransaction(client: Prisma.TransactionClient): MaxAssignmentTransaction {
  return {
    async findActor(identity) {
      const link = await client.maxAccountLink.findUnique({
        where: { maxUserId: identity.maxUserId },
        select: {
          userId: true,
          organizationId: true,
          createdAt: true,
          user: {
            select: {
              id: true,
              login: true,
              name: true,
              status: true,
              organizationId: true,
              role: true,
              userRoles: { select: { roleProfile: { select: { name: true } } } },
            },
          },
        },
      });
      if (!link || link.userId !== identity.userId ||
          link.organizationId !== identity.organizationId ||
          link.createdAt.toISOString() !== identity.linkedAt ||
          link.user.status !== "ACTIVE" ||
          link.user.organizationId !== identity.organizationId) {
        return null;
      }

      const roles = [link.user.role, ...link.user.userRoles.map(({ roleProfile }) => roleProfile.name)];
      const permissions = await getPermissionsForRoleNames(roles, client);
      if (!permissions.includes(PERMISSIONS.COURSES_MANAGE_ASSIGNMENTS) ||
          !permissions.includes(PERMISSIONS.USERS_VIEW) ||
          !permissions.includes(PERMISSIONS.REPORTS_VIEW)) {
        return null;
      }
      return {
        id: link.user.id,
        login: link.user.login,
        name: link.user.name,
        organizationId: identity.organizationId,
      };
    },
    async findCourse(courseId, organizationId) {
      return client.course.findFirst({
        where: { id: courseId, organizationId, status: "PUBLISHED" },
        select: { id: true, title: true },
      });
    },
    async findLearner(learnerId, organizationId) {
      return client.user.findFirst({
        where: { id: learnerId, organizationId, status: "ACTIVE" },
        select: { id: true },
      });
    },
    async directExpiry(courseId, learnerId) {
      const assignment = await client.courseUserAssignment.findUnique({
        where: { courseId_userId: { courseId, userId: learnerId } },
        select: { expiresAt: true },
      });
      return assignment?.expiresAt;
    },
    async createDirect(courseId, learnerId, actorId) {
      const result = await client.courseUserAssignment.createMany({
        data: [{ courseId, userId: learnerId, assignedById: actorId }],
        skipDuplicates: true,
      });
      return result.count === 1;
    },
    async renewDirect(courseId, learnerId, actorId, now) {
      const result = await client.courseUserAssignment.updateMany({
        where: { courseId, userId: learnerId, expiresAt: { lte: now } },
        data: { expiresAt: null, assignedById: actorId },
      });
      return result.count === 1;
    },
    async assignCurrentDocumentTrainings(courseId, learnerId, organizationId) {
      const documents = await client.maxCourseDocument.findMany({
        where: {
          courseId,
          organizationId,
          supersedesId: { not: null },
          checkCorrectIndex: { not: null },
          approvedAt: { not: null },
          revokedAt: null,
        },
        select: { id: true },
      });
      if (documents.length === 0) return;
      await client.maxDocumentTraining.createMany({
        data: documents.map(({ id }) => ({
          organizationId,
          courseId,
          documentId: id,
          userId: learnerId,
        })),
        skipDuplicates: true,
      });
      for (const document of documents) {
        await enqueueMaxRevisionNotifications(client, organizationId, document.id, [learnerId]);
      }
    },
    async recordAudit(actor, course, learnerId, renewed) {
      await client.auditLogEvent.create({
        data: {
          actorId: actor.id,
          actorLogin: actor.login,
          actorName: actor.name,
          action: "courses:assign",
          objectType: "course",
          objectId: course.id,
          objectLabel: course.title,
          metadataJson: JSON.stringify({
            source: "max_mini_app",
            directUserIds: [learnerId],
            renewed,
            accessExpiresAt: null,
          }),
        },
      });
    },
  };
}

export const prismaMaxCourseAssignmentRepository: MaxAssignmentRepository = {
  async transact(work) {
    for (let attempt = 0; ; attempt++) {
      try {
        return await prisma.$transaction((client) => work(createTransaction(client)), {
          isolationLevel: Prisma.TransactionIsolationLevel.Serializable,
        });
      } catch (error) {
        if (error instanceof Prisma.PrismaClientKnownRequestError && error.code === "P2034" && attempt < 2) {
          continue;
        }
        throw error;
      }
    }
  },
};
