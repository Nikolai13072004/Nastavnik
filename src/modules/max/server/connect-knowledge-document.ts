import "server-only";

import type { MaxLearnerIdentity } from "../application/list-courses";
import { createConnectKnowledgeDocument } from "../application/connect-knowledge-document";
import { prismaKnowledgeConnection } from "../infrastructure/prisma-knowledge-connection";
import { createVedomoClient, VedomoClientError } from "../infrastructure/vedomo-client";

export async function connectMaxKnowledgeDocument(identity: MaxLearnerIdentity, courseId: string, documentId: string) {
  if (courseId !== process.env.MAX_VEDOMO_COURSE_ID) return "NOT_FOUND" as const;
  const connect = createConnectKnowledgeDocument(
    prismaKnowledgeConnection,
    createVedomoClient(process.env.MAX_VEDOMO_ORIGIN ?? "", process.env.MAX_VEDOMO_SERVICE_TOKEN ?? ""),
  );
  try {
    return await connect(identity, courseId, documentId);
  } catch (error) {
    if (error instanceof VedomoClientError && error.status === 409) return "CONFLICT" as const;
    throw error;
  }
}
