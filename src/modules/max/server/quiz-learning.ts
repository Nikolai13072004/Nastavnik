import "server-only";

import prisma from "@/lib/prisma";
import { prepareAssessmentQuestions } from "@/modules/assessment/domain/delivery";
import type { AssessmentQuestion } from "@/modules/assessment/domain/assessment";
import { startAssessmentAttempt } from "@/modules/assessment/server/start-assessment-attempt";
import { submitAssessmentAttempt } from "@/modules/assessment/server/submit-assessment-attempt";
import { issueCertificateIfCompleted } from "@/modules/certification/server/issue-certificate-if-completed";
import type { MaxLearnerIdentity } from "../application/list-courses";
import { isSupportedMaxQuiz, toMaxQuizQuestions, validateMaxQuizAnswers } from "../application/quiz-delivery";
import { loadMaxCourseSnapshot } from "./course-learning";

async function loadQuiz(identity: MaxLearnerIdentity, courseId: string, quizId: string) {
  const loaded = await loadMaxCourseSnapshot(identity, courseId);
  if (loaded.kind === "error") return { kind: "error", error: loaded.error } as const;

  const { snapshot } = loaded;
  const itemIndex = snapshot.items.findIndex((item) => item.type === "QUIZ" && item.quiz?.id === quizId);
  const item = snapshot.items[itemIndex];
  if (!item?.quiz) return { kind: "error", error: "QUIZ_NOT_FOUND" } as const;
  if (!isSupportedMaxQuiz(item.quiz, snapshot.resultViewMode)) return { kind: "error", error: "QUIZ_UNSUPPORTED" } as const;

  const liveQuiz = await prisma.quiz.findUnique({
    where: { id: quizId },
    select: { courseItemId: true, courseItem: { select: { courseId: true } } },
  });
  if (liveQuiz?.courseItemId !== item.id || liveQuiz.courseItem.courseId !== courseId) {
    return { kind: "error", error: "QUIZ_NOT_FOUND" } as const;
  }

  const priorRequired = snapshot.items.slice(0, itemIndex).filter((prior) => prior.isRequired);
  if (priorRequired.some((prior) => prior.type !== "TEXT")) return { kind: "error", error: "QUIZ_UNSUPPORTED" } as const;

  return { kind: "ready", quiz: item.quiz, priorRequired } as const;
}

function parseAttemptQuestions(raw: string): AssessmentQuestion[] | null {
  try {
    const questions: unknown = JSON.parse(raw);
    if (!Array.isArray(questions) || questions.length === 0 ||
        !questions.every((question) => question && typeof question.id === "string" &&
          typeof question.prompt === "string" && typeof question.config === "string" &&
          typeof question.orderIndex === "number" && typeof question.points === "number")) return null;
    const typed = questions as AssessmentQuestion[];
    return toMaxQuizQuestions(typed) ? typed : null;
  } catch {
    return null;
  }
}

export async function startMaxQuiz(identity: MaxLearnerIdentity, courseId: string, quizId: string) {
  const loaded = await loadQuiz(identity, courseId, quizId);
  if (loaded.kind === "error") return { error: loaded.error } as const;

  const requiredIds = loaded.priorRequired.map((item) => item.id);
  const views = await prisma.courseItemView.findMany({
    where: { userId: identity.userId, courseItemId: { in: requiredIds } },
    select: { courseItemId: true, progressPercent: true },
  });
  const completedIds = new Set(views.filter((view) => view.progressPercent >= 100).map((view) => view.courseItemId));
  if (requiredIds.some((id) => !completedIds.has(id))) return { error: "MATERIAL_REQUIRED" } as const;

  const quiz = loaded.quiz;
  const prepared = prepareAssessmentQuestions(quiz.questions, {
    shuffleAnswers: quiz.shuffleAnswers,
    shuffleQuestions: quiz.shuffleQuestions,
    questionPoolSize: quiz.questionPoolSize,
  });
  const started = await startAssessmentAttempt({
    quizId, userId: identity.userId, questions: prepared,
    maxAttempts: quiz.maxAttempts, retryDelayMinutes: quiz.retryDelayMinutes,
  });
  const attempt = await prisma.quizAttempt.findUnique({
    where: { id: started.attemptId },
    select: { userId: true, quizId: true, questionSnapshot: true },
  });
  if (attempt?.userId !== identity.userId || attempt.quizId !== quizId) {
    return { error: "ATTEMPT_UNAVAILABLE" } as const;
  }
  const questions = parseAttemptQuestions(attempt.questionSnapshot);
  if (!questions) return { error: "ATTEMPT_UNAVAILABLE" } as const;
  return { attemptId: started.attemptId, questions: toMaxQuizQuestions(questions) } as const;
}

export async function submitMaxQuiz(identity: MaxLearnerIdentity, courseId: string, quizId: string,
  attemptId: string, rawAnswers: unknown) {
  const loaded = await loadQuiz(identity, courseId, quizId);
  if (loaded.kind === "error") return { error: loaded.error } as const;

  const attempt = await prisma.quizAttempt.findUnique({
    where: { id: attemptId },
    select: {
      quizId: true, userId: true, questionSnapshot: true, answers: true, outcome: true,
      score: true, maxScore: true, correctAnswers: true, totalQuestions: true,
    },
  });
  if (attempt?.userId !== identity.userId || attempt.quizId !== quizId) {
    return { error: "ATTEMPT_UNAVAILABLE" } as const;
  }
  const questions = parseAttemptQuestions(attempt.questionSnapshot);
  const answers = questions && validateMaxQuizAnswers(questions, rawAnswers);
  if (!questions || !answers) return { error: "INVALID_ANSWERS" } as const;

  if (attempt.outcome !== "IN_PROGRESS") {
    if (attempt.answers !== JSON.stringify(answers)) return { error: "ALREADY_SUBMITTED" } as const;
    return { result: {
      outcome: attempt.outcome, score: attempt.score, maxScore: attempt.maxScore,
      correctAnswers: attempt.correctAnswers, totalQuestions: attempt.totalQuestions,
    } } as const;
  }

  const quiz = loaded.quiz;
  const result = await submitAssessmentAttempt({
    quizId, userId: identity.userId, expectedAttemptId: attemptId,
    questions, answers, maxAttempts: quiz.maxAttempts,
    minCorrectAnswers: quiz.minCorrectAnswers, retryDelayMinutes: quiz.retryDelayMinutes,
    timeLimitMinutes: quiz.timeLimitMinutes, securityEventsJson: null,
  });
  if (result.outcome === "PASSED") {
    try {
      await issueCertificateIfCompleted({ userId: identity.userId, courseId, issuedVia: "ASSESSMENT_SUBMIT" });
    } catch (error) {
      console.error("Не удалось обработать завершение курса после теста MAX:", error);
    }
  }
  return { result: {
    outcome: result.outcome, score: result.score, maxScore: result.maxScore,
    correctAnswers: result.correctAnswers, totalQuestions: result.totalQuestions,
  } } as const;
}
