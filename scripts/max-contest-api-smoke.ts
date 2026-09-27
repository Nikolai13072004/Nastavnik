import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { setTimeout as delay } from "node:timers/promises";
import { PrismaClient } from "@prisma/client";
import { createMaxSessionCodec } from "../src/modules/max/infrastructure/learner-session";

const contractDirectory = process.env.MAX_CONTEST_CONTRACT_DIR ?? "/run/contest";
let stage = "environment";

async function main() {
  assert.ok(process.argv.includes("--create-temporary-profiles"));
  assert.equal(process.env.MAX_DISABLE_EMAIL, "true");
  assert.equal(new URL(process.env.DATABASE_URL ?? "").pathname, "/prodigy_max");
  const origin = new URL(process.env.APP_BASE_URL ?? "");
  assert.equal(origin.origin, "https://prodigy-max.45-139-78-17.sslip.io");
  const botToken = process.env.MAX_BOT_TOKEN;
  assert.ok(botToken);
  const contract = JSON.parse(await readFile(`${contractDirectory}/openapi.json`, "utf8"));
  const data = JSON.parse(await readFile(`${contractDirectory}/test-data.json`, "utf8"));
  assert.equal(data.synthetic, true);
  assert.equal(data.organizationId, "max-pilot-demo-org");
  assert.equal(data.courseId, "max-pilot-onboarding-course");
  const Ajv = createRequire(import.meta.url)("ajv");
  const validator = new Ajv({ nullable: true, allErrors: true });
  validator.addSchema(contract, "max-contest-api");
  const codec = createMaxSessionCodec(botToken);
  const db = new PrismaClient();
  const prefix = `max-api-smoke-${randomUUID()}`;
  const learnerId = `${prefix}-learner`;
  const managerId = `${prefix}-hr`;
  const profileIds = [learnerId, managerId];
  let created = false;

  async function request(
    path: string, schema: string, expected = 200, token?: string, body?: object,
  ) {
    await delay(350);
    stage = `${body ? "POST" : "GET"} ${new URL(path, origin).pathname} ${schema}`;
    const response = await fetch(new URL(path, origin), {
      method: body ? "POST" : "GET",
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(110_000),
    });
    assert.equal(response.status, expected);
    assert.ok(response.headers.get("content-type")?.startsWith("application/json"));
    assert.equal(response.headers.get("cache-control"), "no-store");
    const value = await response.json();
    const valid = validator.getSchema(`max-contest-api#/components/schemas/${schema}`);
    assert.ok(valid);
    assert.equal(valid(value), true, `Response differs from ${schema}`);
    return value;
  }

  try {
    const course = await db.course.findUniqueOrThrow({ where: { id: data.courseId } });
    assert.equal(course.organizationId, data.organizationId);
    assert.equal(course.status, "PUBLISHED");
    const source = await db.maxCourseDocument.findUniqueOrThrow({ where: { id: data.documentId } });
    assert.equal(source.contentHash, data.documentSha256);
    assert.ok(source.approvedAt && !source.revokedAt);
    stage = "temporary profiles";
    const links = await db.$transaction(async (tx) => {
      const result = [];
      for (const [id, role] of [[learnerId, "USER"], [managerId, "HR"]]) {
        await tx.user.create({ data: {
          id, login: id, name: "Temporary API check", passwordHash: "disabled",
          role, status: "ACTIVE", organizationId: data.organizationId,
        } });
        result.push(await tx.maxAccountLink.create({ data: {
          maxUserId: id, userId: id, organizationId: data.organizationId,
        } }));
      }
      await tx.courseUserAssignment.create({ data: { courseId: data.courseId, userId: learnerId } });
      return result;
    });
    created = true;
    const [learner, manager] = links.map((link) => codec.issue({
      maxUserId: link.maxUserId, userId: link.userId,
      organizationId: link.organizationId, linkedAt: link.createdAt.toISOString(),
    }).token);
    await request("/api/max/identity", "Error", 401, undefined, { initData: "not-a-signed-max-launch" });
    await request("/api/max/courses", "Error", 401);
    const courses = await request("/api/max/courses", "Courses", 200, learner);
    assert.equal(courses.courses.length, 1);
    assert.equal(courses.courses[0].id, data.courseId);
    const coursePath = `/api/max/course?courseId=${data.courseId}`;
    await request(coursePath, "Course", 200, learner);
    await request("/api/max/course", "MaterialCompleted", 200, learner, {
      courseId: data.courseId, materialId: data.materialId,
    });
    const attempt = await request("/api/max/quiz", "QuizStart", 200, learner, {
      action: "start", courseId: data.courseId, quizId: data.quizId,
    });
    const answers: Record<string, number> = {};
    for (const question of attempt.questions) {
      const expectedText = data.quizAnswersByPrompt[question.prompt];
      const index = question.options.indexOf(expectedText);
      assert.ok(index >= 0, "Synthetic answer must match the current delivered options");
      answers[question.id] = index;
    }
    const submission = {
      action: "submit", courseId: data.courseId, quizId: data.quizId,
      attemptId: attempt.attemptId, answers,
    };
    const result = await request("/api/max/quiz", "QuizResult", 200, learner, submission);
    assert.equal(result.result.outcome, "PASSED");
    assert.deepEqual(await request("/api/max/quiz", "QuizResult", 200, learner, submission), result);
    assert.equal(await db.quizAttempt.count({ where: { userId: learnerId } }), 1);
    assert.equal(await db.certificate.count({ where: { userId: learnerId } }), 1);
    const saved = await request(coursePath, "Course", 200, learner);
    assert.equal(saved.course.completed, true);
    await request(`/api/max/documents?courseId=${data.courseId}`, "Documents", 200, learner);
    const sourcePath = `/api/max/documents?courseId=${data.courseId}&documentId=${data.documentId}`;
    const document = await request(sourcePath, "DocumentDetail", 200, learner);
    assert.equal(document.document.contentHash, data.documentSha256);
    for (const field of ["originalKey", "originalHash", "originalSize"]) {
      assert.equal(field in document.document, false);
    }
    const answer = await request("/api/max/knowledge", "KnowledgeAnswer", 200, learner, {
      courseId: data.courseId, question: data.question,
    });
    assert.equal(answer.refused, false);
    assert.ok(answer.sources.some((item: { courseDocumentId?: string; documentHash: string }) =>
      item.courseDocumentId === data.documentId && item.documentHash === data.documentSha256,
    ));
    const reportPath = `/api/max/manager-report?courseId=${data.courseId}`;
    const report = await request(reportPath, "ManagerReport", 200, manager);
    assert.ok(report.report.learners.some((item: { id: string; completed: boolean }) =>
      item.id === learnerId && item.completed,
    ));
    await request(reportPath, "Error", 403, learner);
    await request("/api/max/course?courseId=max-contest-unassigned-course", "Error", 403, learner);
    await db.courseUserAssignment.update({
      where: { courseId_userId: { courseId: data.courseId, userId: learnerId } },
      data: { expiresAt: new Date(0) },
    });
    await request(sourcePath, "Error", 403, learner);
    console.log("Contest API: real HTTPS responses match OpenAPI; test, report, AI source and access checks passed.");
  } finally {
    if (created) {
      await db.$transaction(async (tx) => {
        const certificates = await tx.certificate.findMany({ where: { userId: learnerId }, select: { id: true } });
        await tx.auditLogEvent.deleteMany({ where: { OR: [
          { actorId: { in: profileIds } }, { metadataJson: { contains: learnerId } },
          { objectId: { in: certificates.map(({ id }) => id) } },
        ] } });
        await tx.outboxEvent.deleteMany({ where: { payloadJson: { contains: learnerId } } });
        await tx.user.deleteMany({ where: { id: { in: profileIds } } });
      });
      console.log("Only this run's temporary profiles and results removed.");
    }
    await db.$disconnect();
  }
}

main().catch(() => {
  console.error(`Contest API failed at ${stage}; credentials and private responses were not printed.`);
  process.exitCode = 1;
});
