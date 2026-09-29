import "server-only";

import { createAskAuthorizedKnowledgeQuestion } from "../application/ask-authorized-knowledge-question";
import type { MaxLearnerIdentity } from "../application/list-courses";
import { createVedomoClient } from "../infrastructure/vedomo-client";
import { listAuthorizedMaxKnowledgeDocuments } from "./authorized-knowledge-documents";
import { recordMaxAiEvent } from "./ai-quality";

export async function askMaxKnowledge(
  identity: MaxLearnerIdentity,
  courseId: string,
  question: string,
) {
  const origin = process.env.MAX_VEDOMO_ORIGIN;
  const token = process.env.MAX_VEDOMO_SERVICE_TOKEN;
  if (!origin || !token) throw new Error("Knowledge service is not configured");

  const ask = createAskAuthorizedKnowledgeQuestion(
    { list: listAuthorizedMaxKnowledgeDocuments },
    createVedomoClient(origin, token),
  );
  const started = performance.now();
  try {
    const result = await ask(identity, courseId, question);
    if (!result) return null;
    // Optional telemetry must not turn a successfully generated answer into a failure.
    try {
      const eventId = await recordMaxAiEvent(identity, courseId, performance.now() - started, question, result);
      return { ...result, eventId };
    } catch {
      console.warn("MAX AI metrics unavailable; no question text is logged.");
      return result;
    }
  } catch (error) {
    if (await listAuthorizedMaxKnowledgeDocuments(identity, courseId).catch(() => null)) {
      await recordMaxAiEvent(identity, courseId, performance.now() - started, question, null).catch(() => undefined);
    }
    throw error;
  }
}
