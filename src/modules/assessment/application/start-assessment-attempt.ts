import type { AssessmentRepository } from "@/modules/assessment/application/ports";
import {
  AssessmentDomainError,
  assertAttemptAvailable,
  serializeQuestionSnapshot,
  type AssessmentQuestion,
} from "@/modules/assessment/domain/assessment";
import { AssessmentApplicationError } from "@/modules/assessment/application/errors";

export function createStartAssessmentAttempt(repository: AssessmentRepository) {
  return async function startAssessmentAttempt(command: {
    quizId: string;
    userId: string;
    questions: AssessmentQuestion[];
    maxAttempts: number;
    retryDelayMinutes: number | null;
    practice?: boolean;
    now?: Date;
  }) {
    try {
      return await repository.transact({
        quizId: command.quizId,
        userId: command.userId,
        execute: async (transaction) => {
          const existing = transaction.attempts
            .filter((attempt) => attempt.outcome === "IN_PROGRESS")
            .sort((left, right) => right.attemptNumber - left.attemptNumber)[0];
          if (existing) return { attemptId: existing.id, started: false };

          const completedAttempts = transaction.attempts.filter((attempt) => attempt.outcome !== "IN_PROGRESS");
          const now = command.now ?? new Date();
          assertAttemptAvailable({
            completedAttempts,
            maxAttempts: command.maxAttempts,
            retryDelayMinutes: command.retryDelayMinutes,
            practice: command.practice,
            now,
          });
          const attempt = await transaction.createAttempt({
            attemptNumber: completedAttempts.length + 1,
            answers: "{}",
            questionSnapshot: serializeQuestionSnapshot(command.questions),
            score: 0,
            maxScore: command.questions.reduce((sum, question) => sum + Math.max(0, question.points), 0),
            correctAnswers: 0,
            totalQuestions: command.questions.length,
            outcome: "IN_PROGRESS",
            completedAt: now,
          });
          return { attemptId: attempt.id, started: true };
        },
      });
    } catch (error) {
      if (!(error instanceof AssessmentDomainError)) throw error;
      throw new AssessmentApplicationError(error.code, error.message);
    }
  };
}
