import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { PrismaClient } from "@prisma/client";

const organizationId = "max-pilot-demo-org";
const courseId = "max-pilot-demo-course";
const sourceName = "onboarding-policy.md";
const actor = "system:max-pilot-content-import";

async function main() {
  const path = process.argv[2];
  if (!path) throw new Error("Provide the approved Markdown file path");

  const contentText = await readFile(path, "utf8");
  const contentHash = createHash("sha256").update(contentText).digest("hex");
  const db = new PrismaClient();
  try {
    const document = await db.$transaction(async (transaction) => {
      const course = await transaction.course.findUnique({
        where: { id: courseId },
        select: { organizationId: true, status: true, publishedSnapshotJson: true },
      });
      if (course?.organizationId !== organizationId || course.status !== "PUBLISHED" ||
          !course.publishedSnapshotJson) {
        throw new Error("The pilot course is not published in the expected organization");
      }

      const existing = await transaction.maxCourseDocument.findMany({
        where: { organizationId, courseId, sourceName, revokedAt: null },
        select: { id: true, contentHash: true, approvedAt: true },
      });
      if (existing.length > 1 || existing.some((item) =>
        item.contentHash !== contentHash || !item.approvedAt)) {
        throw new Error("A different or unapproved pilot document already exists");
      }
      if (existing[0]) return existing[0];

      const created = await transaction.maxCourseDocument.create({
        data: {
          organizationId,
          courseId,
          title: "Первый день сотрудника в MAX",
          sourceName,
          contentText,
          contentHash,
          uploadedById: actor,
          approvedById: actor,
          approvedAt: new Date(),
        },
        select: { id: true, contentHash: true },
      });
      await transaction.auditLogEvent.create({
        data: {
          actorLogin: actor,
          actorName: "Pilot content import",
          action: "max_document:publish",
          objectType: "max_course_document",
          objectId: created.id,
          objectLabel: "Первый день сотрудника в MAX",
          metadataJson: JSON.stringify({ organizationId, courseId, contentHash, approval: "owner-confirmed" }),
        },
      });
      return created;
    });
    process.stdout.write(`document_id=${document.id}\ndocument_hash=${document.contentHash}\n`);
  } finally {
    await db.$disconnect();
  }
}

main().catch((error: unknown) => {
  process.stderr.write(`${error instanceof Error ? error.message : "Import failed"}\n`);
  process.exitCode = 1;
});
