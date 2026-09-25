import "server-only";

import { createManageMaxDocuments } from "../application/manage-course-documents";
import type { MaxLearnerIdentity } from "../application/list-courses";
import {
  listMaxManagerDocuments,
  listMaxPublishedDocuments,
  prismaMaxCourseDocuments,
  readMaxManagerDocument,
  readMaxPublishedDocument,
  getMaxDocumentAudience,
  ensureCurrentMaxDocumentTraining,
} from "../infrastructure/prisma-max-course-documents";
import { hasCourseAccess } from "./course-learning";

export const manageMaxCourseDocuments = createManageMaxDocuments(prismaMaxCourseDocuments);

export async function getMaxCourseDocuments(identity: MaxLearnerIdentity, courseId: string, documentId?: string) {
  const access = await hasCourseAccess(identity, courseId);
  if (access !== "ALLOWED") return { error: access } as const;
  await ensureCurrentMaxDocumentTraining(identity.organizationId, courseId, identity.userId);
  if (documentId) {
    const document = await readMaxPublishedDocument(identity.organizationId, courseId, documentId, identity.userId);
    if (!document) return { error: "NOT_FOUND" } as const;
    return { document: {
      ...document,
      checkOptions: document.checkOptionsJson ? JSON.parse(document.checkOptionsJson) as string[] : null,
      checkOptionsJson: undefined,
      training: document.trainingRecipients[0] ?? null,
      trainingRecipients: undefined,
    } } as const;
  }
  const documents = await listMaxPublishedDocuments(identity.organizationId, courseId);
  return { documents } as const;
}

export async function getMaxManagerDocuments(identity: MaxLearnerIdentity, courseId: string, documentId?: string) {
  if (documentId) {
    const document = await readMaxManagerDocument(identity, courseId, documentId);
    return document ? { document } as const : { error: "NOT_FOUND" } as const;
  }
  const documents = await listMaxManagerDocuments(identity, courseId);
  if (!documents) return { error: "NOT_FOUND" } as const;
  const audience = await getMaxDocumentAudience(identity, courseId);
  return { documents, audience } as const;
}
