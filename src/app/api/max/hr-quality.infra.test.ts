import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { after, before, test } from "node:test";
import prisma from "@/lib/prisma";
import { saveMaxStudyPlan } from "@/modules/enrollment/server/max-study-plan";
import { createMaxSessionCodec } from "@/modules/max/infrastructure/learner-session";
import { createPrismaChatSessions } from "@/modules/max/infrastructure/prisma-chat-session-repository";
import {
  activeMaxStudyReminder,
  enqueueMaxStudyReminders,
} from "@/modules/max/infrastructure/prisma-study-reminders";
import { prismaBotDeliveryRepository } from "@/modules/max/infrastructure/prisma-bot-delivery-repository";
import {
  getMaxLearnerHistory,
  getMaxQuestionErrors,
} from "@/modules/max/server/manager-learning-history";
import {
  getMaxAiQuality,
  recordMaxAiEvent,
  reviewMaxAiFeedback,
  submitMaxAiFeedback,
} from "@/modules/max/server/ai-quality";
import { GET as historyGET } from "@/app/api/max/manager-history/route";
import {
  GET as qualityGET,
  POST as qualityPOST,
} from "@/app/api/max/ai-quality/route";
import { POST as feedbackPOST } from "@/app/api/max/ai-feedback/route";
import { POST as planPOST } from "@/app/api/max/study-plan/route";

const prefix = `hr-quality-${randomUUID()}`;
const orgId = `${prefix}-org`;
const foreignOrgId = `${prefix}-foreign-org`;
const hrId = `${prefix}-hr`;
const learnerId = `${prefix}-learner`;
const foreignId = `${prefix}-foreign`;
const courseId = `${prefix}-course`;
const foreignCourseId = `${prefix}-foreign-course`;
const itemId = `${prefix}-item`;
const quizId = `${prefix}-quiz`;
const botUsername = `${prefix}_bot`;
const previousToken = process.env.MAX_BOT_TOKEN;
const botToken = "test-only-hr-quality-secret";
let hr = {
  maxUserId: `${prefix}-hr-max`,
  userId: hrId,
  organizationId: orgId,
  linkedAt: "",
};
let learner = {
  maxUserId: `${prefix}-learner-max`,
  userId: learnerId,
  organizationId: orgId,
  linkedAt: "",
};
const question = {
  id: "q",
  type: "SINGLE_CHOICE",
  prompt: "Сохранённый вопрос",
  config: '{"options":["A","B"],"correctIndex":0}',
};
const answer = {
  answer: "Подтверждённый ответ",
  refused: false,
  sources: [
    {
      documentId: "source",
      documentHash: "hash",
      title: "Документ",
      section: "Пункт 1",
      snippet: "Подтверждение",
      pageStart: null,
      pageEnd: null,
      courseDocumentId: "exact-document",
    },
  ],
};

