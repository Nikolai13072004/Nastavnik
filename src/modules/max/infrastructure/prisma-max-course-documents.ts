import "server-only";

import { createHash } from "node:crypto";
import { Prisma } from "@prisma/client";
import prisma from "@/lib/prisma";
import { getPermissionsForRoleNames } from "@/lib/role-profiles";
import { PERMISSIONS } from "@/lib/roles";
import { resolveEnrollmentAccess } from "@/modules/enrollment/domain/enrollment-access";
import type { MaxLearnerIdentity } from "../application/list-courses";
import type { MaxDocumentCommands, MaxDocumentDraft } from "../application/manage-course-documents";
import { enqueueMaxRevisionNotifications } from "./prisma-revision-notifications";

type DocumentClient = Pick<Prisma.TransactionClient, "maxAccountLink" | "roleProfile">;

async function activeRecipients(client: Prisma.TransactionClient, organizationId: string, courseId: string) {
  const now = new Date();
  const users = await client.user.findMany({
    where: {
      organizationId,
      status: "ACTIVE",
      OR: [
        { directCourseAssignments: { some: { courseId } } },
        { groupMemberships: { some: { group: { courseAssignments: { some: { courseId } } } } } },
      ],
    },
    orderBy: { id: "asc" },
    take: 1001,
    select: {
      id: true,
      name: true,
      directCourseAssignments: { where: { courseId }, select: { expiresAt: true } },
      groupMemberships: { select: { group: { select: {
        courseAssignments: { where: { courseId }, select: { expiresAt: true } },
      } } } },
    },
  });
  if (users.length > 1000) return null;
  return users.filter((user) => resolveEnrollmentAccess({
    directExpiries: user.directCourseAssignments.map(({ expiresAt }) => expiresAt),
    groupExpiries: user.groupMemberships.flatMap(({ group }) =>
      group.courseAssignments.map(({ expiresAt }) => expiresAt)),
    now,
  }).isActive).map(({ id, name }) => ({ id, name }));
}

async function findManager(identity: MaxLearnerIdentity, client: DocumentClient) {
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
          role: true,
          status: true,
          organizationId: true,
          userRoles: { select: { roleProfile: { select: { name: true } } } },
        },
      },
    },
  });
  if (!link || link.userId !== identity.userId || link.organizationId !== identity.organizationId ||
      link.createdAt.toISOString() !== identity.linkedAt || link.user.status !== "ACTIVE" ||
      link.user.organizationId !== identity.organizationId) {
    return null;
  }

  const roles = [link.user.role, ...link.user.userRoles.map(({ roleProfile }) => roleProfile.name)];
  const permissions = await getPermissionsForRoleNames(roles, client);
  if (!permissions.includes(PERMISSIONS.COURSES_MANAGE_ASSIGNMENTS) ||
      !permissions.includes(PERMISSIONS.REPORTS_VIEW)) {
    return null;
  }
  return { id: link.user.id, login: link.user.login, name: link.user.name };
}

async function transactWithRetry<T>(work: (client: Prisma.TransactionClient) => Promise<T>) {
  for (let attempt = 0; ; attempt++) {
    try {
      return await prisma.$transaction(work, { isolationLevel: "Serializable" });
    } catch (error) {
      if (error instanceof Prisma.PrismaClientKnownRequestError && error.code === "P2034" && attempt < 2) {
        continue;
      }
      throw error;
    }
  }
}

