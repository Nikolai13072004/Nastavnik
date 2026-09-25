import "server-only";

import { createAskAuthorizedKnowledgeQuestion } from "../application/ask-authorized-knowledge-question";
import type { MaxLearnerIdentity } from "../application/list-courses";
import { createVedomoClient } from "../infrastructure/vedomo-client";
import { listAuthorizedMaxKnowledgeDocuments } from "./authorized-knowledge-documents";

export async function askMaxKnowledge(
  identity: MaxLearnerIdentity,
  courseId: string,
  question: string,
) {
  const origin = process.env.MAX_VEDOMO_ORIGIN;
  const token = process.env.MAX_VEDOMO_SERVICE_TOKEN;
  if (!origin || !token) throw new Error("Vedomo is not configured");

  const ask = createAskAuthorizedKnowledgeQuestion(
    { list: listAuthorizedMaxKnowledgeDocuments },
    createVedomoClient(origin, token),
  );
  return ask(identity, courseId, question);
}
