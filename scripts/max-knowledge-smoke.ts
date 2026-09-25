import assert from "node:assert/strict";
import { PrismaClient } from "@prisma/client";
import { createMaxSessionCodec } from "../src/modules/max/infrastructure/learner-session";

async function main() {
  const expectedDocumentId = process.argv[2];
  if (!expectedDocumentId) throw new Error("Provide the approved Vedomo document ID");
  const botToken = process.env.MAX_BOT_TOKEN;
  if (!botToken) throw new Error("MAX_BOT_TOKEN is missing");

  const db = new PrismaClient();
  let session: string;
  try {
    const link = await db.maxAccountLink.findFirst({
      where: { organizationId: "max-pilot-demo-org", user: { status: "ACTIVE" } },
      select: { maxUserId: true, userId: true, organizationId: true, createdAt: true },
    });
    if (!link) throw new Error("No active pilot MAX account link");
    session = createMaxSessionCodec(botToken).issue({
      maxUserId: link.maxUserId,
      userId: link.userId,
      organizationId: link.organizationId,
      linkedAt: link.createdAt.toISOString(),
    }).token;
  } finally {
    await db.$disconnect();
  }

  const endpoint = new URL("/api/max/knowledge",
    process.env.MAX_KNOWLEDGE_SMOKE_ORIGIN ?? "http://127.0.0.1:3000");
  const headers = { Authorization: `Bearer ${session}`, "Content-Type": "application/json" };
  const correct = await fetch(endpoint, {
    method: "POST",
    headers,
    body: JSON.stringify({ courseId: "max-pilot-demo-course", question: "Что делать, если код привязки потерян?" }),
    signal: AbortSignal.timeout(120_000),
  });
  assert.equal(correct.status, 200);
  const answer = await correct.json();
  assert.equal(answer.refused, false);
  assert.ok(answer.sources.some((source: { documentId: string }) =>
    source.documentId === expectedDocumentId));
  console.log("approved course: verified answer and source");

  const foreign = await fetch(endpoint, {
    method: "POST",
    headers,
    body: JSON.stringify({ courseId: "max-pilot-assessment-course", question: "Что делать, если код потерян?" }),
  });
  assert.equal(foreign.status, 403);
  console.log("other course: denied");

  const anonymous = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ courseId: "max-pilot-demo-course", question: "Что делать, если код потерян?" }),
  });
  assert.equal(anonymous.status, 401);
  console.log("anonymous request: denied");
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : "Smoke test failed");
  process.exitCode = 1;
});