export const prismaMaxCourseDocuments: MaxDocumentCommands = {
  async upload(identity, draft: MaxDocumentDraft) {
    return transactWithRetry(async (client) => {
      const actor = await findManager(identity, client);
      if (!actor) return "FORBIDDEN" as const;
      const course = await client.course.findFirst({
        where: { id: draft.courseId, organizationId: identity.organizationId, status: "PUBLISHED" },
        select: { id: true },
      });
      if (!course) return "NOT_FOUND" as const;
      const count = await client.maxCourseDocument.count({
        where: { organizationId: identity.organizationId, courseId: course.id, revokedAt: null },
      });
      if (count >= 20) return "LIMIT_REACHED" as const;

      const previous = draft.supersedesId ? await client.maxCourseDocument.findFirst({
        where: {
          id: draft.supersedesId,
          organizationId: identity.organizationId,
          courseId: course.id,
          approvedAt: { not: null },
          revokedAt: null,
        },
        select: { id: true, versionNumber: true, contentHash: true },
      }) : null;
      if (draft.supersedesId && !previous) return "CONFLICT" as const;
      if (previous && previous.contentHash === createHash("sha256").update(draft.contentText).digest("hex")) {
        return "CONFLICT" as const;
      }
      if (previous && await client.maxCourseDocument.findUnique({
        where: { supersedesId: previous.id }, select: { id: true },
      })) return "CONFLICT" as const;

      const document = await client.maxCourseDocument.create({
        data: {
          organizationId: identity.organizationId,
          courseId: course.id,
          title: draft.title,
          sourceName: draft.sourceName,
          contentText: draft.contentText,
          contentHash: createHash("sha256").update(draft.contentText).digest("hex"),
          supersedesId: previous?.id,
          versionNumber: previous ? previous.versionNumber + 1 : 1,
          changeSummary: draft.changeSummary,
          checkQuestion: draft.checkQuestion,
          checkOptionsJson: draft.checkOptions ? JSON.stringify(draft.checkOptions) : null,
          checkCorrectIndex: draft.checkCorrectIndex,
          uploadedById: actor.id,
        },
        select: { id: true },
      });
      await client.auditLogEvent.create({
        data: {
          actorId: actor.id,
          actorLogin: actor.login,
          actorName: actor.name,
          action: "max_document:upload",
          objectType: "max_course_document",
          objectId: document.id,
          objectLabel: draft.title,
          metadataJson: JSON.stringify({ courseId: course.id, organizationId: identity.organizationId }),
        },
      });
      return "CREATED" as const;
    });
  },

  async setPublished(identity, courseId, documentId, publish, expectedRecipientIds) {
    return transactWithRetry(async (client) => {
      const actor = await findManager(identity, client);
      if (!actor) return "FORBIDDEN" as const;
      const now = new Date();
      const documentBefore = await client.maxCourseDocument.findFirst({
        where: { id: documentId, organizationId: identity.organizationId, courseId, revokedAt: null },
        select: { id: true, supersedesId: true, approvedAt: true, title: true },
      });
      if (!documentBefore) return "NOT_FOUND" as const;
      if (!publish && documentBefore.supersedesId && !documentBefore.approvedAt) {
        await client.maxCourseDocument.delete({ where: { id: documentId } });
        await client.auditLogEvent.create({ data: {
          actorId: actor.id,
          actorLogin: actor.login,
          actorName: actor.name,
          action: "max_document:discard_draft",
          objectType: "max_course_document",
          objectId: documentId,
          objectLabel: documentBefore.title,
          metadataJson: JSON.stringify({ courseId, organizationId: identity.organizationId }),
        } });
        return "UPDATED" as const;
      }
      let recipients: Array<{ id: string; name: string }> = [];
      if (publish && documentBefore.supersedesId) {
        if (!expectedRecipientIds) return "AUDIENCE_CHANGED" as const;
        const audience = await activeRecipients(client, identity.organizationId, courseId);
        if (!audience || audience.length > 200) return "AUDIENCE_TOO_LARGE" as const;
        recipients = audience;
        const actualIds = recipients.map(({ id }) => id).sort();
        const confirmedIds = [...expectedRecipientIds].sort();
        if (actualIds.length === 0 || actualIds.length !== expectedRecipientIds.length ||
            actualIds.some((id, index) => id !== confirmedIds[index])) {
          return "AUDIENCE_CHANGED" as const;
        }
        const replaced = await client.maxCourseDocument.updateMany({
          where: {
            id: documentBefore.supersedesId,
            organizationId: identity.organizationId,
            courseId,
            approvedAt: { not: null },
            revokedAt: null,
          },
          data: { revokedAt: now },
        });
        if (replaced.count !== 1) throw new Error("Document revision changed during publication");
      }
      const updated = await client.maxCourseDocument.updateMany({
        where: {
          id: documentId,
          organizationId: identity.organizationId,
          courseId,
          revokedAt: null,
          ...(publish ? { approvedAt: null, course: { status: "PUBLISHED", organizationId: identity.organizationId } } : {}),
        },
        data: publish ? { approvedAt: now, approvedById: actor.id } : { revokedAt: now },
      });
      if (updated.count !== 1) {
        if (publish && documentBefore.supersedesId) throw new Error("Document changed during publication");
        return "NOT_FOUND" as const;
      }
      if (publish && recipients.length > 0) {
        await client.maxDocumentTraining.createMany({
          data: recipients.map(({ id }) => ({
            organizationId: identity.organizationId,
            courseId,
            documentId,
            userId: id,
          })),
        });
        await enqueueMaxRevisionNotifications(
          client, identity.organizationId, documentId, recipients.map(({ id }) => id),
        );
      }
      await client.auditLogEvent.create({
        data: {
          actorId: actor.id,
          actorLogin: actor.login,
          actorName: actor.name,
          action: publish ? "max_document:publish" : "max_document:revoke",
          objectType: "max_course_document",
          objectId: documentId,
          objectLabel: documentBefore.title,
          metadataJson: JSON.stringify({ courseId, organizationId: identity.organizationId }),
        },
      });
      return "UPDATED" as const;
    });
  },
};

