import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { PrismaClient } from "@prisma/client";

async function main() {
  const database = new URL(process.env.DATABASE_URL ?? "");
  assert.equal(process.env.MAX_JOINT_REVIEW, "true");
  assert.equal(database.hostname, "db");
  assert.equal(database.pathname, "/max_review");
  assert.ok(process.env.MAX_BOT_TOKEN?.startsWith("local-review-"));
  const db = new PrismaClient();
  const maxUserId = `review-ai-${randomUUID()}`;
  try {
    const document = await db.maxKnowledgeDocument.findFirstOrThrow({
      where: {
        organizationId: "max-pilot-demo-org",
        courseId: "max-pilot-onboarding-course",
        revokedAt: null,
      },
      select: { vedomoDocumentId: true },
    });
    assert.equal(await db.maxAccountLink.count(), 0, "Review must not contain real MAX links");
    await db.maxAccountLink.create({
      data: {
        maxUserId,
        userId: "max-pilot-demo-learner",
        organizationId: "max-pilot-demo-org",
      },
    });
    execFileSync(process.execPath, [
      "node_modules/tsx/dist/cli.mjs",
      "scripts/max-knowledge-smoke.ts",
      document.vedomoDocumentId,
    ], {
      stdio: "inherit",
      env: {
        ...process.env,
        MAX_KNOWLEDGE_SMOKE_ORIGIN: "https://joint-gateway:8443",
      },
    });
  } finally {
    await db.maxAccountLink.deleteMany({ where: { maxUserId } });
    await db.$disconnect();
  }
}

main().catch(() => {
  console.error("CLEAN_AI_FAILED; no credentials or private response printed.");
  process.exitCode = 1;
});
