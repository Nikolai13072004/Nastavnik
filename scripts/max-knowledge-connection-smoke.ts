import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { PrismaClient } from "@prisma/client";
import { createMaxSessionCodec } from "../src/modules/max/infrastructure/learner-session";
import { onboardingCourseId, onboardingDocumentId } from "../src/modules/max/infrastructure/provision-onboarding-course";

async function main() {
  const botToken = process.env.MAX_BOT_TOKEN;
  if (!process.argv.includes("--create-temporary-profiles") || !botToken ||
      process.env.MAX_DISABLE_EMAIL !== "true" || process.env.MAX_VEDOMO_ENABLED !== "true" ||
      process.env.MAX_VEDOMO_COURSE_ID !== onboardingCourseId) {
    throw new Error("Explicit isolated pilot check with email disabled is required");
  }
  const origin = new URL(process.env.APP_BASE_URL ?? "");
  assert.equal(origin.protocol, "https:");
  const organizationId = "max-pilot-demo-org";
  const suffix = randomUUID();
  const actorIds = [`max-connection-smoke-hr-${suffix}`, `max-connection-smoke-user-${suffix}`];
  const db = new PrismaClient();
  try {
    console.log("Checking approved pilot mapping.");
    const mapping = await db.maxKnowledgeDocument.findFirstOrThrow({
      where: { organizationId, courseId: onboardingCourseId, courseDocumentId: onboardingDocumentId, revokedAt: null },
    });
    const tokens = await db.$transaction(async (tx) => {
      const result: string[] = [];
      for (const [index, id] of actorIds.entries()) {
        await tx.user.create({ data: {
          id, login: id, name: "Temporary connection check", passwordHash: "disabled",
          organizationId, role: index === 0 ? "HR" : "EMPLOYEE", status: "ACTIVE",
        } });
        const link = await tx.maxAccountLink.create({ data: { maxUserId: id, userId: id, organizationId } });
        result.push(createMaxSessionCodec(botToken).issue({
          maxUserId: id, userId: id, organizationId, linkedAt: link.createdAt.toISOString(),
        }).token);
      }
      return result;
    });
    const input = { courseId: onboardingCourseId, documentId: onboardingDocumentId };
    async function connect(token: string | null, body: object, expected: number) {
      const response = await fetch(new URL("/api/max/documents/knowledge", origin), {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify(body), signal: AbortSignal.timeout(20_000), redirect: "error",
      });
      assert.equal(response.status, expected, `Connection HTTP ${response.status}; expected ${expected}`);
      return response.json();
    }
    await connect(null, input, 401);
    await connect(tokens[1], input, 404);
    await connect(tokens[0], { ...input, courseId: "foreign-course" }, 404);
    await connect(tokens[0], { ...input, organizationId: "foreign" }, 400);
    assert.equal((await connect(tokens[0], input, 200)).status, "APPROVED");
    assert.equal((await connect(tokens[0], input, 200)).status, "APPROVED");
    const updated = await db.maxKnowledgeDocument.findUniqueOrThrow({ where: { id: mapping.id } });
    assert.equal(updated.vedomoDocumentId, mapping.vedomoDocumentId);
    assert.equal(updated.vedomoDocumentHash, mapping.vedomoDocumentHash);
    assert.equal(await db.maxKnowledgeDocument.count({
      where: { organizationId, courseId: onboardingCourseId, courseDocumentId: onboardingDocumentId },
    }), 1);
    const query = new URLSearchParams({ scope: "manager", courseId: onboardingCourseId });
    const response = await fetch(new URL(`/api/max/documents?${query}`, origin), {
      headers: { Authorization: `Bearer ${tokens[0]}` }, signal: AbortSignal.timeout(15_000),
    });
    assert.equal(response.status, 200);
    const list = await response.json();
    assert.equal(list.aiConnectionEnabled, true);
    assert.equal(list.documents.find((document: { id: string }) => document.id === onboardingDocumentId)?.aiConnected, true);
    console.log("HTTPS: HR connection, repeat without duplicates, saved status and access denials passed.");
  } finally {
    await db.$transaction(async (tx) => {
      await tx.auditLogEvent.deleteMany({ where: { actorId: { in: actorIds } } });
      await tx.user.deleteMany({ where: { id: { in: actorIds } } });
    });
    await db.$disconnect();
  }
}
main().catch((error: unknown) => {
  if (error instanceof assert.AssertionError) console.error(error.message);
  else if (error instanceof Error) console.error(error.name);
  console.error("Connection smoke check failed; credentials and responses were not printed.");
  process.exitCode = 1;
});
