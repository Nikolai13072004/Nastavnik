import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import { hash } from "bcryptjs";
import { PrismaClient } from "@prisma/client";
import { storage } from "../src/lib/storage";
import { createMaxSessionCodec } from "../src/modules/max/infrastructure/learner-session";
import { approveMaxKnowledgeDocument } from "../src/modules/max/infrastructure/approve-max-knowledge-document";
import { createVedomoClient } from "../src/modules/max/infrastructure/vedomo-client";
import { onboardingCourseId, onboardingDocumentId } from "../src/modules/max/infrastructure/provision-onboarding-course";

async function checkCredentialsLogin(db: PrismaClient) {
  const id = `max-auth-review-${randomUUID()}`;
  const password = randomUUID();
  await db.user.create({ data: {
    id, login: id, name: "Local auth review", role: "USER", status: "ACTIVE",
    organizationId: "max-pilot-demo-org", passwordHash: await hash(password, 12),
  } });
  try {
    const origin = "http://web:3000";
    const csrf = await fetch(`${origin}/api/auth/csrf`);
    assert.equal(csrf.status, 200);
    const { csrfToken } = await csrf.json();
    const cookies = csrf.headers.getSetCookie().map((cookie) => cookie.split(";")[0]);
    async function login(value: string) {
      return fetch(`${origin}/api/auth/callback/credentials`, {
        method: "POST", redirect: "manual",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-Auth-Return-Redirect": "true", Cookie: cookies.join("; "),
        },
        body: new URLSearchParams({ csrfToken, login: id, password: value, callbackUrl: `${origin}/max` }),
        signal: AbortSignal.timeout(15_000),
      });
    }
    const denied = await login("incorrect-password");
    assert.match((await denied.json()).url, /error=CredentialsSignin/);
    const accepted = await login(password);
    assert.doesNotMatch((await accepted.json()).url, /error=/);
    const sessionCookies = accepted.headers.getSetCookie().map((cookie) => cookie.split(";")[0]);
    const response = await fetch(`${origin}/api/auth/session`, {
      headers: { Cookie: [...cookies, ...sessionCookies].join("; ") },
      signal: AbortSignal.timeout(15_000),
    });
    assert.equal(response.status, 200);
    assert.equal((await response.json()).user.id, id);
    console.log("Credentials login: incorrect password denied, valid password creates a session.");
  } finally {
    await db.auditLogEvent.deleteMany({ where: { actorId: id } });
    await db.user.delete({ where: { id } });
  }
}

async function checkPublishedDocumentImport(db: PrismaClient) {
  const actorId = `max-import-review-${randomUUID()}`;
  const title = `Local import check ${randomUUID()}`;
  const organizationId = "max-pilot-demo-org";
  let documentId: string | undefined;
  let originalKey: string | null = null;
  let stage = "create review account";
  await db.user.create({ data: {
    id: actorId, login: actorId, name: "Local document import", role: "HR", status: "ACTIVE",
    organizationId, passwordHash: "disabled",
  } });
  try {
    stage = "link review account";
    const link = await db.maxAccountLink.create({ data: {
      maxUserId: actorId, userId: actorId, organizationId,
    } });
    const token = createMaxSessionCodec(process.env.MAX_BOT_TOKEN!).issue({
      maxUserId: actorId, userId: actorId, organizationId, linkedAt: link.createdAt.toISOString(),
    }).token;
    async function request(path: string, method: string, body: object, expected: number) {
      await delay(350);
      const response = await fetch(`https://joint-gateway:8443${path}`, {
        method,
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify(body), redirect: "error", signal: AbortSignal.timeout(20_000),
      });
      assert.equal(response.status, expected, `${method} ${path}: unexpected HTTP status`);
      return response.json();
    }
    const contentText = `# ${title}\n\nПеред запуском учебного устройства Q750 проверяют защитный кожух.\n`;
    stage = "upload document";
    await request("/api/max/documents", "POST", {
      courseId: onboardingCourseId, title, sourceName: "import-review.md", contentText,
    }, 201);
    const document = await db.maxCourseDocument.findFirstOrThrow({
      where: { courseId: onboardingCourseId, title, uploadedById: actorId },
    });
    documentId = document.id;
    originalKey = document.originalKey;
    const input = { courseId: onboardingCourseId, documentId };
    stage = "deny draft import";
    assert.equal((await request("/api/max/documents/knowledge", "POST", input, 404)).status, "NOT_FOUND");
    stage = "publish document";
    await request("/api/max/documents", "PUT", { ...input, publish: true }, 200);
    stage = "start indexing";
    assert.equal((await request("/api/max/documents/knowledge", "POST", input, 202)).status, "PROCESSING");
    let approved = false;
    stage = "wait for indexing";
    for (let attempt = 0; attempt < 45; attempt += 1) {
      await delay(3000);
      const response = await fetch("https://joint-gateway:8443/api/max/documents/knowledge", {
        method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify(input), signal: AbortSignal.timeout(20_000), redirect: "error",
      });
      assert.ok(response.status === 200 || response.status === 202, "Indexing must succeed without a retry");
      const result = await response.json();
      if (result.status === "APPROVED") {
        approved = true;
        break;
      }
      assert.equal(result.status, "PROCESSING");
    }
    assert.equal(approved, true, "Real document indexing must finish within the review timeout");
    stage = "verify source mapping";
    const mapping = await db.maxKnowledgeDocument.findFirstOrThrow({ where: { courseDocumentId: documentId } });
    assert.equal(mapping.vedomoDocumentHash, document.contentHash);
    const client = createVedomoClient(process.env.MAX_VEDOMO_ORIGIN!, process.env.MAX_VEDOMO_SERVICE_TOKEN!);
    const source = await client.findDocument(organizationId, onboardingCourseId, document.contentHash);
    assert.equal(source?.documentId, mapping.vedomoDocumentId);
    stage = "verify repeat import";
    assert.equal((await request("/api/max/documents/knowledge", "POST", input, 200)).status, "APPROVED");
    assert.equal(await db.maxKnowledgeDocument.count({ where: { courseDocumentId: documentId } }), 1);
    stage = "revoke publication";
    await request("/api/max/documents", "PUT", { ...input, publish: false }, 200);
    assert.equal((await request("/api/max/documents/knowledge", "POST", input, 404)).status, "NOT_FOUND");
    assert.ok((await db.maxCourseDocument.findUniqueOrThrow({ where: { id: documentId } })).revokedAt);
    console.log("HR document: upload, draft denial, publication, real indexing, exact hash, repeat and revocation passed.");
  } catch (error) {
    const detail = error instanceof Error ? error.message : "unknown error";
    console.error(`HR document review failed at ${stage}: ${detail}`);
    throw error;
  } finally {
    if (documentId) {
      await db.maxKnowledgeDocument.deleteMany({ where: { courseDocumentId: documentId } });
      await db.maxCourseDocument.delete({ where: { id: documentId } });
      if (originalKey && await db.maxCourseDocument.count({ where: { originalKey } }) === 0) {
        await storage.delete("uploads", originalKey);
        await db.storageFile.deleteMany({ where: { key: originalKey } });
      }
    }
    await db.auditLogEvent.deleteMany({ where: { actorId } });
    await db.user.delete({ where: { id: actorId } });
  }
}

