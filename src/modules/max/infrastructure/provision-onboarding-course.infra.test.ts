import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { before, after, test } from "node:test";
import prisma from "@/lib/prisma";
import { buildPublishedCourseSnapshot } from "@/lib/course-content";
import { completeMaxMaterial, getMaxCourse } from "../server/course-learning";
import { startMaxQuiz, submitMaxQuiz } from "../server/quiz-learning";
import {
  onboardingCourseId,
  onboardingDocumentId,
  onboardingMaterialId,
  onboardingQuizId,
  provisionOnboardingCourse,
} from "./provision-onboarding-course";

const organizationId = "max-pilot-demo-org";
const learnerId = "max-pilot-demo-learner";
const sourceCourseId = "max-pilot-demo-course";
const sourceDocumentId = "onboarding-test-source";
let identity: {
  maxUserId: string;
  userId: string;
  organizationId: string;
  linkedAt: string;
};
let oldState: string;

async function readOldState() {
  return JSON.stringify(
    await prisma.course.findUnique({
      where: { id: sourceCourseId },
      include: { certificates: true, items: { include: { views: true } } },
    }),
  );
}

before(async () => {
  // The infra runner owns a disposable database; fixed pilot IDs never target the VPS.
  await prisma.organization.create({
    data: { id: organizationId, name: "Demo" },
  });
  await prisma.user.create({
    data: {
      id: learnerId,
      login: "max-pilot-demo",
      name: "Demo",
      passwordHash: "disabled",
      organizationId,
    },
  });
  const link = await prisma.maxAccountLink.create({
    data: {
      maxUserId: "onboarding-fixture",
      userId: learnerId,
      organizationId,
    },
  });
  identity = {
    maxUserId: link.maxUserId,
    userId: learnerId,
    organizationId,
    linkedAt: link.createdAt.toISOString(),
  };
  const course = await prisma.course.create({
    data: {
      id: sourceCourseId,
      title: "Legacy",
      organizationId,
      status: "PUBLISHED",
      items: {
        create: {
          id: "onboarding-legacy-material",
          type: "TEXT",
          title: "Legacy",
          orderIndex: 0,
          views: { create: { userId: learnerId, progressPercent: 100 } },
        },
      },
      certificates: {
        create: {
          serial: "onboarding-legacy-certificate",
          userId: learnerId,
          issuedVia: "LEARNING",
          completedAt: new Date(),
          snapshotJson: "legacy-snapshot",
        },
      },
    },
    include: {
      modules: true,
      items: { include: { quiz: { include: { questions: true } } } },
    },
  });
  await prisma.course.update({
    where: { id: sourceCourseId },
    data: {
      publishedSnapshotJson: JSON.stringify(
        buildPublishedCourseSnapshot(course),
      ),
    },
  });
  const contentText = await readFile(
    "docs/drafts/onboarding-policy.md",
    "utf8",
  );
  await prisma.maxCourseDocument.create({
    data: {
      id: sourceDocumentId,
      organizationId,
      courseId: sourceCourseId,
      title: "Approved",
      sourceName: "onboarding-policy.md",
      contentText,
      contentHash: createHash("sha256").update(contentText).digest("hex"),
      approvedAt: new Date(),
      uploadedById: "owner",
      approvedById: "owner",
    },
  });
  oldState = await readOldState();
});

after(async () => {
  await prisma.auditLogEvent.deleteMany({
    where: { objectId: onboardingCourseId },
  });
  await prisma.outboxEvent.deleteMany({
    where: { payloadJson: { contains: onboardingCourseId } },
  });
  await prisma.course.deleteMany({
    where: { id: { in: [onboardingCourseId, sourceCourseId] } },
  });
  await prisma.user.deleteMany({ where: { id: learnerId } });
  await prisma.organization.deleteMany({ where: { id: organizationId } });
  await prisma.$disconnect();
});

