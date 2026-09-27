import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { after, before, test } from "node:test";
import prisma from "@/lib/prisma";
import { storage } from "@/lib/storage";
import { GET, POST } from "./route";
import { createMaxSessionCodec } from "@/modules/max/infrastructure/learner-session";
import {
  downloadMaxDocument,
  getMaxCourseDocuments,
  getMaxManagerDocuments,
  manageMaxCourseDocuments,
} from "@/modules/max/server/course-documents";

const prefix = `max-document-test-${randomUUID()}`;
const ownOrg = `${prefix}-own`;
const otherOrg = `${prefix}-other`;
const managerId = `${prefix}-manager`;
const learnerId = `${prefix}-learner`;
const otherLearnerId = `${prefix}-other-learner`;
const ownCourse = `${prefix}-course`;
const originalCourse = `${prefix}-original-course`;
const otherCourse = `${prefix}-foreign-course`;
const managerMax = `${prefix}-manager-max`;
const learnerMax = `${prefix}-learner-max`;
const otherMax = `${prefix}-other-max`;
const originalKeys = new Set<string>();

async function identity(maxUserId: string) {
  const link = await prisma.maxAccountLink.findUniqueOrThrow({ where: { maxUserId } });
  return {
    maxUserId,
    userId: link.userId,
    organizationId: link.organizationId,
    linkedAt: link.createdAt.toISOString(),
  };
}

before(async () => {
  await prisma.organization.createMany({ data: [
    { id: ownOrg, name: ownOrg },
    { id: otherOrg, name: otherOrg },
  ] });
  await prisma.user.createMany({ data: [
    { id: managerId, login: managerId, name: "HR", role: "HR", passwordHash: "disabled", organizationId: ownOrg },
    { id: learnerId, login: learnerId, name: "Learner", passwordHash: "disabled", organizationId: ownOrg },
    { id: otherLearnerId, login: otherLearnerId, name: "Other", passwordHash: "disabled", organizationId: otherOrg },
  ] });
  await prisma.maxAccountLink.createMany({ data: [
    { maxUserId: managerMax, userId: managerId, organizationId: ownOrg },
    { maxUserId: learnerMax, userId: learnerId, organizationId: ownOrg },
    { maxUserId: otherMax, userId: otherLearnerId, organizationId: otherOrg },
  ] });
  await prisma.course.createMany({ data: [
    { id: ownCourse, title: "Our course", ownerId: managerId, organizationId: ownOrg, status: "PUBLISHED" },
    { id: originalCourse, title: "Original files", ownerId: managerId, organizationId: ownOrg, status: "PUBLISHED" },
    { id: otherCourse, title: "Other course", ownerId: otherLearnerId, organizationId: otherOrg, status: "PUBLISHED" },
  ] });
  await prisma.courseUserAssignment.create({ data: { courseId: ownCourse, userId: learnerId } });
  await prisma.courseUserAssignment.create({ data: { courseId: originalCourse, userId: learnerId } });
});

after(async () => {
  await prisma.auditLogEvent.deleteMany({ where: { actorId: managerId, objectType: "max_course_document" } });
  await prisma.maxCourseDocument.deleteMany({ where: { organizationId: { in: [ownOrg, otherOrg] } } });
  for (const key of originalKeys) {
    assert.ok(key.startsWith("max-documents/"));
    assert.equal(await prisma.maxCourseDocument.count({ where: { originalKey: key } }), 0);
    await prisma.storageFile.deleteMany({ where: { key } });
    await storage.delete("uploads", key);
  }
  await prisma.course.deleteMany({ where: { id: { in: [ownCourse, originalCourse, otherCourse] } } });
  await prisma.user.deleteMany({ where: { id: { in: [managerId, learnerId, otherLearnerId] } } });
  await prisma.organization.deleteMany({ where: { id: { in: [ownOrg, otherOrg] } } });
  await prisma.$disconnect();
});