before(async () => {
  process.env.MAX_BOT_TOKEN = botToken;
  await prisma.organization.createMany({
    data: [
      { id: orgId, name: orgId },
      { id: foreignOrgId, name: foreignOrgId },
    ],
  });
  await prisma.user.createMany({
    data: [
      {
        id: hrId,
        login: hrId,
        name: "HR",
        passwordHash: "test",
        role: "HR",
        organizationId: orgId,
      },
      {
        id: learnerId,
        login: learnerId,
        name: "Сотрудник",
        passwordHash: "test",
        organizationId: orgId,
      },
      {
        id: foreignId,
        login: foreignId,
        name: "Чужой",
        passwordHash: "test",
        organizationId: foreignOrgId,
      },
    ],
  });
  const hrLink = await prisma.maxAccountLink.create({
    data: { maxUserId: hr.maxUserId, userId: hrId, organizationId: orgId },
  });
  const learnerLink = await prisma.maxAccountLink.create({
    data: {
      maxUserId: learner.maxUserId,
      userId: learnerId,
      organizationId: orgId,
    },
  });
  hr = { ...hr, linkedAt: hrLink.createdAt.toISOString() };
  learner = { ...learner, linkedAt: learnerLink.createdAt.toISOString() };
  await prisma.course.createMany({
    data: [
      {
        id: courseId,
        title: "Свой курс",
        ownerId: hrId,
        organizationId: orgId,
        status: "PUBLISHED",
      },
      {
        id: foreignCourseId,
        title: "Чужой курс",
        ownerId: foreignId,
        organizationId: foreignOrgId,
        status: "PUBLISHED",
      },
    ],
  });
  await prisma.courseUserAssignment.create({
    data: { courseId, userId: learnerId },
  });
  await prisma.courseItem.create({
    data: {
      id: itemId,
      courseId,
      title: "Тест",
      type: "QUIZ",
      quiz: { create: { id: quizId } },
    },
  });
  await prisma.quizAttempt.create({
    data: {
      quizId,
      userId: learnerId,
      attemptNumber: 1,
      answers: '{"q":1}',
      questionSnapshot: JSON.stringify([question]),
      score: 0,
      maxScore: 1,
      correctAnswers: 0,
      totalQuestions: 1,
      outcome: "FAILED",
    },
  });
});

after(async () => {
  if (previousToken === undefined) delete process.env.MAX_BOT_TOKEN;
  else process.env.MAX_BOT_TOKEN = previousToken;
  await prisma.maxBotDelivery.deleteMany({ where: { botUsername } });
  await prisma.auditLogEvent.deleteMany({ where: { actorId: hrId } });
  await prisma.course.deleteMany({
    where: { id: { in: [courseId, foreignCourseId] } },
  });
  await prisma.user.deleteMany({
    where: { id: { in: [hrId, learnerId, foreignId] } },
  });
  await prisma.organization.deleteMany({
    where: { id: { in: [orgId, foreignOrgId] } },
  });
  await prisma.$disconnect();
});

