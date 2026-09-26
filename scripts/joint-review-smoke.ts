import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import { hash } from "bcryptjs";
import { PrismaClient } from "@prisma/client";
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
