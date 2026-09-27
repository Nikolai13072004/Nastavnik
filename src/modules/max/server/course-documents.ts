import "server-only";
import { createHash } from "node:crypto";
import { storage } from "@/lib/storage";

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

function visibleDocument<T extends { originalKey: string | null; originalHash: string | null; originalSize: number | null }>(document: T) {
  const { originalKey, originalHash, originalSize, ...visible } = document;
  return { ...visible, hasOriginal: Boolean(originalKey && originalHash && originalSize) };
}

export async function getMaxCourseDocuments(identity: MaxLearnerIdentity, courseId: string, documentId?: string) {
  const access = await hasCourseAccess(identity, courseId);
  if (access !== "ALLOWED") return { error: access } as const;
  await ensureCurrentMaxDocumentTraining(identity.organizationId, courseId, identity.userId);
  if (documentId) {
    const document = await readMaxPublishedDocument(identity.organizationId, courseId, documentId, identity.userId);
    if (!document) return { error: "NOT_FOUND" } as const;
    return { document: {
      ...visibleDocument(document),
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
    if (!document) return { error: "NOT_FOUND" } as const;
    return { document: visibleDocument(document) } as const;
  }
  const documents = await listMaxManagerDocuments(identity, courseId);
  if (!documents) return { error: "NOT_FOUND" } as const;
  const audience = await getMaxDocumentAudience(identity, courseId);
  return {
    documents,
    audience,
    aiConnectionEnabled: process.env.MAX_VEDOMO_ENABLED === "true" && courseId === process.env.MAX_VEDOMO_COURSE_ID,
    aiImportEnabled: process.env.MAX_VEDOMO_IMPORT_ENABLED === "true",
  } as const;
}

export async function downloadMaxDocument(
  identity: MaxLearnerIdentity,
  courseId: string,
  documentId: string,
  manager: boolean,
) {
  if (!manager) {
    const access = await hasCourseAccess(identity, courseId);
    if (access !== "ALLOWED") return { error: access } as const;
  }
  const document = manager
    ? await readMaxManagerDocument(identity, courseId, documentId)
    : await readMaxPublishedDocument(identity.organizationId, courseId, documentId, identity.userId);
  if (!document?.originalKey || !document.originalHash || !document.originalSize) {
    return { error: "NOT_FOUND" } as const;
  }
  const info = await storage.stat("uploads", document.originalKey);
  if (!info.isFile() || info.size !== document.originalSize || info.size > 512 * 1024) {
    return { error: "TEMPORARILY_UNAVAILABLE" } as const;
  }
  const bytes = await storage.get("uploads", document.originalKey);
  if (createHash("sha256").update(bytes).digest("hex") !== document.originalHash) {
    return { error: "TEMPORARILY_UNAVAILABLE" } as const;
  }
  return { bytes, sourceName: document.sourceName } as const;
}
