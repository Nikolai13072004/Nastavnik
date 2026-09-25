import type { AssessmentRepository } from "@/modules/assessment/application/ports";
import { AssessmentApplicationError } from "@/modules/assessment/application/errors";
import {
  AssessmentDomainError,
  assertAttemptAvailable,
  assertTimeLimit,
  getBestAssessmentAttempt,
  getRequiredCorrectAnswers,
  scoreAssessment,
  serializeQuestionSnapshot,
  type AssessmentQuestion,
} from "@/modules/assessment/domain/assessment";

export type SubmitAssessmentAttemptCommand = {
  quizId: string;
  userId: string;
  expectedAttemptId?: string;
  questions: AssessmentQuestion[];
  answers: Record<string, unknown>;
  maxAttempts: number;
  minCorrectAnswers: number;
  retryDelayMinutes: number | null;
  timeLimitMinutes: number | null;
  securityEventsJson: string | null;
  now?: Date;
};

export function createSubmitAssessmentAttempt(repository: AssessmentRepository) {
  return async function submitAssessmentAttempt(command: SubmitAssessmentAttemptCommand) {
    try {
      return await repository.transact({
        quizId: command.quizId,
        userId: command.userId,
        execute: async (transaction) => {
          const now = command.now ?? new Date();
          const completedAttempts = transaction.attempts.filter((attempt) => attempt.outcome !== "IN_PROGRESS");
          const draftAttempt = transaction.attempts
            .filter((attempt) => attempt.outcome === "IN_PROGRESS")
            .sort((left, right) => right.attemptNumber - left.attemptNumber)[0] ?? null;

          if (command.expectedAttemptId && draftAttempt?.id !== command.expectedAttemptId) {
            throw new AssessmentDomainError("ATTEMPT_NOT_ACTIVE", "Попытка уже завершена или недоступна.");
          }

          assertAttemptAvailable({
            completedAttempts,
            maxAttempts: command.maxAttempts,
            retryDelayMinutes: draftAttempt ? null : command.retryDelayMinutes,
            now,
          });
          assertTimeLimit({
            attempt: draftAttempt,
            timeLimitMinutes: command.timeLimitMinutes,
            now,
            submissionGraceMs: 15_000,
          });

          const scored = scoreAssessment(command.questions, command.answers);
          const requiredCorrectAnswers = getRequiredCorrectAnswers(
            command.minCorrectAnswers,
            scored.totalQuestions,
          );
          const outcome = scored.hasPendingReview
            ? "PENDING_REVIEW"
            : scored.correctAnswers >= requiredCorrectAnswers
              ? "PASSED"
              : "FAILED";
          const write = {
            answers: JSON.stringify(command.answers),
            questionSnapshot: serializeQuestionSnapshot(command.questions),
            score: scored.score,
            maxScore: scored.maxScore,
            correctAnswers: scored.correctAnswers,
            totalQuestions: scored.totalQuestions,
            outcome,
            securityEventsJson: command.securityEventsJson,
            completedAt: now,
          };
          const attempt = draftAttempt
            ? await transaction.updateAttempt(draftAttempt.id, write)
            : await transaction.createAttempt({
                ...write,
                attemptNumber: completedAttempts.length + 1,
              });

          const allCompletedAttempts = [...completedAttempts, attempt];
          const bestAttempt = getBestAssessmentAttempt(allCompletedAttempts);
          const hasPendingReview = allCompletedAttempts.some((item) => item.outcome === "PENDING_REVIEW");
          const status = bestAttempt?.outcome === "PASSED"
            ? "PASSED"
            : hasPendingReview
              ? "PENDING_REVIEW"
              : allCompletedAttempts.length >= command.maxAttempts
                ? "FAILED"
                : "IN_PROGRESS";
          await transaction.saveBestResult({
            bestAttemptId: bestAttempt?.id ?? null,
            bestScore: bestAttempt?.score ?? 0,
            bestMaxScore: bestAttempt?.maxScore ?? 0,
            bestCorrectAnswers: bestAttempt?.correctAnswers ?? 0,
            attemptsUsed: allCompletedAttempts.length,
            status,
          });

          return {
            attemptId: attempt.id,
            outcome,
            score: scored.score,
            maxScore: scored.maxScore,
            correctAnswers: scored.correctAnswers,
            totalQuestions: scored.totalQuestions,
          };
        },
      });
    } catch (error) {
      if (!(error instanceof AssessmentDomainError)) throw error;
      throw new AssessmentApplicationError(error.code, error.message);
    }
  };
}
