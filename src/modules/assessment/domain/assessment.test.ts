import assert from "node:assert/strict";
import test from "node:test";
import {
  AssessmentDomainError,
  assertAttemptAvailable,
  getRequiredCorrectAnswers,
  scoreAssessment,
  type AssessmentAttempt,
  type AssessmentQuestion,
} from "./assessment";

const questions: AssessmentQuestion[] = [
  {
    id: "single",
    orderIndex: 0,
    type: "SINGLE_CHOICE",
    prompt: "Выберите ответ",
    config: JSON.stringify({ options: ["A", "B"], correctIndex: 1 }),
    points: 2,
  },
  {
    id: "manual",
    orderIndex: 1,
    type: "OPEN",
    prompt: "Объясните",
    config: JSON.stringify({ reviewMode: "MANUAL" }),
    points: 5,
  },
];

function attempt(overrides: Partial<AssessmentAttempt> = {}): AssessmentAttempt {
  return {
    id: "attempt-1",
    attemptNumber: 1,
    outcome: "FAILED",
    score: 0,
    maxScore: 2,
    correctAnswers: 0,
    totalQuestions: 1,
    answers: "{}",
    questionSnapshot: "[]",
    createdAt: new Date("2026-08-13T10:00:00Z"),
    completedAt: new Date("2026-08-13T10:05:00Z"),
    ...overrides,
  };
}

test("configured pass threshold is authoritative for any question count", () => {
  assert.equal(getRequiredCorrectAnswers(1, 10), 1);
  assert.equal(getRequiredCorrectAnswers(12, 10), 10);
  assert.equal(getRequiredCorrectAnswers(1, 0), 0);
});

test("automatic scoring keeps manual questions pending", () => {
  const result = scoreAssessment(questions, { single: "1", manual: "Текст" });
  assert.equal(result.score, 2);
  assert.equal(result.maxScore, 7);
  assert.equal(result.correctAnswers, 1);
  assert.equal(result.hasPendingReview, true);
});

test("retry delay is enforced from server time", () => {
  assert.throws(
    () => assertAttemptAvailable({
      completedAttempts: [attempt()],
      maxAttempts: 3,
      retryDelayMinutes: 30,
      now: new Date("2026-08-13T10:20:00Z"),
    }),
    (error: unknown) => error instanceof AssessmentDomainError && error.code === "RETRY_DELAY",
  );
});

test("practice permits passed, exhausted and immediate retries but still blocks manual review", () => {
  const args = {
    completedAttempts: [attempt({ outcome: "PASSED" }), attempt()],
    maxAttempts: 1,
    retryDelayMinutes: 30,
    now: new Date("2026-08-13T10:06:00Z"),
    practice: true,
  };
  assert.doesNotThrow(() => assertAttemptAvailable(args));
  assert.throws(
    () => assertAttemptAvailable({ ...args, practice: false }),
    (error: unknown) => error instanceof AssessmentDomainError && error.code === "ALREADY_PASSED",
  );
  assert.throws(
    () => assertAttemptAvailable({ ...args, completedAttempts: [attempt({ outcome: "PENDING_REVIEW" })] }),
    (error: unknown) => error instanceof AssessmentDomainError && error.code === "PENDING_REVIEW",
  );
});
