import "server-only";

import prisma from "@/lib/prisma";
import type { Prisma } from "@prisma/client";
import type { MaxSessionIdentity } from "../infrastructure/learner-session";
import { findReportManager } from "./manager-report";
import {
  aggregateQuestionErrors,
  describeAttemptQuestions,
} from "../application/hr-analytics";
import { resolveEnrollmentAccess } from "@/modules/enrollment/domain/enrollment-access";

export async function requireMaxReportManager(
  identity: MaxSessionIdentity,
  client: Pick<
    Prisma.TransactionClient,
    "maxAccountLink" | "roleProfile"
  > = prisma,
) {
  const manager = await findReportManager(identity.maxUserId, client);
  return manager &&
    manager.userId === identity.userId &&
    manager.organizationId === identity.organizationId &&
    manager.linkedAt === identity.linkedAt
    ? manager
    : null;
}

export async function getMaxLearnerHistory(
  identity: MaxSessionIdentity,
  learnerId: string,
  page = 0,
) {
  const manager = await requireMaxReportManager(identity);
  if (!manager) return { error: "FORBIDDEN" } as const;
  const learner = await prisma.user.findFirst({
    where: { id: learnerId, organizationId: manager.organizationId },
    select: { id: true, name: true, status: true },
  });
  if (!learner) return { error: "NOT_FOUND" } as const;
  const scope = { organizationId: manager.organizationId };
  const [courses, attempts, totalAttempts, documents] = await Promise.all([
    prisma.course.findMany({
      where: {
        ...scope,
        OR: [
          { directAssignments: { some: { userId: learnerId } } },
          {
            groupAssignments: {
              some: { group: { memberships: { some: { userId: learnerId } } } },
            },
          },
          {
            items: {
              some: {
                quiz: { is: { attempts: { some: { userId: learnerId } } } },
              },
            },
          },
          { certificates: { some: { userId: learnerId } } },
        ],
      },
      orderBy: [{ title: "asc" }, { id: "asc" }],
      take: 101,
      select: {
        id: true,
        title: true,
        status: true,
        directAssignments: {
          where: { userId: learnerId },
          select: { expiresAt: true },
        },
        groupAssignments: {
          where: { group: { memberships: { some: { userId: learnerId } } } },
          select: { expiresAt: true },
        },
        maxStudyPlans: {
          where: { userId: learnerId },
          select: { dueAt: true, remindersEnabled: true },
        },
        certificates: {
          where: { userId: learnerId },
          select: { serial: true, status: true, issuedAt: true },
        },
        items: {
          where: { archivedAt: null },
          select: {
            id: true,
            title: true,
            type: true,
            views: {
              where: { userId: learnerId },
              select: { progressPercent: true, viewedAt: true },
            },
          },
        },
      },
    }),
    prisma.quizAttempt.findMany({
      where: { userId: learnerId, quiz: { courseItem: { course: scope } } },
      orderBy: [{ createdAt: "desc" }, { id: "desc" }],
      skip: page * 20,
      take: 20,
      select: {
        id: true,
        attemptNumber: true,
        outcome: true,
        score: true,
        maxScore: true,
        correctAnswers: true,
        totalQuestions: true,
        createdAt: true,
        completedAt: true,
        answers: true,
        questionSnapshot: true,
        quiz: {
          select: {
            courseItem: {
              select: {
                title: true,
                course: { select: { id: true, title: true } },
              },
            },
          },
        },
      },
    }),
    prisma.quizAttempt.count({
      where: { userId: learnerId, quiz: { courseItem: { course: scope } } },
    }),
    prisma.maxDocumentTraining.findMany({
      where: { userId: learnerId, ...scope, course: scope },
      orderBy: [{ assignedAt: "desc" }, { id: "desc" }],
      take: 201,
      select: {
        assignedAt: true,
        viewedAt: true,
        passedAt: true,
        attempts: true,
        document: {
          select: {
            id: true,
            title: true,
            versionNumber: true,
            revokedAt: true,
          },
        },
      },
    }),
  ]);
  return {
    learner,
    courses: courses
      .slice(0, 100)
      .map(
        ({
          directAssignments,
          groupAssignments,
          maxStudyPlans,
          ...course
        }) => ({
          ...course,
          access: resolveEnrollmentAccess({
            directExpiries: directAssignments.map(({ expiresAt }) => expiresAt),
            groupExpiries: groupAssignments.map(({ expiresAt }) => expiresAt),
          }).status,
          plan: maxStudyPlans[0] ?? null,
        }),
      ),
    coursesTruncated: courses.length > 100,
    attempts: attempts.map(
      ({ answers, questionSnapshot, quiz, ...attempt }) => ({
        ...attempt,
        courseTitle: quiz.courseItem.course.title,
        quizTitle: quiz.courseItem.title,
        questions: describeAttemptQuestions(
          questionSnapshot,
          answers,
          attempt.outcome,
        ),
      }),
    ),
    totalAttempts,
    page,
    documents: documents.slice(0, 200),
    documentsTruncated: documents.length > 200,
  };
}

export async function getMaxQuestionErrors(
  identity: MaxSessionIdentity,
  courseId: string,
) {
  const manager = await requireMaxReportManager(identity);
  if (!manager) return { error: "FORBIDDEN" } as const;
  const course = await prisma.course.findFirst({
    where: { id: courseId, organizationId: manager.organizationId },
  });
  if (!course) return { error: "NOT_FOUND" } as const;
  const attempts = await prisma.quizAttempt.findMany({
    where: {
      user: { organizationId: manager.organizationId },
      quiz: { courseItem: { courseId } },
      outcome: { in: ["PASSED", "FAILED"] },
    },
    orderBy: [{ completedAt: "desc" }, { id: "desc" }],
    take: 2001,
    select: { answers: true, questionSnapshot: true, outcome: true },
  });
  const questions = aggregateQuestionErrors(attempts.slice(0, 2000));
  return {
    attemptsAnalyzed: Math.min(attempts.length, 2000),
    truncated: attempts.length > 2000,
    questions: questions.slice(0, 100),
    questionsTruncated: questions.length > 100,
  };
}