function request(path: string, identity = hr, body?: unknown) {
  return new Request(`http://localhost/api/max/${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      Authorization: `Bearer ${createMaxSessionCodec(botToken).issue(identity).token}`,
      "Content-Type": "application/json",
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
}

test("HR history uses the saved attempt snapshot, not edited questions; student and other tenants are denied", async () => {
  await prisma.question.create({
    data: {
      quizId,
      id: `${prefix}-live-question`,
      type: "SINGLE_CHOICE",
      prompt: "Новый вопрос",
      config: "{}",
    },
  });
  const history = await getMaxLearnerHistory(hr, learnerId);
  assert.ok(!("error" in history));
  assert.equal(history.totalAttempts, 1);
  assert.equal(history.attempts[0].questions[0].prompt, "Сохранённый вопрос");
  assert.equal(history.attempts[0].questions[0].selected, "B");
  assert.equal(history.attempts[0].questions[0].correct, "A");
  assert.deepEqual(await getMaxLearnerHistory(learner, hrId), {
    error: "FORBIDDEN",
  });
  assert.deepEqual(await getMaxLearnerHistory(hr, foreignId), {
    error: "NOT_FOUND",
  });
  assert.deepEqual(await getMaxQuestionErrors(hr, foreignCourseId), {
    error: "NOT_FOUND",
  });
  const errors = await getMaxQuestionErrors(hr, courseId);
  assert.ok(!("error" in errors));
  assert.deepEqual(errors.questions, [
    { prompt: question.prompt, correct: "A", answered: 1, incorrect: 1 },
  ]);
});

test("HR endpoints use bearer MAX sessions, protect CSV and reject malformed requests", async () => {
  for (const handler of [
    historyGET,
    qualityGET,
    qualityPOST,
    feedbackPOST,
    planPOST,
  ]) {
    const response = await handler(
      new Request("http://localhost/api/max/manager-history"),
    );
    assert.equal(response.status, 401);
    assert.equal(response.headers.get("Cache-Control"), "no-store");
  }
  assert.equal(
    (
      await historyGET(
        request(`manager-history?learnerId=${learnerId}`, learner),
      )
    ).status,
    403,
  );
  const csv = await historyGET(
    request(`manager-history?courseId=${courseId}&export=csv`),
  );
  assert.equal(csv.status, 200);
  assert.match(csv.headers.get("Content-Type")!, /text\/csv/);
  assert.match(await csv.text(), /Сотрудник/);
  assert.equal(
    (await feedbackPOST(request("ai-feedback", learner, { consent: false })))
      .status,
    400,
  );
  assert.equal(
    (
      await planPOST(
        request("study-plan", hr, {
          courseId,
          learnerId,
          dueAt: "bad",
          remindersEnabled: true,
        }),
      )
    ).status,
    400,
  );
});

test("training deadlines preserve access and attempt history; no course assignment is inferred", async () => {
  const dueAt = new Date(Date.now() + 86400000);
  assert.deepEqual(
    await saveMaxStudyPlan(hr, {
      courseId,
      learnerId,
      dueAt,
      remindersEnabled: true,
    }),
    { status: "SAVED" },
  );
  const assignment = await prisma.courseUserAssignment.findUniqueOrThrow({
    where: { courseId_userId: { courseId, userId: learnerId } },
  });
  assert.equal(assignment.expiresAt, null);
  assert.equal(
    await prisma.quizAttempt.count({ where: { userId: learnerId } }),
    1,
  );
  assert.deepEqual(
    await saveMaxStudyPlan(learner, {
      courseId,
      learnerId,
      dueAt,
      remindersEnabled: true,
    }),
    { error: "FORBIDDEN" },
  );
  assert.deepEqual(
    await saveMaxStudyPlan(hr, {
      courseId,
      learnerId: hrId,
      dueAt,
      remindersEnabled: true,
    }),
    { error: "NOT_ASSIGNED" },
  );
  assert.deepEqual(
    await saveMaxStudyPlan(hr, {
      courseId,
      learnerId: foreignId,
      dueAt,
      remindersEnabled: true,
    }),
    { error: "NOT_FOUND" },
  );
});

test("AI metadata stores no correspondence; complaints require an authentic answer and current course access", async () => {
  const eventId = await recordMaxAiEvent(
    learner,
    courseId,
    1250,
    "Как действовать?",
    answer,
  );
  const event = await prisma.maxAiEvent.findUniqueOrThrow({
    where: { id: eventId },
  });
  assert.equal(event.outcome, "ANSWERED");
  assert.ok(!JSON.stringify(event).includes(answer.answer));
  const input = {
    courseId,
    eventId,
    question: "Как действовать?",
    result: answer,
    reason: "WRONG_ANSWER",
    comment: "Проверьте пункт",
  };
  assert.deepEqual(
    await submitMaxAiFeedback(learner, {
      ...input,
      result: { ...answer, answer: "Подделка" },
    }),
    { error: "NOT_FOUND" },
  );
  assert.deepEqual(await submitMaxAiFeedback(hr, input), {
    error: "FORBIDDEN",
  });
  assert.deepEqual(await submitMaxAiFeedback(learner, input), {
    status: "SAVED",
  });
  assert.deepEqual(await submitMaxAiFeedback(learner, input), {
    status: "ALREADY_REPORTED",
  });
  await recordMaxAiEvent(learner, courseId, 750, "Нет подтверждения", {
    answer: "Не найдено",
    refused: true,
    sources: [],
  });
  await recordMaxAiEvent(learner, courseId, 3000, "Сбой", null);
  const quality = await getMaxAiQuality(hr, courseId);
  assert.ok(!("error" in quality));
  assert.equal(quality.total, 3);
  assert.equal(quality.answered, 1);
  assert.equal(quality.refused, 1);
  assert.equal(quality.errors, 1);
  assert.equal(quality.feedbackCount, 1);
  assert.equal(quality.p95Ms, 3000);
  assert.equal("accuracy" in quality, false);
  assert.deepEqual(await getMaxAiQuality(learner, courseId), {
    error: "FORBIDDEN",
  });
  assert.deepEqual(await getMaxAiQuality(hr, foreignCourseId), {
    error: "NOT_FOUND",
  });
  assert.deepEqual(
    await reviewMaxAiFeedback(learner, courseId, eventId, "INCORRECT", ""),
    { error: "FORBIDDEN" },
  );
  assert.deepEqual(
    await reviewMaxAiFeedback(hr, foreignCourseId, eventId, "INCORRECT", ""),
    { error: "NOT_FOUND_OR_REVIEWED" },
  );
  const review = await qualityPOST(
    request("ai-quality", hr, {
      courseId,
      eventId,
      verdict: "INCORRECT",
      comment: "П".repeat(1000),
    }),
  );
  assert.equal(
    review.status,
    200,
    "1000 Cyrillic characters fit the request body limit",
  );
  assert.deepEqual(
    await reviewMaxAiFeedback(hr, courseId, eventId, "CORRECT", ""),
    { error: "NOT_FOUND_OR_REVIEWED" },
  );
  const reviewed = await getMaxAiQuality(hr, courseId);
  assert.ok(!("error" in reviewed));
  assert.deepEqual(reviewed.verdicts, [{ verdict: "INCORRECT", count: 1 }]);
  await prisma.courseUserAssignment.update({
    where: { courseId_userId: { courseId, userId: learnerId } },
    data: { expiresAt: new Date(0) },
  });
  assert.deepEqual(await submitMaxAiFeedback(learner, input), {
    error: "FORBIDDEN",
  });
  await prisma.courseUserAssignment.update({
    where: { courseId_userId: { courseId, userId: learnerId } },
    data: { expiresAt: null },
  });
});

test("ephemeral chat feedback is encrypted and bound to the employee link", async () => {
  const sessions = createPrismaChatSessions(botUsername);
  const state = {
    version: "0000000000000001",
    courseId,
    aiFeedback: { question: "Приватный вопрос", result: answer },
  };
  await sessions.save(learner, state);
  const stored = await prisma.maxChatSession.findUniqueOrThrow({
    where: {
      botUsername_maxUserId: { botUsername, maxUserId: learner.maxUserId },
    },
  });
  assert.ok(!stored.stateJson.includes("Приватный вопрос"));
  assert.ok(!stored.stateJson.includes(answer.answer));
  assert.deepEqual(await sessions.load(learner), state);
  assert.equal(
    await sessions.load({ ...learner, linkedAt: new Date(0).toISOString() }),
    null,
  );
  await sessions.clear(learner.maxUserId);
});

test("concurrent reminder scans enqueue once per rolling 24 hours, including midnight", async () => {
  const now = new Date();
  const concurrent = await Promise.all([
    enqueueMaxStudyReminders(botUsername, now),
    enqueueMaxStudyReminders(botUsername, now),
  ]);
  assert.deepEqual(concurrent.sort(), [0, 1]);
  await prisma.maxBotDelivery.updateMany({
    where: { botUsername, kind: "STUDY_REMINDER" },
    data: { status: "SENT", finishedAt: now },
  });
  assert.equal(
    await enqueueMaxStudyReminders(
      botUsername,
      new Date(now.getTime() + 23 * 3600000),
    ),
    0,
  );
  assert.equal(
    await enqueueMaxStudyReminders(
      botUsername,
      new Date(now.getTime() + 25 * 3600000),
    ),
    1,
  );
  assert.equal(
    await prisma.maxBotDelivery.count({ where: { botUsername } }),
    2,
  );
  await prisma.maxBotDelivery.deleteMany({ where: { botUsername } });
});

test("delayed and uncertain reminders do not trigger another daily delivery", async () => {
  const now = new Date();
  assert.equal(await enqueueMaxStudyReminders(botUsername, now), 1);
  const tomorrow = new Date(now.getTime() + 25 * 3600000);
  assert.equal(await enqueueMaxStudyReminders(botUsername, tomorrow), 0);
  await prisma.maxBotDelivery.updateMany({
    where: { botUsername, kind: "STUDY_REMINDER" },
    data: {
      status: "SENT",
      finishedAt: new Date(now.getTime() + 23 * 3600000),
    },
  });
  assert.equal(await enqueueMaxStudyReminders(botUsername, tomorrow), 0);
  await prisma.maxBotDelivery.updateMany({
    where: { botUsername, kind: "STUDY_REMINDER" },
    data: { status: "UNCERTAIN", finishedAt: now },
  });
  assert.equal(await enqueueMaxStudyReminders(botUsername, tomorrow), 0);
  await prisma.maxBotDelivery.deleteMany({ where: { botUsername } });
});

test("reminders stop on opt-out, access expiry, certificate or stale assignment, but resume for new document training", async () => {
  const plan = await prisma.maxStudyPlan.findUniqueOrThrow({
    where: { courseId_userId: { courseId, userId: learnerId } },
  });
  assert.ok(
    await activeMaxStudyReminder(
      prisma,
      plan.id,
      learner.maxUserId,
      new Date(),
    ),
  );
  await enqueueMaxStudyReminders(botUsername);
  await prisma.maxStudyPlan.update({
    where: { id: plan.id },
    data: { remindersEnabled: false },
  });
  assert.equal(await prismaBotDeliveryRepository.claim(botUsername), null);
  assert.equal(
    (await prisma.maxBotDelivery.findFirstOrThrow({ where: { botUsername } }))
      .status,
    "SKIPPED",
  );
  await prisma.maxStudyPlan.update({
    where: { id: plan.id },
    data: { remindersEnabled: true },
  });
  await prisma.courseUserAssignment.update({
    where: { courseId_userId: { courseId, userId: learnerId } },
    data: { expiresAt: new Date(0) },
  });
  assert.equal(
    await activeMaxStudyReminder(
      prisma,
      plan.id,
      learner.maxUserId,
      new Date(),
    ),
    null,
  );
  await prisma.courseUserAssignment.update({
    where: { courseId_userId: { courseId, userId: learnerId } },
    data: { expiresAt: null },
  });
  await prisma.certificate.create({
    data: {
      courseId,
      userId: learnerId,
      serial: prefix,
      issuedVia: "MANUAL",
      completedAt: new Date(),
      snapshotJson: "{}",
    },
  });
  assert.equal(
    await activeMaxStudyReminder(
      prisma,
      plan.id,
      learner.maxUserId,
      new Date(),
    ),
    null,
  );
  const document = await prisma.maxCourseDocument.create({
    data: {
      organizationId: orgId,
      courseId,
      title: "Новая редакция",
      sourceName: "new.md",
      contentText: "Новое правило",
      contentHash: "hash",
      uploadedById: hrId,
      approvedById: hrId,
      approvedAt: new Date(),
      versionNumber: 2,
    },
  });
  await prisma.maxDocumentTraining.create({
    data: {
      organizationId: orgId,
      courseId,
      documentId: document.id,
      userId: learnerId,
    },
  });
  assert.ok(
    await activeMaxStudyReminder(
      prisma,
      plan.id,
      learner.maxUserId,
      new Date(),
    ),
  );
  assert.equal(
    await prisma.certificate.count({
      where: { userId: learnerId, status: "ISSUED" },
    }),
    1,
  );
  await prisma.maxDocumentTraining.update({
    where: {
      documentId_userId: { documentId: document.id, userId: learnerId },
    },
    data: { passedAt: new Date() },
  });
  assert.equal(
    await activeMaxStudyReminder(
      prisma,
      plan.id,
      learner.maxUserId,
      new Date(),
    ),
    null,
  );
  await saveMaxStudyPlan(hr, {
    courseId,
    learnerId,
    dueAt: null,
    remindersEnabled: true,
  });
  assert.equal(
    (await prisma.maxStudyPlan.findUniqueOrThrow({ where: { id: plan.id } }))
      .remindersEnabled,
    false,
  );
});
