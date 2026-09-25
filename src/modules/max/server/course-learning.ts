import "server-only";

import prisma from "@/lib/prisma";
import { parsePublishedCourseSnapshot } from "@/lib/course-content";
import { sanitizeRichTextHtml } from "@/lib/rich-text";
import { createRecordMaterialLearningEvent } from "@/modules/learning/application/record-material-learning-event";
import { prismaLearningRepository } from "@/modules/learning/infrastructure/prisma-learning-repository";
import { issueCertificateIfCompleted } from "@/modules/certification/server/issue-certificate-if-completed";
import type { MaxLearnerIdentity } from "../application/list-courses";
import { createListMaxCourses } from "../application/list-courses";
import { prismaMaxLearnerRepository } from "../infrastructure/prisma-max-learner-repository";
import { isSupportedMaxQuiz } from "../application/quiz-delivery";

const listCourses = createListMaxCourses(prismaMaxLearnerRepository);

export async function hasCourseAccess(identity: MaxLearnerIdentity, courseId: string) {
  const courses = await listCourses(identity);
  if (!courses) return "SESSION_REVOKED" as const;
  return courses.some((course) => course.id === courseId) ? "ALLOWED" as const : "FORBIDDEN" as const;
}

export async function loadMaxCourseSnapshot(identity: MaxLearnerIdentity, courseId: string) {
  const access = await hasCourseAccess(identity, courseId);
  if (access !== "ALLOWED") return { kind: "error", error: access } as const;

  const course = await prisma.course.findUnique({
    where: { id: courseId },
    select: { id: true, status: true, publishedSnapshotJson: true },
  });
  if (course?.status !== "PUBLISHED") return { kind: "error", error: "FORBIDDEN" } as const;
  const snapshot = parsePublishedCourseSnapshot(course.publishedSnapshotJson);
  if (!snapshot) return { kind: "error", error: "CONTENT_UNAVAILABLE" } as const;
  return { kind: "ready", course, snapshot } as const;
}

export async function getMaxCourse(identity: MaxLearnerIdentity, courseId: string) {
  const loaded = await loadMaxCourseSnapshot(identity, courseId);
  if (loaded.kind === "error") return { error: loaded.error } as const;
  const { course, snapshot } = loaded;

  const textItems = snapshot.items.filter((item) => item.type === "TEXT");
  const supportedQuizzes = snapshot.items.filter((item) => item.type === "QUIZ" && item.quiz &&
    isSupportedMaxQuiz(item.quiz, snapshot.resultViewMode));
  const [views, bestResults, certificate] = await Promise.all([
    prisma.courseItemView.findMany({
      where: { userId: identity.userId, courseItemId: { in: textItems.map((item) => item.id) } },
      select: { courseItemId: true, progressPercent: true },
    }),
    prisma.quizUserBestResult.findMany({
      where: { userId: identity.userId, quizId: { in: supportedQuizzes.map((item) => item.quiz!.id) } },
      select: { quizId: true, status: true, attemptsUsed: true, bestCorrectAnswers: true },
    }),
    prisma.certificate.findUnique({
      where: { courseId_userId: { courseId, userId: identity.userId } },
      select: { status: true },
    }),
  ]);
  const progressByItem = new Map(views.map((view) => [view.courseItemId, view.progressPercent]));
  const resultByQuiz = new Map(bestResults.map((result) => [result.quizId, result]));

  return {
    course: {
      id: course.id,
      title: snapshot.title,
      description: snapshot.description,
      completed: certificate?.status === "ISSUED",
      materials: textItems.map((item) => ({
        id: item.id,
        title: item.title,
        content: sanitizeRichTextHtml(item.content),
        completed: (progressByItem.get(item.id) ?? 0) >= 100,
      })),
      quizzes: supportedQuizzes.map((item) => {
        const quiz = item.quiz!;
        const result = resultByQuiz.get(quiz.id);
        return {
          id: quiz.id,
          title: item.title,
          description: quiz.description,
          questionCount: quiz.questions.length,
          maxAttempts: quiz.maxAttempts,
          attemptsUsed: result?.attemptsUsed ?? 0,
          status: result?.status ?? "NOT_STARTED",
          bestCorrectAnswers: result?.bestCorrectAnswers ?? 0,
        };
      }),
      hasUnsupportedItems: snapshot.items.some((item) => item.type !== "TEXT" &&
        !supportedQuizzes.some((supported) => supported.id === item.id)),
    },
  } as const;
}

export async function completeMaxMaterial(identity: MaxLearnerIdentity, courseId: string, materialId: string) {
  const result = await getMaxCourse(identity, courseId);
  if (result.error) return { error: result.error } as const;
  if (!result.course.materials.some((material) => material.id === materialId)) {
    return { error: "MATERIAL_NOT_FOUND" } as const;
  }

  const record = createRecordMaterialLearningEvent({
    repository: prismaLearningRepository,
    accessPolicy: {
      async canRecord({ actorId, courseId: currentCourseId }) {
        return actorId === identity.userId && currentCourseId === courseId &&
          await hasCourseAccess(identity, courseId) === "ALLOWED";
      },
    },
    onCourseProgressAdvanced: async ({ userId, courseId: completedCourseId }) => {
      await issueCertificateIfCompleted({ userId, courseId: completedCourseId, issuedVia: "LEARNING" });
    },
  });
  const progress = await record({
    actor: { id: identity.userId, roles: [], permissions: [] },
    materialId,
    event: { type: "MATERIAL_COMPLETED" },
  });
  return { progress } as const;
}
