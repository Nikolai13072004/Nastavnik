import assert from "node:assert/strict";
import { randomUUID, createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { PrismaClient } from "@prisma/client";
import { createMaxSessionCodec } from "../src/modules/max/infrastructure/learner-session";
import {
  DEMO_CASES,
  DEMO_DOCUMENT_HASH,
  screenEvalAnswer,
  validateDemoDocument,
  type EvalAnswer,
} from "./max-ai-eval-core";

const organizationId = "max-pilot-demo-org";
const origin = "https://prodigy-max.45-139-78-17.sslip.io";
const db = new PrismaClient();
const userId = `max-ai-eval-${randomUUID()}`;
let stage = "configuration";

type CaseResult = {
  id: string;
  category: string;
  expected: string;
  question: string;
  answer: string | null;
  refused: boolean | null;
  sourceIds: string[];
  durationMs: number;
  problems: string[];
  review: "pending";
};

async function request(url: URL, token: string, question?: string, courseId?: string): Promise<Response> {
  return fetch(url, {
    method: question ? "POST" : "GET",
    headers: {
      Authorization: `Bearer ${token}`,
      ...(question ? { "Content-Type": "application/json" } : {}),
    },
    body: question ? JSON.stringify({ courseId, question }) : undefined,
    signal: AbortSignal.timeout(question ? 120_000 : 15_000),
    cache: "no-store",
  });
}

async function main() {
  if (process.argv[2] !== "--verify-pilot" || process.env.MAX_LMS_LINKS !== "disabled") {
    throw new Error("This evaluation is restricted to the configured MAX pilot");
  }
  const botToken = process.env.MAX_BOT_TOKEN;
  const courseId = process.env.MAX_VEDOMO_COURSE_ID;
  assert.ok(botToken && courseId, "Pilot configuration is incomplete");
  const course = await db.course.findUniqueOrThrow({ where: { id: courseId } });
  assert.equal(course.organizationId, organizationId);
  assert.equal(course.status, "PUBLISHED");

  stage = "approved demo document";
  const approved = await db.maxKnowledgeDocument.findMany({
    where: { organizationId, courseId, vedomoDocumentHash: DEMO_DOCUMENT_HASH, revokedAt: null },
    include: { courseDocument: true },
  });
  assert.equal(approved.length, 1, "Exactly one approved demo document is required");
  const mapping = approved[0];
  const document = mapping.courseDocument;
  assert.ok(document && document.approvedAt && !document.revokedAt);
  assert.equal(document.contentHash, DEMO_DOCUMENT_HASH);
  assert.deepEqual(validateDemoDocument(document.contentText), []);

  const codec = createMaxSessionCodec(botToken);
  let actorCreated = false;
  const results: CaseResult[] = [];
  try {
    stage = "temporary learner";
    await db.user.create({
      data: {
        id: userId,
        login: userId,
        name: "Проверка учебного AI",
        passwordHash: "disabled",
        role: "USER",
        status: "ACTIVE",
        organizationId,
      },
    });
    actorCreated = true;
    const link = await db.maxAccountLink.create({
      data: { userId, maxUserId: userId, organizationId },
    });
    await db.courseUserAssignment.create({ data: { courseId, userId } });
    const session = codec.issue({
      userId,
      maxUserId: userId,
      organizationId,
      linkedAt: link.createdAt.toISOString(),
    }).token;

    stage = "source opening";
    const documentUrl = new URL("/api/max/documents", origin);
    documentUrl.searchParams.set("courseId", courseId);
    documentUrl.searchParams.set("documentId", document.id);
    const opened = await request(documentUrl, session);
    assert.equal(opened.status, 200);
    const openedDocument = (await opened.json()).document;
    assert.equal(openedDocument.id, document.id);
    assert.equal(openedDocument.contentText, document.contentText);

    for (const item of DEMO_CASES) {
      stage = `question ${item.id}`;
      // This is a small sequential check, not a load test.
      await new Promise((resolve) => setTimeout(resolve, 350));
      const started = performance.now();
      let answer: EvalAnswer | null = null;
      let problems: string[] = [];
      try {
        const response = await request(new URL("/api/max/knowledge", origin), session, item.question, courseId);
        if (response.status !== 200) {
          problems = [`http_${response.status}`];
        } else {
          answer = await response.json() as EvalAnswer;
          if (typeof answer.answer !== "string" || typeof answer.refused !== "boolean" ||
              !Array.isArray(answer.sources)) {
            problems = ["invalid_response"];
            answer = null;
          } else {
            problems = screenEvalAnswer(
              item,
              answer,
              mapping.vedomoDocumentId,
              document.id,
              document.contentText,
            );
          }
        }
      } catch {
        problems = ["request_failed"];
      }
      results.push({
        id: item.id,
        category: item.category,
        expected: item.expected,
        question: item.question,
        answer: answer?.answer ?? null,
        refused: answer?.refused ?? null,
        sourceIds: answer?.sources.map((source) => source.courseDocumentId ?? "missing") ?? [],
        durationMs: Math.round(performance.now() - started),
        problems,
        review: "pending",
      });
      console.log(`${item.id}: ${problems.length ? `CHECK ${problems.join(",")}` : "SCREEN_OK"}`);
    }
  } finally {
    if (actorCreated) {
      // Cascades remove this run's assignment and AI events. No real profile is touched.
      await db.user.delete({ where: { id: userId } });
    }
  }

  stage = "report";
  const report = {
    createdAt: new Date().toISOString(),
    scope: "One approved synthetic MAX course document; sequential production API requests",
    courseId,
    documentSha256: createHash("sha256").update(document.contentText).digest("hex"),
    cases: results.length,
    screenPassed: results.filter((item) => item.problems.length === 0).length,
    answerable: results.filter((item) => item.expected === "answer").length,
    refusal: results.filter((item) => item.expected === "refusal").length,
    humanReviewed: 0,
    note: "The automatic screen checks routing, source identity and simple signals. It is not an accuracy score. Review answer meaning and cited passages before making a quality claim.",
    results,
  };
  const reportPath = process.env.MAX_AI_EVAL_REPORT || path.join("output", "contest", "max-ai-eval.json");
  await mkdir(path.dirname(reportPath), { recursive: true });
  await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, { mode: 0o600 });
  console.log(`MAX_AI_EVAL_SCREEN: ${report.screenPassed}/${report.cases}; human review pending; report: ${reportPath}`);
  if (report.screenPassed !== report.cases) process.exitCode = 1;
}

main()
  .catch(() => {
    console.error(`MAX AI evaluation failed at ${stage}. No tokens or answers are logged.`);
    process.exitCode = 1;
  })
  .finally(() => db.$disconnect());
