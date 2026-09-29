import "server-only";

import { Prisma } from "@prisma/client";
import prisma from "@/lib/prisma";
import type { MaxSessionIdentity } from "@/modules/max/infrastructure/learner-session";
import { requireMaxReportManager } from "@/modules/max/server/manager-learning-history";
import { resolveEnrollmentAccess } from "../domain/enrollment-access";

export async function saveMaxStudyPlan(
  identity: MaxSessionIdentity,
  input: {
    courseId: string;
    learnerId: string;
    dueAt: Date | null;
    remindersEnabled: boolean;
  },
) {
  const manager = await requireMaxReportManager(identity);
  if (!manager?.canAssign) return { error: "FORBIDDEN" } as const;
  for (let attempt = 0; ; attempt++) {
    try {
      return await prisma.$transaction(
        async (tx) => {
          const current = await requireMaxReportManager(identity, tx);
          if (!current?.canAssign) return { error: "FORBIDDEN" } as const;
          const learner = await tx.user.findFirst({
            where: {
              id: input.learnerId,
              organizationId: identity.organizationId,
              status: "ACTIVE",
            },
            select: {
              directCourseAssignments: {
                where: { courseId: input.courseId },
                select: { expiresAt: true },
              },
              groupMemberships: {
                select: {
                  group: {
                    select: {
                      courseAssignments: {
                        where: { courseId: input.courseId },
                        select: { expiresAt: true },
                      },
                    },
                  },
                },
              },
            },
          });
          const course = await tx.course.findFirst({
            where: {
              id: input.courseId,
              organizationId: identity.organizationId,
              status: "PUBLISHED",
            },
          });
          if (!learner || !course) return { error: "NOT_FOUND" } as const;
          const access = resolveEnrollmentAccess({
            directExpiries: learner.directCourseAssignments.map(
              ({ expiresAt }) => expiresAt,
            ),
            groupExpiries: learner.groupMemberships.flatMap(({ group }) =>
              group.courseAssignments.map(({ expiresAt }) => expiresAt),
            ),
          });
          if (!access.isActive) return { error: "NOT_ASSIGNED" } as const;
          const data = {
            dueAt: input.dueAt,
            remindersEnabled: Boolean(input.dueAt && input.remindersEnabled),
          };
          await tx.maxStudyPlan.upsert({
            where: {
              courseId_userId: {
                courseId: input.courseId,
                userId: input.learnerId,
              },
            },
            create: {
              organizationId: identity.organizationId,
              courseId: input.courseId,
              userId: input.learnerId,
              ...data,
            },
            update: data,
          });
          await tx.auditLogEvent.create({
            data: {
              actorId: identity.userId,
              action: "max_study:deadline",
              objectType: "course",
              objectId: course.id,
              metadataJson: JSON.stringify({
                learnerId: input.learnerId,
                ...data,
              }),
            },
          });
          return { status: "SAVED" } as const;
        },
        { isolationLevel: "Serializable" },
      );
    } catch (error) {
      if (
        error instanceof Prisma.PrismaClientKnownRequestError &&
        error.code === "P2034" &&
        attempt < 2
      )
        continue;
      throw error;
    }
  }
}
