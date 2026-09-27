import "server-only";

import { createHash } from "node:crypto";
import prisma from "@/lib/prisma";
import type { KnowledgeConnectionRepository } from "../application/connect-knowledge-document";
import { approveMaxKnowledgeDocument } from "./approve-max-knowledge-document";
import { findManager } from "./prisma-max-course-documents";

export const prismaKnowledgeConnection: KnowledgeConnectionRepository = {
  async load(identity, courseId, documentId) {
    if (!await findManager(identity, prisma)) return null;
    const document = await prisma.maxCourseDocument.findFirst({
      where: {
        id: documentId,
        organizationId: identity.organizationId,
        courseId,
        approvedAt: { not: null },
        revokedAt: null,
        course: { is: { organizationId: identity.organizationId, status: "PUBLISHED" } },
      },
      select: {
        sourceName: true,
        contentHash: true,
        contentText: true,
        course: { select: { publishedSnapshotJson: true } },
      },
    });
    if (!document?.course.publishedSnapshotJson ||
        createHash("sha256").update(document.contentText).digest("hex") !== document.contentHash) return null;
    return {
      sourceName: document.sourceName,
      contentHash: document.contentHash,
      contentText: document.contentText,
      publishedSnapshotHash: createHash("sha256").update(document.course.publishedSnapshotJson).digest("hex"),
    };
  },
  async approve(identity, courseId, documentId, source, snapshotHash) {
    return approveMaxKnowledgeDocument(prisma, {
      courseId,
      courseDocumentId: documentId,
      vedomoDocumentId: source.documentId,
      vedomoDocumentHash: source.documentHash,
    }, {
      organizationId: identity.organizationId,
      publishedSnapshotHash: snapshotHash,
      findManager: (transaction) => findManager(identity, transaction),
    });
  },
};