async function main() {
  const database = new URL(process.env.DATABASE_URL ?? "");
  const botToken = process.env.MAX_BOT_TOKEN ?? "";
  assert.equal(process.env.MAX_JOINT_REVIEW, "true");
  assert.equal(database.hostname, "db");
  assert.equal(database.pathname, "/max_review");
  assert.ok(botToken.startsWith("local-review-"));
  assert.equal(process.env.MAX_DISABLE_EMAIL, "true");
  assert.equal(process.env.APP_BASE_URL, "https://joint-gateway:8443");

  const run = (script: string, ...args: string[]) => execFileSync(process.execPath,
    ["node_modules/tsx/dist/cli.mjs", `scripts/${script}`, ...args], { stdio: "inherit" });
  run("max-pilot-setup.ts", "setup");
  run("max-pilot-setup.ts", "assessment");
  run("max-pilot-setup.ts", "scope");
  run("import-max-demo-document.ts", "/run/onboarding-policy.md");
  run("max-onboarding-setup.ts", "--create-pilot-course");

  const db = new PrismaClient();
  try {
    const document = await db.maxCourseDocument.findUniqueOrThrow({ where: { id: onboardingDocumentId } });
    const client = createVedomoClient(process.env.MAX_VEDOMO_ORIGIN ?? "", process.env.MAX_VEDOMO_SERVICE_TOKEN ?? "");
    const source = await client.findDocument("max-pilot-demo-org", onboardingCourseId, document.contentHash);
    assert.ok(source, "The indexed source must be found over verified HTTPS");
    assert.equal((await client.getDocument("max-pilot-demo-org", onboardingCourseId, source.documentId)).documentHash,
      document.contentHash);
    await assert.rejects(client.findDocument("foreign-org", onboardingCourseId, document.contentHash));
    await assert.rejects(client.findDocument("max-pilot-demo-org", "foreign-course", document.contentHash));
    assert.equal(await approveMaxKnowledgeDocument(db, {
      courseId: onboardingCourseId, courseDocumentId: onboardingDocumentId,
      vedomoDocumentId: source.documentId, vedomoDocumentHash: source.documentHash,
    }), "APPROVED");
    await checkCredentialsLogin(db);
    if (process.env.MAX_VEDOMO_IMPORT_ENABLED === "true") {
      await checkPublishedDocumentImport(db);
    }
  } finally {
    await db.$disconnect();
  }
  run("max-knowledge-connection-smoke.ts", "--create-temporary-profiles");
  await delay(2_500);
  run("max-onboarding-smoke.ts", "--create-temporary-profiles");
  console.log("Joint review passed: migrations, indexed source, verified HTTPS, access isolation and learning results.");
  console.log("AI generation and native MAX delivery are deliberately not exercised by this offline review.");
}

main().catch(() => {
  console.error("Joint review failed; credentials and private responses were not printed.");
  process.exitCode = 1;
});
