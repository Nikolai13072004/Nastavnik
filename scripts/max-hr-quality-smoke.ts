import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { PrismaClient } from "@prisma/client";
import { createMaxSessionCodec } from "../src/modules/max/infrastructure/learner-session";

const db = new PrismaClient();
const suffix = randomUUID();
const organizationId = "max-pilot-demo-org";
const managerId = `max-quality-hr-${suffix}`;
const learnerId = `max-quality-learner-${suffix}`;
const origin = "https://prodigy-max.45-139-78-17.sslip.io";
let stage = "configuration";

async function main() {
  if (
    process.argv[2] !== "--verify-pilot" ||
    process.env.MAX_LMS_LINKS !== "disabled" ||
    !process.env.MAX_BOT_TOKEN
  ) {
    throw new Error("This check is restricted to the configured pilot");
  }
  const courseId = process.env.MAX_VEDOMO_COURSE_ID;
  assert.ok(courseId);
  const course = await db.course.findUniqueOrThrow({ where: { id: courseId } });
  assert.equal(course.organizationId, organizationId);
  assert.equal(course.status, "PUBLISHED");
  const codec = createMaxSessionCodec(process.env.MAX_BOT_TOKEN);
  const tokens = new Map<string, string>();

  async function request(
    path: string,
    actor: string | null,
    expected: number,
    body?: unknown,
  ) {
    // Keep the shared gateway well below its aggregate request limit.
    await new Promise((resolve) => setTimeout(resolve, 300));
    const response = await fetch(new URL(path, origin), {
      method: body === undefined ? "GET" : "POST",
      headers: {
        ...(actor ? { Authorization: `Bearer ${tokens.get(actor)}` } : {}),
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(
        path === "/api/max/knowledge" ? 110000 : 15000,
      ),
      cache: "no-store",
    });
    assert.equal(response.status, expected, stage);
    assert.equal(response.headers.get("cache-control"), "no-store");
    return response;
  }

  try {
    stage = "temporary actors";
    for (const [id, role] of [
      [managerId, "HR"],
      [learnerId, "USER"],
    ]) {
      await db.user.create({
        data: {
          id,
          login: id,
          name: "Учебная проверка HR",
          passwordHash: "disabled",
          role,
          status: "ACTIVE",
          organizationId,
        },
      });
      const link = await db.maxAccountLink.create({
        data: { userId: id, maxUserId: id, organizationId },
      });
      tokens.set(
        id,
        codec.issue({
          userId: id,
          maxUserId: id,
          organizationId,
          linkedAt: link.createdAt.toISOString(),
        }).token,
      );
    }
    await db.courseUserAssignment.create({
      data: { courseId, userId: learnerId },
    });
    stage = "history and role guards";
    const historyPath = `/api/max/manager-history?learnerId=${learnerId}`;
    const history = await (await request(historyPath, managerId, 200)).json();
    assert.equal(history.learner.id, learnerId);
    assert.ok(
      history.courses.some((item: { id: string }) => item.id === courseId),
    );
    await request(historyPath, learnerId, 403);
    await request(historyPath, null, 401);
    await request("/api/max/study-plan", null, 401, {});
    await request("/api/max/ai-feedback", null, 401, {});

    stage = "deadline and CSV";
    await request("/api/max/study-plan", managerId, 200, {
      courseId,
      learnerId,
      dueAt: new Date(Date.now() + 2 * 86400000).toISOString(),
      remindersEnabled: false,
    });
    const csv = await request(
      `/api/max/manager-history?courseId=${courseId}&export=csv`,
      managerId,
      200,
    );
    assert.ok(csv.headers.get("content-type")?.startsWith("text/csv"));
    assert.ok((await csv.text()).includes("Учебная проверка HR"));

    stage = "live AI and metadata";
    const question = "Что делать, если код привязки потерян?";
    const result = await (
      await request("/api/max/knowledge", learnerId, 200, {
        courseId,
        question,
      })
    ).json();
    assert.equal(result.refused, false);
    assert.ok(result.eventId);
    assert.ok(result.sources.length);
    assert.ok(
      result.sources.every(
        (source: { courseDocumentId: string }) => source.courseDocumentId,
      ),
    );
    const event = await db.maxAiEvent.findUniqueOrThrow({
      where: { id: result.eventId },
    });
    assert.equal(event.userId, learnerId);
    assert.equal(event.outcome, "ANSWERED");
    assert.ok(event.payloadHash);
    stage = "explicit feedback and review";
    const feedback = {
      courseId,
      eventId: result.eventId,
      question,
      result,
      reason: "WRONG_ANSWER",
      comment: "Учебная проверка отправки, не реальная жалоба.",
      consent: true,
    };
    const saved = await (
      await request("/api/max/ai-feedback", learnerId, 200, feedback)
    ).json();
    assert.equal(saved.status, "SAVED");
    const repeated = await (
      await request("/api/max/ai-feedback", learnerId, 200, feedback)
    ).json();
    assert.equal(repeated.status, "ALREADY_REPORTED");
    await request("/api/max/ai-feedback", learnerId, 404, {
      ...feedback,
      question: "Подменённый вопрос",
    });
    const qualityPath = `/api/max/ai-quality?courseId=${courseId}`;
    const quality = await (await request(qualityPath, managerId, 200)).json();
    assert.ok(
      quality.feedback.some(
        (item: { eventId: string }) => item.eventId === result.eventId,
      ),
    );
    await request(qualityPath, learnerId, 403);
    const review = {
      courseId,
      eventId: result.eventId,
      verdict: "CORRECT",
      comment: "Учебная проверка решения HR.",
    };
    await request("/api/max/ai-quality", managerId, 200, review);
    await request("/api/max/ai-quality", managerId, 409, review);
    console.log(
      "HR_QUALITY_SMOKE_PASSED: history, roles, deadline, CSV, live AI, metadata, feedback, review",
    );
  } finally {
    // Delete only this invocation's synthetic data; keep real employees and history.
    await db.auditLogEvent.deleteMany({
      where: { actorId: { in: [managerId, learnerId] } },
    });
    await db.user.deleteMany({ where: { id: { in: [managerId, learnerId] } } });
    console.log(
      "Temporary HR quality profiles removed; real profiles unchanged.",
    );
  }
}

main()
  .catch(() => {
    console.error(
      `HR quality check failed at: ${stage}. Secrets and answer text are not printed.`,
    );
    process.exitCode = 1;
  })
  .finally(() => db.$disconnect());