test("new unified course preserves legacy completion and approved source bytes", async () => {
  assert.equal(await provisionOnboardingCourse(prisma), "CREATED");
  assert.equal(await readOldState(), oldState);
  const [source, copy] = await Promise.all([
    prisma.maxCourseDocument.findUniqueOrThrow({
      where: { id: sourceDocumentId },
    }),
    prisma.maxCourseDocument.findUniqueOrThrow({
      where: { id: onboardingDocumentId },
    }),
  ]);
  assert.equal(copy.contentText, source.contentText);
  assert.equal(copy.contentHash, source.contentHash);
  assert.equal(copy.approvedAt?.getTime(), source.approvedAt?.getTime());
  const loaded = await getMaxCourse(identity, onboardingCourseId);
  assert.ok(loaded.course);
  assert.equal(loaded.course.completed, false);
  assert.equal(loaded.course.materials.length, 1);
  assert.equal(loaded.course.quizzes.length, 1);
  assert.equal(loaded.course.quizzes[0].questionCount, 3);
});

test("repeat provisioning does not modify a published course or progress", async () => {
  const before = await prisma.course.findUniqueOrThrow({
    where: { id: onboardingCourseId },
  });
  assert.equal(await provisionOnboardingCourse(prisma), "ALREADY_EXISTS");
  assert.deepEqual(
    await prisma.course.findUniqueOrThrow({
      where: { id: onboardingCourseId },
    }),
    before,
  );
  assert.equal(await readOldState(), oldState);
});

test("reading alone does not finish the unified course; one passed test does", async () => {
  assert.deepEqual(
    await startMaxQuiz(identity, onboardingCourseId, onboardingQuizId),
    { error: "MATERIAL_REQUIRED" },
  );
  const completed = await completeMaxMaterial(
    identity,
    onboardingCourseId,
    onboardingMaterialId,
  );
  assert.ok(completed.progress);
  assert.equal(
    (await getMaxCourse(identity, onboardingCourseId)).course?.completed,
    false,
  );
  const started = await startMaxQuiz(
    identity,
    onboardingCourseId,
    onboardingQuizId,
  );
  assert.ok(started.attemptId && started.questions);
  const questions = await prisma.question.findMany({
    where: { quizId: onboardingQuizId },
  });
  const answers = Object.fromEntries(
    questions.map((question) => [
      question.id,
      JSON.parse(question.config).correctIndex,
    ]),
  );
  const passed = await submitMaxQuiz(
    identity,
    onboardingCourseId,
    onboardingQuizId,
    started.attemptId,
    answers,
  );
  assert.equal(passed.result?.outcome, "PASSED");
  assert.equal(
    (await getMaxCourse(identity, onboardingCourseId)).course?.completed,
    true,
  );
  assert.deepEqual(
    await submitMaxQuiz(
      identity,
      onboardingCourseId,
      onboardingQuizId,
      started.attemptId,
      answers,
    ),
    passed,
  );
  assert.equal(
    await prisma.quizAttempt.count({
      where: { quizId: onboardingQuizId, userId: learnerId },
    }),
    1,
  );
  assert.equal(await readOldState(), oldState);
});

test("changed or unapproved sources and occupied course IDs fail without overwrites", async () => {
  await prisma.maxCourseDocument.update({
    where: { id: sourceDocumentId },
    data: { revokedAt: new Date() },
  });
  await assert.rejects(
    provisionOnboardingCourse(prisma),
    /unchanged, approved/,
  );
  await prisma.maxCourseDocument.update({
    where: { id: sourceDocumentId },
    data: { revokedAt: null },
  });
  await prisma.maxCourseDocument.update({
    where: { id: onboardingDocumentId },
    data: { contentText: "Changed by owner" },
  });
  await assert.rejects(provisionOnboardingCourse(prisma), /differs/);
  assert.equal(
    (
      await prisma.maxCourseDocument.findUniqueOrThrow({
        where: { id: onboardingDocumentId },
      })
    ).contentText,
    "Changed by owner",
  );
  assert.equal(await readOldState(), oldState);
});