test("original download preserves bytes and checks publication, binding and enrollment", async () => {
  await prisma.user.update({ where: { id: managerId }, data: { organizationId: ownOrg } });
  await prisma.courseUserAssignment.update({
    where: { courseId_userId: { courseId: originalCourse, userId: learnerId } }, data: { expiresAt: null },
  });
  const manager = await identity(managerMax);
  const learner = await identity(learnerMax);
  const foreign = await identity(otherMax);
  const bytes = Buffer.from(`\uFEFF# ${prefix}\r\nОригинал с переносами.\r\n`, "utf8");
  const draft = { courseId: originalCourse, title: "Оригинал", sourceName: "original.md",
    contentText: bytes.toString("utf8"), originalBytes: bytes };
  assert.equal(await manageMaxCourseDocuments.upload(manager, draft), "CREATED");
  const document = await prisma.maxCourseDocument.findFirstOrThrow({ where: { courseId: originalCourse, title: "Оригинал" } });
  originalKeys.add(document.originalKey!);
  assert.ok(document.originalKey);
  assert.equal(document.originalSize, bytes.length);
  assert.deepEqual(await downloadMaxDocument(learner, originalCourse, document.id, false), { error: "NOT_FOUND" });
  assert.deepEqual((await downloadMaxDocument(manager, originalCourse, document.id, true)).bytes, bytes);
  assert.deepEqual(await downloadMaxDocument(learner, originalCourse, document.id, true), { error: "NOT_FOUND" });
  assert.equal(await manageMaxCourseDocuments.setPublished(manager, originalCourse, document.id, true), "UPDATED");
  const visible = (await getMaxCourseDocuments(learner, originalCourse, document.id)).document!;
  assert.equal(visible.hasOriginal, true);
  assert.equal("originalKey" in visible, false);
  assert.deepEqual((await downloadMaxDocument(learner, originalCourse, document.id, false)).bytes, bytes);
  assert.deepEqual(await downloadMaxDocument(foreign, originalCourse, document.id, false), { error: "FORBIDDEN" });
  assert.deepEqual(await downloadMaxDocument(learner, otherCourse, document.id, false), { error: "FORBIDDEN" });

  assert.equal(await manageMaxCourseDocuments.upload(manager, { ...draft, title: "Та же копия" }), "CREATED");
  const duplicate = await prisma.maxCourseDocument.findFirstOrThrow({ where: { courseId: originalCourse, title: "Та же копия" } });
  assert.equal(duplicate.originalKey, document.originalKey);
  await prisma.courseUserAssignment.update({
    where: { courseId_userId: { courseId: originalCourse, userId: learnerId } }, data: { expiresAt: new Date(0) },
  });
  assert.deepEqual(await downloadMaxDocument(learner, originalCourse, document.id, false), { error: "FORBIDDEN" });
  await prisma.courseUserAssignment.update({
    where: { courseId_userId: { courseId: originalCourse, userId: learnerId } }, data: { expiresAt: null },
  });
  assert.equal(await manageMaxCourseDocuments.setPublished(manager, originalCourse, document.id, false), "UPDATED");
  assert.deepEqual(await downloadMaxDocument(learner, originalCourse, document.id, false), { error: "NOT_FOUND" });
  assert.deepEqual((await downloadMaxDocument(manager, originalCourse, duplicate.id, true)).bytes, bytes);
  await prisma.maxAccountLink.delete({ where: { maxUserId: learnerMax } });
  assert.deepEqual(await downloadMaxDocument(learner, originalCourse, duplicate.id, false), { error: "SESSION_REVOKED" });
  await prisma.maxAccountLink.create({ data: { maxUserId: learnerMax, userId: learnerId, organizationId: ownOrg } });
});

test("HR publishes a document only to active learners of the same organization", async () => {
  const manager = await identity(managerMax);
  const learner = await identity(learnerMax);
  const foreign = await identity(otherMax);
  const draft = { courseId: ownCourse, title: "Правила работы", sourceName: "rules.txt", contentText: "Проверенный рабочий текст." };

  assert.equal(await manageMaxCourseDocuments.upload(learner, draft), "FORBIDDEN");
  assert.equal(await manageMaxCourseDocuments.upload(manager, { ...draft, courseId: otherCourse }), "NOT_FOUND");
  assert.equal(await manageMaxCourseDocuments.upload(manager, draft), "CREATED");

  const stored = await prisma.maxCourseDocument.findFirstOrThrow({ where: { organizationId: ownOrg, courseId: ownCourse } });
  assert.equal(stored.approvedAt, null);
  assert.equal(stored.contentText, draft.contentText);
  assert.deepEqual(await getMaxCourseDocuments(learner, ownCourse), { documents: [] });
  assert.deepEqual(await getMaxCourseDocuments(learner, ownCourse, stored.id), { error: "NOT_FOUND" });
  assert.deepEqual(await getMaxCourseDocuments(foreign, ownCourse), { error: "FORBIDDEN" });
  assert.equal((await getMaxManagerDocuments(manager, ownCourse, stored.id)).document?.contentText, draft.contentText);
  assert.deepEqual(await getMaxManagerDocuments(learner, ownCourse), { error: "NOT_FOUND" });

  assert.equal(await manageMaxCourseDocuments.setPublished(manager, ownCourse, stored.id, true), "UPDATED");
  assert.equal((await getMaxCourseDocuments(learner, ownCourse)).documents?.length, 1);
  assert.equal((await getMaxCourseDocuments(learner, ownCourse, stored.id)).document?.contentText, draft.contentText);
  assert.equal(await manageMaxCourseDocuments.setPublished(manager, otherCourse, stored.id, false), "NOT_FOUND");

  await prisma.courseUserAssignment.update({
    where: { courseId_userId: { courseId: ownCourse, userId: learnerId } },
    data: { expiresAt: new Date(0) },
  });
  assert.deepEqual(await getMaxCourseDocuments(learner, ownCourse, stored.id), { error: "FORBIDDEN" });

  assert.equal(await manageMaxCourseDocuments.setPublished(manager, ownCourse, stored.id, false), "UPDATED");
  assert.equal(await prisma.maxCourseDocument.count({ where: { id: stored.id, revokedAt: { not: null } } }), 1);
  assert.equal(await manageMaxCourseDocuments.setPublished(manager, ownCourse, stored.id, true), "NOT_FOUND");
  assert.equal(await prisma.auditLogEvent.count({ where: { actorId: managerId, objectId: stored.id } }), 3);

  await prisma.user.update({ where: { id: managerId }, data: { organizationId: otherOrg } });
  assert.equal(await manageMaxCourseDocuments.upload(manager, draft), "FORBIDDEN");
});

