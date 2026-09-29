import "server-only";

import type { Prisma } from "@prisma/client";
import prisma from "@/lib/prisma";
import { parsePublishedCourseSnapshot } from "@/lib/course-content";
import { getPermissionsForRoleNames } from "@/lib/role-profiles";
import { PERMISSIONS, STANDARD_ROLE_NAMES } from "@/lib/roles";
import { resolveEnrollmentAccess } from "@/modules/enrollment/domain/enrollment-access";
import type { MaxSessionIdentity } from "../infrastructure/learner-session";

export async function findReportManager(maxUserId: string, client: Pick<Prisma.TransactionClient, "maxAccountLink" | "roleProfile"> = prisma) {
  const link = await client.maxAccountLink.findUnique({
    where: { maxUserId },
    select: {
      userId: true,
      organizationId: true,
      createdAt: true,
      user: { select: {
        status: true,
        organizationId: true,
        role: true,
        userRoles: { select: { roleProfile: { select: { name: true } } } },
      } },
    },
  });
  if (!link || link.user.status !== "ACTIVE" || link.user.organizationId !== link.organizationId) return null;

  const roles = [link.user.role, ...link.user.userRoles.map(({ roleProfile }) => roleProfile.name)];
  const permissions = await getPermissionsForRoleNames(roles, client);
  return permissions.includes(PERMISSIONS.REPORTS_VIEW)
    ? {
        userId: link.userId,
        organizationId: link.organizationId,
        linkedAt: link.createdAt.toISOString(),
        canAssign: permissions.includes(PERMISSIONS.COURSES_MANAGE_ASSIGNMENTS) &&
          permissions.includes(PERMISSIONS.USERS_VIEW),
        canCreate: permissions.includes(PERMISSIONS.USERS_CREATE) &&
          permissions.includes(PERMISSIONS.USERS_VIEW),
      }
    : null;
}

export async function canViewMaxManagerReport(maxUserId: string) {
  return Boolean(await findReportManager(maxUserId));
}

export async function getMaxManagerReport(identity: MaxSessionIdentity, courseId: string | null) {
  const manager = await findReportManager(identity.maxUserId);
  if (!manager || manager.userId !== identity.userId ||
      manager.organizationId !== identity.organizationId || manager.linkedAt !== identity.linkedAt) {
    return { error: "FORBIDDEN" as const };
  }

  // Explicit tenant ownership is stable even if a course author changes jobs.
  // Historical courses without a reviewed organization remain hidden.
  const scope = { status: "PUBLISHED", organizationId: manager.organizationId };
  const [courseRows, learnerDirectory] = await Promise.all([
    prisma.course.findMany({
      where: scope,
      orderBy: [{ publishedAt: "desc" }, { id: "asc" }],
      take: 50,
      select: { id: true, title: true, publishedSnapshotJson: true },
    }),
    manager.canAssign || manager.canCreate ? prisma.user.findMany({
      where: { organizationId: manager.organizationId, status: "ACTIVE" },
      orderBy: [{ name: "asc" }, { id: "asc" }],
      take: 101,
      select: {
        id: true,
        name: true,
        role: true,
        userRoles: { select: { roleProfile: { select: { name: true } } } },
        maxLink: { select: { maxUserId: true } },
      },
    }) : Promise.resolve([]),
  ]);
  const assignment = {
    canAssign: manager.canAssign,
    canCreate: manager.canCreate,
    learners: learnerDirectory.slice(0, 100).map((learner) => ({
      id: learner.id,
      name: learner.name,
      canIssueCode: learner.role === STANDARD_ROLE_NAMES.STUDENT && !learner.maxLink &&
        learner.userRoles.every(({ roleProfile }) => roleProfile.name === STANDARD_ROLE_NAMES.STUDENT),
    })),
    directoryTruncated: learnerDirectory.length > 100,
  };
  const courses = courseRows.map((course) => ({
    id: course.id,
    title: parsePublishedCourseSnapshot(course.publishedSnapshotJson)?.title ?? course.title,
  }));
  if (!courseId) return { courses, report: null, assignment };

  const course = await prisma.course.findFirst({
    where: { ...scope, id: courseId },
    select: {
      id: true,
      title: true,
      publishedSnapshotJson: true,
      items: { where: { type: "QUIZ" }, select: { quiz: { select: { id: true } } } },
    },
  });
  if (!course) return { error: "NOT_FOUND" as const };

  const snapshot = parsePublishedCourseSnapshot(course.publishedSnapshotJson);
  const quizIds = snapshot
    ? snapshot.items.flatMap(({ quiz }) => quiz ? [quiz.id] : [])
    : course.items.flatMap(({ quiz }) => quiz ? [quiz.id] : []);
  const now = new Date();
  const activeDirect: Prisma.CourseUserAssignmentWhereInput = {
    courseId,
    OR: [{ expiresAt: null }, { expiresAt: { gt: now } }],
  };
  const activeGroup: Prisma.CourseGroupAssignmentWhereInput = {
    courseId,
    OR: [{ expiresAt: null }, { expiresAt: { gt: now } }],
  };
  const learners = await prisma.user.findMany({
    where: {
      organizationId: manager.organizationId,
      status: "ACTIVE",
      OR: [
        { directCourseAssignments: { some: activeDirect } },
        {
          directCourseAssignments: { none: { courseId } },
          groupMemberships: { some: { group: { courseAssignments: { some: activeGroup } } } },
        },
      ],
    },
    orderBy: [{ name: "asc" }, { id: "asc" }],
    take: 201,
    select: {
      id: true,
      name: true,
      directCourseAssignments: { where: { courseId }, select: { expiresAt: true } },
      groupMemberships: {
        where: { group: { courseAssignments: { some: { courseId } } } },
        select: { group: { select: {
          courseAssignments: { where: { courseId }, select: { expiresAt: true } },
        } } },
      },
      certificates: { where: { courseId, status: "ISSUED" }, select: { id: true } },
      maxStudyPlans: { where: { courseId }, select: { dueAt: true, remindersEnabled: true } },
      maxDocumentTrainings: {
        where: { courseId, passedAt: null, document: { approvedAt: { not: null }, revokedAt: null } },
        select: { id: true },
      },
      bestQuizResults: {
        where: { quizId: { in: quizIds } },
        select: { quizId: true, status: true },
      },
    },
  });
  const activeLearners = learners.filter((learner) => resolveEnrollmentAccess({
    directExpiries: learner.directCourseAssignments.map(({ expiresAt }) => expiresAt),
    groupExpiries: learner.groupMemberships.flatMap(({ group }) =>
      group.courseAssignments.map(({ expiresAt }) => expiresAt)),
    now,
  }).isActive);
  const truncated = activeLearners.length > 200;
  const rows = activeLearners.slice(0, 200).map((learner) => ({
    id: learner.id,
    name: learner.name,
    completed: learner.certificates.length > 0,
    passedQuizzes: learner.bestQuizResults.filter(({ status }) => status === "PASSED").length,
    dueAt: learner.maxStudyPlans[0]?.dueAt?.toISOString() ?? null,
    remindersEnabled: learner.maxStudyPlans[0]?.remindersEnabled ?? false,
    pendingDocuments: learner.maxDocumentTrainings.length,
  }));

  return {
    courses,
    assignment,
    report: {
      courseId: course.id,
      title: snapshot?.title ?? course.title,
      quizCount: quizIds.length,
      assignedCount: rows.length,
      completedCount: rows.filter(({ completed }) => completed).length,
      truncated,
      learners: rows,
    },
  };
}
