import { createHash } from "node:crypto";
import type { Prisma } from "@prisma/client";
import prisma from "@/lib/prisma";
import { resolveEnrollmentAccess } from "@/modules/enrollment/domain/enrollment-access";
import { shouldRemind } from "../application/hr-analytics";

type StudyClient = Pick<Prisma.TransactionClient, "maxStudyPlan" | "user">;
const DAY_MS = 86400000;

export async function activeMaxStudyReminder(
  client: StudyClient,
  planId: string,
  maxUserId: string,
  now: Date,
) {
  const plan = await client.maxStudyPlan.findUnique({
    where: { id: planId },
    include: {
      user: {
        select: {
          status: true,
          organizationId: true,
          maxLink: { select: { maxUserId: true, organizationId: true } },
        },
      },
      course: { select: { title: true, status: true, organizationId: true } },
    },
  });
  if (
    !plan ||
    !plan.remindersEnabled ||
    !plan.dueAt ||
    !shouldRemind(plan.dueAt, now) ||
    plan.user.status !== "ACTIVE" ||
    plan.course.status !== "PUBLISHED" ||
    plan.user.organizationId !== plan.organizationId ||
    plan.course.organizationId !== plan.organizationId ||
    plan.user.maxLink?.maxUserId !== maxUserId ||
    plan.user.maxLink.organizationId !== plan.organizationId
  )
    return null;

  // Query only this employee and course, not every certificate in the course.
  const learner = await client.user.findUnique({
    where: { id: plan.userId },
    select: {
      directCourseAssignments: {
        where: { courseId: plan.courseId },
        select: { expiresAt: true },
      },
      groupMemberships: {
        select: {
          group: {
            select: {
              courseAssignments: {
                where: { courseId: plan.courseId },
                select: { expiresAt: true },
              },
            },
          },
        },
      },
      certificates: {
        where: { courseId: plan.courseId, status: "ISSUED" },
        take: 1,
        select: { id: true },
      },
      maxDocumentTrainings: {
        where: {
          courseId: plan.courseId,
          passedAt: null,
          document: { approvedAt: { not: null }, revokedAt: null },
        },
        take: 1,
        select: { id: true },
      },
    },
  });
  if (!learner) return null;
  const access = resolveEnrollmentAccess({
    directExpiries: learner.directCourseAssignments.map(
      ({ expiresAt }) => expiresAt,
    ),
    groupExpiries: learner.groupMemberships.flatMap(({ group }) =>
      group.courseAssignments.map(({ expiresAt }) => expiresAt),
    ),
    now,
  });
  if (
    !access.isActive ||
    (learner.certificates.length > 0 &&
      learner.maxDocumentTrainings.length === 0)
  ) {
    return null;
  }
  return { ...plan, dueAt: plan.dueAt };
}

export async function enqueueMaxStudyReminders(
  botUsername: string,
  now = new Date(),
) {
  let cursor: string | undefined;
  let queued = 0;
  do {
    const plans = await prisma.maxStudyPlan.findMany({
      where: {
        remindersEnabled: true,
        dueAt: { lte: new Date(now.getTime() + 3 * DAY_MS) },
      },
      orderBy: { id: "asc" },
      take: 100,
      ...(cursor ? { cursor: { id: cursor }, skip: 1 } : {}),
      select: {
        id: true,
        user: { select: { maxLink: { select: { maxUserId: true } } } },
      },
    });
    for (const row of plans) {
      const maxUserId = row.user.maxLink?.maxUserId;
      if (!maxUserId) continue;
      queued += await prisma.$transaction(async (tx) => {
        await tx.$executeRaw`SELECT pg_advisory_xact_lock(7125, hashtext(${`${botUsername}:${row.id}`}))`;
        if (!(await activeMaxStudyReminder(tx, row.id, maxUserId, now)))
          return 0;
        const recent = await tx.maxBotDelivery.findFirst({
          where: {
            botUsername,
            studyPlanId: row.id,
            kind: "STUDY_REMINDER",
            OR: [
              { status: { in: ["PENDING", "SENDING", "UNCERTAIN"] } },
              { createdAt: { gte: new Date(now.getTime() - DAY_MS) } },
              {
                status: "SENT",
                finishedAt: { gte: new Date(now.getTime() - DAY_MS) },
              },
            ],
          },
          select: { eventKey: true },
        });
        if (recent) return 0;
        const eventKey = createHash("sha256")
          .update(
            JSON.stringify([
              botUsername,
              "study-reminder",
              row.id,
              now.toISOString(),
            ]),
          )
          .digest("hex");
        const result = await tx.maxBotDelivery.createMany({
          data: [
            {
              eventKey,
              botUsername,
              maxUserId,
              kind: "STUDY_REMINDER",
              studyPlanId: row.id,
              createdAt: now,
            },
          ],
          skipDuplicates: true,
        });
        return result.count;
      });
    }
    if (plans.length < 100) break;
    cursor = plans.at(-1)!.id;
  } while (cursor);
  return queued;
}
