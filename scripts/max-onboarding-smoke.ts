import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import { PrismaClient } from "@prisma/client";
import { createMaxSessionCodec } from "../src/modules/max/infrastructure/learner-session";
import {
  onboardingCourseId,
  onboardingMaterialId,
  onboardingQuizId,
} from "../src/modules/max/infrastructure/provision-onboarding-course";

async function main() {
  const botToken = process.env.MAX_BOT_TOKEN;
  if (
    !botToken ||
    process.env.MAX_DISABLE_EMAIL !== "true" ||
    !process.argv.includes("--create-temporary-profiles")
  ) {
    throw new Error(
      "Explicit isolated pilot check with email disabled is required",
    );
  }
  const origin = new URL(process.env.APP_BASE_URL ?? "");
  if (origin.protocol !== "https:") throw new Error("HTTPS required");
  const organizationId = "max-pilot-demo-org";
  const learnerId = `max-smoke-${randomUUID()}`;
  const managerId = `max-smoke-${randomUUID()}`;
  const db = new PrismaClient();
  let created = false;
  try {
    const course = await db.course.findUniqueOrThrow({
      where: { id: onboardingCourseId },
    });
    assert.equal(course.organizationId, organizationId);
    assert.equal(course.status, "PUBLISHED");
    const links = await db.$transaction(async (tx) => {
      const result = [];
      for (const [id, role] of [
        [learnerId, "Ученик"],
        [managerId, "HR"],
      ]) {
        await tx.user.create({
          data: {
            id,
            login: id,
            name: "Temporary smoke profile",
            role,
            organizationId,
            passwordHash: "disabled",
            status: "ACTIVE",
          },
        });
        result.push(
          await tx.maxAccountLink.create({
            data: { maxUserId: id, userId: id, organizationId },
          }),
        );
      }
      await tx.courseUserAssignment.create({
        data: { userId: learnerId, courseId: onboardingCourseId },
      });
      return result;
    });
    created = true;
    const tokens = links.map(
      (link) =>
        createMaxSessionCodec(botToken).issue({
          maxUserId: link.maxUserId,
          userId: link.userId,
          organizationId,
          linkedAt: link.createdAt.toISOString(),
        }).token,
    );

    async function request(
      path: string,
      token: string | null,
      body?: object,
      expectedStatus = 200,
    ) {
      // Respect the gateway's 5 requests/second limit; do not retry mutating requests.
      await delay(220);
      const headers: Record<string, string> = {};
      if (token) headers.Authorization = `Bearer ${token}`;
      if (body) headers["Content-Type"] = "application/json";
      const response = await fetch(new URL(path, origin), {
        method: body ? "POST" : "GET",
        headers,
        body: body ? JSON.stringify(body) : undefined,
        signal: AbortSignal.timeout(15_000),
      });
      assert.equal(
        response.status,
        expectedStatus,
        `${path}: unexpected status`,
      );
      return response.json();
    }
    const detailPath = `/api/max/course?courseId=${onboardingCourseId}`;
    const quizBody = { courseId: onboardingCourseId, quizId: onboardingQuizId };
    const listed = await request("/api/max/courses", tokens[0]);
    assert.ok(
      listed.courses.some(
        (item: { id: string }) => item.id === onboardingCourseId,
      ),
    );
    await request(detailPath, null, undefined, 401);
    const blocked = await request(
      "/api/max/quiz",
      tokens[0],
      { ...quizBody, action: "start" },
      403,
    );
    assert.equal(blocked.error, "MATERIAL_REQUIRED");
    await request("/api/max/course", tokens[0], {
      courseId: onboardingCourseId,
      materialId: onboardingMaterialId,
    });
    assert.equal(
      (await request(detailPath, tokens[0])).course.completed,
      false,
    );
    const started = await request("/api/max/quiz", tokens[0], {
      ...quizBody,
      action: "start",
    });
    const questions = await db.question.findMany({
      where: { quizId: onboardingQuizId },
    });
    const answers = Object.fromEntries(
      questions.map((question) => [
        question.id,
        JSON.parse(question.config).correctIndex,
      ]),
    );
    const submission = {
      ...quizBody,
      action: "submit",
      attemptId: started.attemptId,
      answers,
    };
    const passed = await request("/api/max/quiz", tokens[0], submission);
    assert.equal(passed.result.outcome, "PASSED");
    assert.deepEqual(
      await request("/api/max/quiz", tokens[0], submission),
      passed,
    );
    assert.equal(
      await db.quizAttempt.count({
        where: { userId: learnerId, quizId: onboardingQuizId },
      }),
      1,
    );
    assert.equal(
      await db.certificate.count({
        where: { userId: learnerId, courseId: onboardingCourseId },
      }),
      1,
    );
    assert.equal((await request(detailPath, tokens[0])).course.completed, true);
    await request(
      `/api/max/manager-report?courseId=${onboardingCourseId}`,
      tokens[0],
      undefined,
      403,
    );
    const report = await request(
      `/api/max/manager-report?courseId=${onboardingCourseId}`,
      tokens[1],
    );
    const row = report.report.learners.find(
      (item: { id: string }) => item.id === learnerId,
    );
    assert.equal(row?.completed, true);
    assert.equal(row?.passedQuizzes, 1);
    await db.courseUserAssignment.update({
      where: {
        courseId_userId: { courseId: onboardingCourseId, userId: learnerId },
      },
      data: { expiresAt: new Date(0) },
    });
    await request(detailPath, tokens[0], undefined, 403);
    console.log(
      "HTTPS onboarding: material gate, one passed attempt, completion, HR report and expired access verified",
    );
  } finally {
    if (created) {
      await db.$transaction(async (tx) => {
        const certificates = await tx.certificate.findMany({
          where: { userId: learnerId },
          select: { id: true },
        });
        await tx.auditLogEvent.deleteMany({
          where: {
            OR: [
              { actorId: { in: [learnerId, managerId] } },
              { metadataJson: { contains: learnerId } },
              { objectId: { in: certificates.map(({ id }) => id) } },
            ],
          },
        });
        await tx.outboxEvent.deleteMany({
          where: { payloadJson: { contains: learnerId } },
        });
        // These two random IDs were created by this run; cascade removes only their test results.
        await tx.user.deleteMany({
          where: { id: { in: [learnerId, managerId] } },
        });
      });
      console.log(
        "Temporary smoke profiles and their test records removed; pilot learner history unchanged",
      );
    }
    await db.$disconnect();
  }
}

main().catch(() => {
  console.error(
    "Onboarding smoke check failed; inspect pilot state. No secrets printed.",
  );
  process.exitCode = 1;
});
