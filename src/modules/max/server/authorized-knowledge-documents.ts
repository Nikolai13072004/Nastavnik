import "server-only";

import { createHash } from "node:crypto";
import { createListAuthorizedKnowledgeDocuments } from "../application/authorized-knowledge-documents";
import { prismaMaxKnowledgeDocuments } from "../infrastructure/prisma-max-knowledge-documents";
import { loadMaxCourseSnapshot } from "./course-learning";

export const listAuthorizedMaxKnowledgeDocuments = createListAuthorizedKnowledgeDocuments(
  {
    async publishedSnapshotHash(identity, courseId) {
      const loaded = await loadMaxCourseSnapshot(identity, courseId);
      if (loaded.kind !== "ready" || !loaded.course.publishedSnapshotJson) return null;
      return createHash("sha256").update(loaded.course.publishedSnapshotJson).digest("hex");
    },
  },
  prismaMaxKnowledgeDocuments,
);