export async function listMaxManagerDocuments(identity: MaxLearnerIdentity, courseId: string) {
  if (!await findManager(identity, prisma)) return null;
  const course = await prisma.course.findFirst({
    where: { id: courseId, organizationId: identity.organizationId, status: "PUBLISHED" },
    select: { id: true },
  });
  if (!course) return null;
  return prisma.maxCourseDocument.findMany({
    where: { organizationId: identity.organizationId, courseId },
    orderBy: [{ createdAt: "desc" }, { id: "desc" }],
    take: 100,
    select: {
      id: true, title: true, sourceName: true, approvedAt: true, revokedAt: true, createdAt: true,
      supersedesId: true, versionNumber: true, changeSummary: true,
      trainingRecipients: { select: { userId: true, viewedAt: true, passedAt: true, attempts: true,
        user: { select: { name: true } } } },
    },
  });
}

export async function getMaxDocumentAudience(identity: MaxLearnerIdentity, courseId: string) {
  if (!await findManager(identity, prisma)) return null;
  const course = await prisma.course.findFirst({
    where: { id: courseId, organizationId: identity.organizationId, status: "PUBLISHED" },
    select: { id: true },
  });
  if (!course) return null;
  return activeRecipients(prisma, identity.organizationId, courseId);
}

export async function listMaxPublishedDocuments(organizationId: string, courseId: string) {
  return prisma.maxCourseDocument.findMany({
    where: { organizationId, courseId, approvedAt: { not: null }, revokedAt: null },
    orderBy: [{ approvedAt: "desc" }, { id: "desc" }],
    take: 20,
    select: { id: true, title: true, sourceName: true, approvedAt: true, supersedesId: true,
      versionNumber: true, changeSummary: true },
  });
}

export async function ensureCurrentMaxDocumentTraining(organizationId: string, courseId: string, userId: string) {
  await prisma.$transaction(async (client) => {
    const documents = await client.maxCourseDocument.findMany({
      where: {
        organizationId,
        courseId,
        supersedesId: { not: null },
        checkCorrectIndex: { not: null },
        approvedAt: { not: null },
        revokedAt: null,
      },
      select: { id: true },
    });
    for (const document of documents) {
      const result = await client.maxDocumentTraining.createMany({
        data: [{ organizationId, courseId, documentId: document.id, userId }],
        skipDuplicates: true,
      });
      if (result.count === 1) {
        await enqueueMaxRevisionNotifications(client, organizationId, document.id, [userId]);
      }
    }
  });
}

export async function readMaxPublishedDocument(organizationId: string, courseId: string, documentId: string,
  userId: string) {
  return prisma.maxCourseDocument.findFirst({
    where: { id: documentId, organizationId, courseId, approvedAt: { not: null }, revokedAt: null },
    select: { id: true, title: true, sourceName: true, contentText: true, contentHash: true,
      supersedesId: true, versionNumber: true, changeSummary: true, checkQuestion: true,
      checkOptionsJson: true,
      trainingRecipients: { where: { userId }, select: { viewedAt: true, passedAt: true, attempts: true } } },
  });
}

export async function readMaxManagerDocument(identity: MaxLearnerIdentity, courseId: string, documentId: string) {
  if (!await findManager(identity, prisma)) return null;
  return prisma.maxCourseDocument.findFirst({
    where: { id: documentId, organizationId: identity.organizationId, courseId },
    select: { id: true, title: true, sourceName: true, contentText: true, contentHash: true,
      supersedesId: true, versionNumber: true, changeSummary: true, checkQuestion: true,
      checkOptionsJson: true, checkCorrectIndex: true },
  });
}
