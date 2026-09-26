import "server-only";

import { createHash } from "node:crypto";
import prisma from "@/lib/prisma";
import type { MaxKnowledgeDocumentRepository } from "../application/authorized-knowledge-documents";

export const prismaMaxKnowledgeDocuments: MaxKnowledgeDocumentRepository = {
  async listApproved(organizationId, courseId, snapshotHash) {
    const documents = await prisma.maxKnowledgeDocument.findMany({
      where: {
        organizationId,
        courseId,
        publishedSnapshotHash: snapshotHash,
        revokedAt: null,
        courseDocument: {
          is: {
            organizationId,
            courseId,
            approvedAt: { not: null },
            revokedAt: null,
          },
        },
      },
      select: {
        vedomoDocumentId: true,
        vedomoDocumentHash: true,
        courseDocument: { select: { id: true, contentText: true, contentHash: true } },
      },
    });
    return documents.flatMap((document) => {
      if (!document.courseDocument ||
          document.courseDocument.contentHash !== document.vedomoDocumentHash ||
          createHash("sha256").update(document.courseDocument.contentText).digest("hex") !==
          document.vedomoDocumentHash) return [];
      return [{
        id: document.vedomoDocumentId,
        contentHash: document.vedomoDocumentHash,
        contentText: document.courseDocument.contentText,
        courseDocumentId: document.courseDocument.id,
      }];
    });
  },
};
