import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import prisma from "../src/lib/prisma";
import { storage } from "../src/lib/storage";
import { putBufferDedup } from "../src/lib/storage/dedup";

async function main() {
  const [action, prefix] = process.argv.slice(2);
  assert.ok(["create", "verify", "clean"].includes(action));
  assert.match(prefix, /^max-restore-probe-[a-f0-9-]{36}$/);
  assert.equal(process.env.MAX_DISABLE_EMAIL, "true");
  const database = new URL(process.env.DATABASE_URL ?? "");
  if (action === "verify") {
    assert.equal(process.env.MAX_RESTORE_REVIEW, "true");
    assert.equal(database.hostname, "restore-db");
    assert.equal(database.pathname, "/max_restore_review");
  } else {
    assert.equal(database.pathname, "/prodigy_max");
    assert.equal(process.env.APP_BASE_URL, "https://prodigy-max.45-139-78-17.sslip.io");
  }
  const bytes = Buffer.from(`\uFEFF# Проверка восстановления\r\n${prefix}\r\n`, "utf8");
  const originalHash = createHash("sha256").update(bytes).digest("hex");
  const contentText = bytes.toString("utf8").replace(/^\uFEFF/, "").replace(/\r\n/g, "\n").trim();
  const sourceName = `${prefix}.md`;
  const db = prisma;

  async function clean() {
    const document = await db.maxCourseDocument.findUnique({ where: { id: prefix } });
    if (document) {
      assert.equal(document.organizationId, prefix);
      assert.equal(document.courseId, prefix);
      assert.equal(document.originalHash, originalHash);
      await db.maxCourseDocument.delete({ where: { id: prefix } });
    }
    const files = await db.storageFile.findMany({ where: {
      area: "uploads", originalName: sourceName, sha256: originalHash,
    } });
    for (const file of files) {
      assert.ok(file.key.startsWith("max-documents/"));
      if (await db.maxCourseDocument.count({ where: { originalKey: file.key } }) === 0) {
        await storage.delete("uploads", file.key);
        await db.storageFile.delete({ where: { id: file.id } });
      }
    }
    await db.course.deleteMany({ where: { id: prefix, organizationId: prefix } });
    await db.user.deleteMany({ where: { id: prefix, organizationId: prefix } });
    await db.organization.deleteMany({ where: { id: prefix, name: prefix } });
  }

  try {
    if (action === "create") {
      assert.equal(await db.organization.count({ where: { id: prefix } }), 0);
      const original = await putBufferDedup("uploads", `max-documents/${prefix}.md`, bytes, {
        originalName: sourceName, mimeType: "text/plain;charset=utf-8", purpose: "max-restore-probe", extension: "md",
      });
      try {
        await db.$transaction(async (tx) => {
          await tx.organization.create({ data: { id: prefix, name: prefix } });
          await tx.user.create({ data: {
            id: prefix, login: prefix, name: "Restore probe", role: "USER", status: "ACTIVE",
            passwordHash: "disabled", organizationId: prefix,
          } });
          await tx.course.create({ data: {
            id: prefix, title: "Restore probe", ownerId: prefix, organizationId: prefix, status: "DRAFT",
          } });
          await tx.maxCourseDocument.create({ data: {
            id: prefix, organizationId: prefix, courseId: prefix, title: "Restore probe", sourceName,
            contentText, contentHash: createHash("sha256").update(contentText).digest("hex"),
            originalKey: original.object.key, originalHash: original.sha256,
            originalSize: original.sizeBytes, uploadedById: prefix,
          } });
        });
      } catch (error) {
        await clean();
        throw error;
      }
    } else if (action === "verify") {
      const fixture = await db.maxCourseDocument.findUniqueOrThrow({ where: { id: prefix } });
      assert.equal(fixture.originalHash, originalHash);
      assert.equal(fixture.contentText, contentText);
      assert.equal(fixture.originalSize, bytes.length);
      assert.ok(fixture.originalKey);
      assert.deepEqual(await storage.get("uploads", fixture.originalKey), bytes);
      const documents = await db.maxCourseDocument.findMany({ where: { originalKey: { not: null } } });
      for (const document of documents) {
        assert.ok(document.originalKey && document.originalHash && document.originalSize);
        const original = await storage.get("uploads", document.originalKey);
        assert.equal(original.length, document.originalSize);
        assert.equal(createHash("sha256").update(original).digest("hex"), document.originalHash);
      }
      const approved = await db.maxCourseDocument.findUniqueOrThrow({ where: { id: "max-pilot-onboarding-document" } });
      assert.equal(approved.contentHash, "4ae1eaa63cc6a90372bde88a52d1c94c573c14ef701f7fac262cfc52199acdd2");
      console.log("Restored database references and exact original bytes verified, including BOM and CRLF.");
    } else {
      await clean();
      console.log("Only the unique restore fixture and its unshared file removed from the pilot.");
    }
  } finally {
    await db.$disconnect();
  }
}

main().catch(() => {
  console.error("Restore fixture failed. No credentials, profile data or private file contents printed.");
  process.exitCode = 1;
});