function syntheticPdf() {
  const text = "BT /F1 12 Tf 20 40 Td (Synthetic training document.) Tj ET";
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
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

test("HTTP upload and original download preserve PDF and UTF-8 bytes without exposing storage keys", async () => {
  await prisma.user.update({ where: { id: managerId }, data: { organizationId: ownOrg } });
  await prisma.courseUserAssignment.update({
    where: { courseId_userId: { courseId: originalCourse, userId: learnerId } }, data: { expiresAt: null },
  });
  const previousToken = process.env.MAX_BOT_TOKEN;
  process.env.MAX_BOT_TOKEN = `local-review-${randomUUID()}`;
  const manager = await identity(managerMax);
  const learner = await identity(learnerMax);
  const codec = createMaxSessionCodec(process.env.MAX_BOT_TOKEN);
  const managerHeaders = { Authorization: `Bearer ${codec.issue(manager).token}`, "Content-Type": "application/json" };
  const learnerHeaders = { Authorization: `Bearer ${codec.issue(learner).token}` };
  try {
    for (const [extension, bytes] of [
      ["md", Buffer.from(`\uFEFF# ${prefix}\r\nИсходный файл.\r\n`, "utf8")],
      ["pdf", syntheticPdf()],
    ] as const) {
      const sourceName = `исходник.${extension}`;
      const title = `${prefix}-${extension}`;
      const upload = await POST(new Request("http://localhost/api/max/documents", {
        method: "POST", headers: managerHeaders,
        body: JSON.stringify({ courseId: originalCourse, title, sourceName, fileBase64: bytes.toString("base64") }),
      }));
      assert.equal(upload.status, 201);
      const document = await prisma.maxCourseDocument.findFirstOrThrow({ where: { courseId: originalCourse, title } });
      originalKeys.add(document.originalKey!);
      assert.ok(document.contentText.length > 0);
      assert.equal(await manageMaxCourseDocuments.setPublished(manager, originalCourse, document.id, true), "UPDATED");
      const url = `http://localhost/api/max/documents?courseId=${originalCourse}&documentId=${document.id}&download=original`;
      const download = await GET(new Request(url, { headers: learnerHeaders }));
      assert.equal(download.status, 200);
      assert.equal(download.headers.get("cache-control"), "no-store");
      assert.equal(download.headers.get("x-content-type-options"), "nosniff");
      assert.match(download.headers.get("content-disposition")!, /attachment;.*filename\*=UTF-8''/);
      assert.deepEqual(Buffer.from(await download.arrayBuffer()), bytes);
      assert.equal((await GET(new Request(url))).status, 401);
      assert.equal((await GET(new Request(url.replace(originalCourse, otherCourse), { headers: learnerHeaders }))).status, 403);
      const read = await GET(new Request(url.replace("&download=original", ""), { headers: learnerHeaders }));
      const visible = (await read.json()).document;
      assert.equal(visible.hasOriginal, true);
      assert.equal("originalKey" in visible, false);
      assert.equal("originalHash" in visible, false);
      const valid = await storage.get("uploads", document.originalKey!);
      await storage.put("uploads", document.originalKey!, Buffer.alloc(valid.length));
      assert.equal((await GET(new Request(url, { headers: learnerHeaders }))).status, 503);
      await storage.put("uploads", document.originalKey!, valid);
      assert.equal(await manageMaxCourseDocuments.setPublished(manager, originalCourse, document.id, false), "UPDATED");
      assert.equal((await GET(new Request(url, { headers: learnerHeaders }))).status, 404);
    }
    const invalid = await POST(new Request("http://localhost/api/max/documents", {
      method: "POST", headers: managerHeaders,
      body: JSON.stringify({ courseId: originalCourse, title: "Invalid", sourceName: "bad.txt", fileBase64: "/w==" }),
    }));
    assert.equal(invalid.status, 400);
    assert.equal((await invalid.json()).error, "INVALID_ENCODING");
  } finally {
    if (previousToken === undefined) delete process.env.MAX_BOT_TOKEN;
    else process.env.MAX_BOT_TOKEN = previousToken;
  }
});
