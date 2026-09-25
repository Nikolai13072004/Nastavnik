import { createHash } from "node:crypto";
import { Prisma, PrismaClient } from "@prisma/client";

type MappingInput = {
  courseId: string;
  courseDocumentId: string;
  vedomoDocumentId: string;
  vedomoDocumentHash: string;
};

export type MappingResult =
  | "APPROVED"
  | "REVOKED"
  | "NOT_FOUND"
  | "NOT_APPROVED"
  | "UNSUPPORTED_FORMAT"
  | "HASH_MISMATCH"
  | "CONFLICT";

function validateInput(input: MappingInput) {
  for (const id of [input.courseId, input.courseDocumentId, input.vedomoDocumentId]) {
    if (!id || id.length > 128) throw new Error("Invalid document mapping ID");
  }
  if (!/^[a-f0-9]{64}$/.test(input.vedomoDocumentHash)) {
    throw new Error("Invalid Vedomo document hash");
  }
}

export async function approveMaxKnowledgeDocument(db: PrismaClient, input: MappingInput): Promise<MappingResult> {
  validateInput(input);

  return db.$transaction(async (transaction) => {
    const course = await transaction.course.findUnique({
      where: { id: input.courseId },
      select: { organizationId: true, status: true, publishedSnapshotJson: true },
    });
    const document = await transaction.maxCourseDocument.findUnique({
      where: { id: input.courseDocumentId },
      select: {
        organizationId: true, courseId: true, sourceName: true, contentHash: true, contentText: true,
        approvedAt: true, revokedAt: true,
      },
    });
    if (!course?.organizationId || course.status !== "PUBLISHED" || !course.publishedSnapshotJson ||
        !document || document.organizationId !== course.organizationId || document.courseId !== input.courseId) {
      return "NOT_FOUND";
    }
    if (!document.approvedAt || document.revokedAt) return "NOT_APPROVED";
    if (!/\.(txt|md)$/i.test(document.sourceName)) return "UNSUPPORTED_FORMAT";
    if (document.contentHash !== input.vedomoDocumentHash ||
        createHash("sha256").update(document.contentText).digest("hex") !== input.vedomoDocumentHash) {
      return "HASH_MISMATCH";
    }

    const existing = await transaction.maxKnowledgeDocument.findFirst({
      where: {
        organizationId: course.organizationId,
        courseId: input.courseId,
        OR: [
          { vedomoDocumentId: input.vedomoDocumentId },
          { courseDocumentId: input.courseDocumentId },
        ],
      },
    });
    if (existing && (existing.vedomoDocumentId !== input.vedomoDocumentId ||
        existing.courseDocumentId !== input.courseDocumentId)) return "CONFLICT";

    const publishedSnapshotHash = createHash("sha256")
      .update(course.publishedSnapshotJson).digest("hex");
    if (existing) {
      await transaction.maxKnowledgeDocument.update({
        where: { id: existing.id },
        data: {
          vedomoDocumentHash: input.vedomoDocumentHash,
          publishedSnapshotHash,
          approvedAt: new Date(),
          revokedAt: null,
        },
      });
    } else {
      await transaction.maxKnowledgeDocument.create({
        data: {
          organizationId: course.organizationId,
          courseId: input.courseId,
          courseDocumentId: input.courseDocumentId,
          vedomoDocumentId: input.vedomoDocumentId,
          vedomoDocumentHash: input.vedomoDocumentHash,
          publishedSnapshotHash,
        },
      });
    }
    return "APPROVED";
  }, { isolationLevel: Prisma.TransactionIsolationLevel.Serializable });
}

export async function revokeMaxKnowledgeDocument(
  db: PrismaClient,
  courseId: string,
  vedomoDocumentId: string,
): Promise<MappingResult> {
  if (!courseId || courseId.length > 128 || !vedomoDocumentId || vedomoDocumentId.length > 128) {
    throw new Error("Invalid document mapping ID");
  }
  const updated = await db.maxKnowledgeDocument.updateMany({
    where: { courseId, vedomoDocumentId, revokedAt: null },
    data: { revokedAt: new Date() },
  });
  return updated.count === 1 ? "REVOKED" : "NOT_FOUND";
}
