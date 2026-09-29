import assert from "node:assert/strict";
import test from "node:test";
import type {
  AssessmentRepository,
  AssessmentTransaction,
  BestResultWrite,
} from "./ports";
import { createStartAssessmentAttempt } from "./start-assessment-attempt";
import { createSaveAssessmentDraft } from "./save-assessment-draft";
import {
  createSubmitAssessmentAttempt,
} from "./submit-assessment-attempt";
import { AssessmentApplicationError } from "./errors";
import type { AssessmentAttempt, AssessmentQuestion } from "../domain/assessment";

const question: AssessmentQuestion = {
  id: "q1",
  orderIndex: 0,
  type: "SINGLE_CHOICE",
  prompt: "Ответ",
  config: JSON.stringify({ options: ["Нет", "Да"], correctIndex: 1 }),
  points: 3,
};

test("practice retains all attempts and the passed best result beyond the ordinary limit", async () => {
  const { repository, state } = createRepository();
  const start = createStartAssessmentAttempt(repository);
  const submit = createSubmitAssessmentAttempt(repository);
  const command = {
    quizId: "practice-quiz", userId: "learner", questions: [question],
    maxAttempts: 2, retryDelayMinutes: 30, practice: true,
  };
  let passedAttemptId = "";
  for (let index = 0; index < 7; index++) {
    const attempt = await start(command);
    assert.equal((await start(command)).attemptId, attempt.attemptId);
    const result = await submit({
      ...command, expectedAttemptId: attempt.attemptId,
      answers: { q1: index === 4 ? 1 : 0 }, minCorrectAnswers: 1,
      timeLimitMinutes: null, securityEventsJson: null,
    });
    if (result.outcome === "PASSED") passedAttemptId = attempt.attemptId;
    assert.equal(state.best?.status, index >= 4 ? "PASSED" : "IN_PROGRESS");
    await assert.rejects(
      submit({ ...command, expectedAttemptId: attempt.attemptId, answers: { q1: 0 },
        minCorrectAnswers: 1, timeLimitMinutes: null, securityEventsJson: null }),
      (error: unknown) => error instanceof AssessmentApplicationError && error.code === "ATTEMPT_NOT_ACTIVE",
    );
  }
  assert.equal(state.attempts.length, 7);
  assert.deepEqual(state.attempts.map((attempt) => attempt.attemptNumber), [1, 2, 3, 4, 5, 6, 7]);
  assert.equal(state.best?.attemptsUsed, 7);
  assert.equal(state.best?.bestAttemptId, passedAttemptId);
  assert.equal(state.best?.bestCorrectAnswers, 1);
  await assert.rejects(
    start({ ...command, practice: false }),
    (error: unknown) => error instanceof AssessmentApplicationError && error.code === "ALREADY_PASSED",
  );
});

function createRepository() {
  const state: { attempts: AssessmentAttempt[]; best: BestResultWrite | null } = {
    attempts: [],
    best: null,
  };
  const repository: AssessmentRepository = {
    async transact({ execute }) {
      const transaction: AssessmentTransaction = {
        attempts: state.attempts.slice(),
        async createAttempt(data) {
          const created: AssessmentAttempt = {
            id: `attempt-${state.attempts.length + 1}`,
            createdAt: data.completedAt,
            ...data,
          };
          state.attempts.push(created);
          return created;
        },
        async updateAttempt(attemptId, data) {
          const index = state.attempts.findIndex((attempt) => attempt.id === attemptId);
          const updated = { ...state.attempts[index], ...data };
          state.attempts[index] = updated;
          return updated;
        },
        async saveBestResult(data) {
          state.best = data;
        },
      };
      return execute(transaction);
    },
  };
  return { repository, state };
}

test("start is explicit and idempotent while an attempt is in progress", async () => {
  const { repository, state } = createRepository();
  const start = createStartAssessmentAttempt(repository);
  const command = {
    quizId: "quiz-1",
    userId: "user-1",
    questions: [question],
    maxAttempts: 2,
    retryDelayMinutes: null,
    now: new Date("2026-08-13T10:00:00Z"),
  };
  const first = await start(command);
  const second = await start(command);
  assert.equal(first.started, true);
  assert.equal(second.started, false);
  assert.equal(state.attempts.length, 1);
});

test("submission cannot silently create another attempt when its expected draft is gone", async () => {
  const { repository, state } = createRepository();
  const submit = createSubmitAssessmentAttempt(repository);
  await assert.rejects(
    submit({
      quizId: "quiz-1", userId: "user-1", expectedAttemptId: "missing-attempt",
      questions: [question], answers: { q1: 1 }, maxAttempts: 2,
      minCorrectAnswers: 1, retryDelayMinutes: null, timeLimitMinutes: null,
      securityEventsJson: null,
    }),
    (error: unknown) => error instanceof AssessmentApplicationError && error.code === "ATTEMPT_NOT_ACTIVE",
  );
  assert.equal(state.attempts.length, 0);
});

test("submission calculates and projects the result in one transaction", async () => {
  const { repository, state } = createRepository();
  const submit = createSubmitAssessmentAttempt(repository);
  const result = await submit({
    quizId: "quiz-1",
    userId: "user-1",
    questions: [question],
    answers: { q1: "1" },
    maxAttempts: 2,
    minCorrectAnswers: 1,
    retryDelayMinutes: null,
    timeLimitMinutes: null,
    securityEventsJson: null,
    now: new Date("2026-08-13T10:05:00Z"),
  });
  assert.equal(result.outcome, "PASSED");
  assert.equal(result.score, 3);
  assert.equal(state.best?.status, "PASSED");
  assert.equal(state.best?.bestAttemptId, result.attemptId);
});

test("draft saving keeps the attempt in progress without consuming another attempt", async () => {
  const { repository, state } = createRepository();
  const start = createStartAssessmentAttempt(repository);
  await start({
    quizId: "quiz-1",
    userId: "user-1",
    questions: [question],
    maxAttempts: 2,
    retryDelayMinutes: null,
    now: new Date("2026-08-13T10:00:00Z"),
  });
  const save = createSaveAssessmentDraft(repository);
  await save({
    quizId: "quiz-1",
    userId: "user-1",
    questions: [question],
    answers: { q1: "1" },
    maxAttempts: 2,
    retryDelayMinutes: null,
    timeLimitMinutes: 30,
    securityEventsJson: null,
    now: new Date("2026-08-13T10:01:00Z"),
  });
  assert.equal(state.attempts.length, 1);
  assert.equal(state.attempts[0].outcome, "IN_PROGRESS");
  assert.deepEqual(JSON.parse(state.attempts[0].answers), { q1: "1" });
});

test("server rejects submission after the attempt time limit", async () => {
  const { repository } = createRepository();
  const start = createStartAssessmentAttempt(repository);
  await start({
    quizId: "quiz-1",
    userId: "user-1",
    questions: [question],
    maxAttempts: 2,
    retryDelayMinutes: null,
    now: new Date("2026-08-13T10:00:00Z"),
  });
  const submit = createSubmitAssessmentAttempt(repository);
  await assert.rejects(
    submit({
      quizId: "quiz-1",
      userId: "user-1",
      questions: [question],
      answers: { q1: "1" },
      maxAttempts: 2,
      minCorrectAnswers: 1,
      retryDelayMinutes: null,
      timeLimitMinutes: 5,
      securityEventsJson: null,
      now: new Date("2026-08-13T10:05:16Z"),
    }),
    (error: unknown) => error instanceof AssessmentApplicationError && error.code === "TIME_LIMIT_EXPIRED",
  );
});
