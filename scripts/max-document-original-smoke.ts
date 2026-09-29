import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import { PrismaClient } from "@prisma/client";
import { storage } from "../src/lib/storage";
import { createMaxSessionCodec } from "../src/modules/max/infrastructure/learner-session";

let check = "environment";

function syntheticPdf(label: string) {
  const text = `BT /F1 12 Tf 20 40 Td (${label}) Tj ET`;
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 100] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
    `<< /Length ${text.length} >>\nstream\n${text}\nendstream`,
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
  ];
  let pdf = "%PDF-1.4\n";
  const offsets = [0];
  for (const [index, object] of objects.entries()) {
    offsets.push(pdf.length);
    pdf += `${index + 1} 0 obj\n${object}\nendobj\n`;
  }
  const xref = pdf.length;
  pdf += "xref\n0 6\n0000000000 65535 f \n";
  pdf += offsets.slice(1).map((offset) => `${String(offset).padStart(10, "0")} 00000 n \n`).join("");
  pdf += `trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(pdf, "ascii");
}

async function main() {
  assert.ok(process.argv.includes("--create-temporary-profiles"));
  assert.equal(process.env.MAX_DISABLE_EMAIL, "true");
  const origin = new URL(process.env.APP_BASE_URL ?? "");
  assert.equal(origin.origin, "https://prodigy-max.45-139-78-17.sslip.io");
  assert.equal(new URL(process.env.DATABASE_URL ?? "").pathname, "/prodigy_max");
  assert.ok(process.env.MAX_BOT_TOKEN);
  const codec = createMaxSessionCodec(process.env.MAX_BOT_TOKEN);
  const db = new PrismaClient();
  const prefix = `max-original-smoke-${randomUUID()}`;
  const organizationId = `${prefix}-org`;
  const foreignOrg = `${prefix}-foreign`;
  const managerId = `${prefix}-hr`;
  const learnerId = `${prefix}-learner`;
  const outsiderId = `${prefix}-outsider`;
  const courseId = `${prefix}-course`;
  const foreignCourse = `${prefix}-foreign-course`;
  let created = false;
  const originalKeys = new Set<string>();

  async function request(path: string, token?: string, body?: unknown, method = "GET") {
    await delay(320);
    check = `${method} ${new URL(path, origin).pathname}`;
    return fetch(new URL(path, origin), {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(20_000),
    });
  }

  try {
    const links = await db.$transaction(async (tx) => {
      await tx.organization.createMany({ data: [
        { id: organizationId, name: "Temporary original-file check" },
        { id: foreignOrg, name: "Temporary access check" },
      ] });
      const result = [];
      for (const [id, role, org] of [
        [managerId, "HR", organizationId],
        [learnerId, "USER", organizationId],
        [outsiderId, "USER", foreignOrg],
      ]) {
        await tx.user.create({ data: {
          id, login: id, name: "Temporary smoke profile", role,
          organizationId: org, passwordHash: "disabled", status: "ACTIVE",
        } });
        result.push(await tx.maxAccountLink.create({ data: {
          maxUserId: id, userId: id, organizationId: org,
        } }));
      }
      await tx.course.createMany({ data: [
        { id: courseId, title: "Temporary originals", ownerId: managerId, organizationId, status: "PUBLISHED" },
        { id: foreignCourse, title: "Temporary foreign course", ownerId: outsiderId, organizationId: foreignOrg, status: "PUBLISHED" },
      ] });
      await tx.courseUserAssignment.create({ data: { courseId, userId: learnerId } });
      return result;
    });
    created = true;
    const tokens = links.map((link) => codec.issue({
      maxUserId: link.maxUserId,
      userId: link.userId,
      organizationId: link.organizationId,
      linkedAt: link.createdAt.toISOString(),
    }).token);
    const [manager, learner, outsider] = tokens;
    assert.equal((await request("/api/max/documents", learner, {
      courseId, title: "Forbidden upload", sourceName: "blocked.md",
      contentText: "Employee cannot upload.",
    }, "POST")).status, 403);

    for (const [extension, bytes] of [
      ["md", Buffer.from(`\uFEFF# ${prefix}\r\nУчебный исходный файл.\r\n`, "utf8")],
      ["pdf", syntheticPdf(prefix)],
    ] as const) {
      const title = `${prefix}-${extension}`;
      const sourceName = `учебный-исходник.${extension}`;
      assert.equal((await request("/api/max/documents", manager, {
        courseId, title, sourceName, fileBase64: bytes.toString("base64"),
      }, "POST")).status, 201);
      const document = await db.maxCourseDocument.findFirstOrThrow({ where: { courseId, title } });
      assert.ok(document.originalKey);
      originalKeys.add(document.originalKey);
      const detail = `/api/max/documents?courseId=${courseId}&documentId=${document.id}`;
      const download = `${detail}&download=original`;
      const managerDownload = `${download}&scope=manager`;
      assert.equal((await request(download, learner)).status, 404);
      assert.equal((await request(managerDownload, learner)).status, 404);
      assert.equal((await request(managerDownload, manager)).status, 200);
      assert.equal((await request("/api/max/documents", manager, {
        courseId, documentId: document.id, publish: true,
      }, "PUT")).status, 200);
      const response = await request(download, learner);
      check = `${extension} download status`;
      assert.equal(response.status, 200);
      check = `${extension} cache-control`;
      assert.equal(response.headers.get("cache-control"), "no-store");
      check = `${extension} content-type-options`;
      assert.equal(response.headers.get("x-content-type-options"), "nosniff");
      check = `${extension} content-disposition`;
      assert.match(response.headers.get("content-disposition")!, /attachment;.*filename\*=UTF-8''/);
      check = `${extension} original bytes`;
      assert.deepEqual(Buffer.from(await response.arrayBuffer()), bytes);
      assert.equal((await request(download)).status, 401);
      assert.equal((await request(download, outsider)).status, 403);
      assert.equal((await request(download.replace(courseId, foreignCourse), learner)).status, 403);
      const visible = (await (await request(detail, learner)).json()).document;
      check = `${extension} original metadata`;
      assert.equal(visible.hasOriginal, true);
      for (const field of ["originalKey", "originalHash", "originalSize"]) {
        assert.equal(field in visible, false);
      }
      assert.equal((await request(`/uploads/${document.originalKey}`, learner)).status, 404);
      await db.courseUserAssignment.update({
        where: { courseId_userId: { courseId, userId: learnerId } }, data: { expiresAt: new Date(0) },
      });
      assert.equal((await request(download, learner)).status, 403);
      await db.courseUserAssignment.update({
        where: { courseId_userId: { courseId, userId: learnerId } }, data: { expiresAt: null },
      });
      assert.equal((await request("/api/max/documents", manager, {
        courseId, documentId: document.id, publish: false,
      }, "PUT")).status, 200);
      assert.equal((await request(download, learner)).status, 404);
      console.log(`HTTPS ${extension}: exact original, private storage and current access verified.`);
    }
    await db.maxAccountLink.delete({ where: { maxUserId: learnerId } });
    assert.equal((await request(`/api/max/documents?courseId=${courseId}`, learner)).status, 401);
  } finally {
    if (created) {
      const documents = await db.maxCourseDocument.findMany({
        where: { organizationId }, select: { originalKey: true },
      });
      for (const document of documents) if (document.originalKey) originalKeys.add(document.originalKey);
      await db.maxCourseDocument.deleteMany({ where: { organizationId } });
      for (const key of originalKeys) {
        assert.ok(key.startsWith("max-documents/"));
        if (await db.maxCourseDocument.count({ where: { originalKey: key } }) === 0) {
          await storage.delete("uploads", key);
          await db.storageFile.deleteMany({ where: { key } });
        }
      }
      await db.auditLogEvent.deleteMany({ where: { actorId: managerId } });
      await db.course.deleteMany({ where: { id: { in: [courseId, foreignCourse] } } });
      await db.user.deleteMany({ where: { id: { in: [managerId, learnerId, outsiderId] } } });
      await db.organization.deleteMany({ where: { id: { in: [organizationId, foreignOrg] } } });
      console.log("Only temporary original-file profiles, records and unshared files removed.");
    }
    await db.$disconnect();
  }
}

main().catch((error: unknown) => {
  const status = error instanceof assert.AssertionError && ["number", "boolean"].includes(typeof error.actual)
    ? ` actual=${error.actual} expected=${error.expected}` : "";
  const kind = error instanceof Error ? error.name : "UnknownError";
  console.error(`Original-file verification failed at ${check} (${kind})${status}; no credentials or private bytes printed.`);
  process.exitCode = 1;
});
