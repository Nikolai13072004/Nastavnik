import { createHash } from "node:crypto";
import type { KnowledgeAnswer } from "../application/verify-knowledge-answer";

export function knowledgeFeedbackHash(
  question: string,
  result: KnowledgeAnswer,
) {
  return createHash("sha256")
    .update(
      JSON.stringify({
        question,
        answer: result.answer,
        refused: result.refused,
        sources: result.sources.map((source) => ({
          documentId: source.documentId,
          documentHash: source.documentHash,
          title: source.title,
          section: source.section,
          pageStart: source.pageStart,
          pageEnd: source.pageEnd,
          snippet: source.snippet,
          courseDocumentId: source.courseDocumentId ?? null,
        })),
      }),
    )
    .digest("hex");
}
