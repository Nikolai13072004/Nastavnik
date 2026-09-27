import assert from "node:assert/strict";
import test from "node:test";
import { isDemoPracticeQuiz, isSupportedMaxQuiz, toMaxQuizQuestions, validateMaxQuizAnswers } from "./quiz-delivery";

test("unlimited practice is opt-in and confined to the exact synthetic pilot quiz", () => {
  const organization = "max-pilot-demo-org";
  const course = "max-pilot-onboarding-course";
  const quiz = "max-pilot-onboarding-quiz";
  assert.equal(isDemoPracticeQuiz(true, organization, course, quiz), true);
  assert.equal(isDemoPracticeQuiz(false, organization, course, quiz), false);
  assert.equal(isDemoPracticeQuiz(true, "real-company", course, quiz), false);
  assert.equal(isDemoPracticeQuiz(true, organization, "work-course", quiz), false);
  assert.equal(isDemoPracticeQuiz(true, organization, course, "work-quiz"), false);
});

const question = {
  id: "q1", orderIndex: 0, type: "SINGLE_CHOICE", prompt: "Кто утверждает?",
  config: JSON.stringify({ options: ["AI", "Сотрудник"], correctIndex: 1 }), points: 1,
};

test("MAX question projection never exposes the correct answer", () => {
  const projected = toMaxQuizQuestions([question]);
  assert.deepEqual(projected, [{ id: "q1", prompt: "Кто утверждает?", options: ["AI", "Сотрудник"] }]);
  assert.equal(JSON.stringify(projected).includes("correctIndex"), false);
});

test("MAX answers must cover exactly the offered questions with valid indices", () => {
  assert.deepEqual(validateMaxQuizAnswers([question], { q1: 1 }), { q1: 1 });
  assert.equal(validateMaxQuizAnswers([question], { q1: 2 }), null);
  assert.equal(validateMaxQuizAnswers([question], { q1: 1, extra: 0 }), null);
  assert.equal(validateMaxQuizAnswers([question], { q1: "1" }), null);
});

test("unsupported quiz modes and question types remain unavailable in MAX", () => {
  const quiz = {
    id: "quiz", description: null, maxAttempts: 2, minCorrectAnswers: 1,
    timeLimitMinutes: null, shuffleQuestions: false, shuffleAnswers: false,
    lockMaterialsOnStart: false, questionPoolSize: null, retryDelayMinutes: null,
    trackSecurityEvents: false, questions: [question],
  };
  assert.equal(isSupportedMaxQuiz(quiz, "SCORE_ONLY"), true);
  assert.equal(isSupportedMaxQuiz({ ...quiz, timeLimitMinutes: 5 }, "SCORE_ONLY"), false);
  assert.equal(isSupportedMaxQuiz({ ...quiz, questions: [{ ...question, type: "OPEN" }] }, "SCORE_ONLY"), false);
});
