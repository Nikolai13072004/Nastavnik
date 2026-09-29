import {
  authenticateMaxRequest,
  isMaxId,
  maxPrivateHeaders as headers,
  readMaxJson,
} from "@/modules/max/infrastructure/max-request";
import { submitMaxAiFeedback } from "@/modules/max/server/ai-quality";
import type { KnowledgeAnswer } from "@/modules/max/application/verify-knowledge-answer";

export const runtime = "nodejs";

export async function POST(request: Request) {
  const identity = authenticateMaxRequest(request);
  if (!identity)
    return Response.json(
      { error: "SESSION_EXPIRED" },
      { status: 401, headers },
    );
  try {
    const body = await readMaxJson(request, 64 * 1024);
    const result = body?.result as KnowledgeAnswer | undefined;
    if (
      !body ||
      body.consent !== true ||
      !isMaxId(body.eventId) ||
      !isMaxId(body.courseId) ||
      typeof body.question !== "string" ||
      body.question.length > 500 ||
      !["WRONG_ANSWER", "WRONG_SOURCE", "MISSING_ANSWER"].includes(
        String(body.reason),
      ) ||
      typeof body.comment !== "string" ||
      body.comment.length > 1000 ||
      !result ||
      typeof result.answer !== "string" ||
      result.answer.length > 20000 ||
      typeof result.refused !== "boolean" ||
      !Array.isArray(result.sources) ||
      result.sources.length > 20 ||
      !result.sources.every(
        (source) =>
          source &&
          typeof source === "object" &&
          typeof source.documentId === "string" &&
          typeof source.documentHash === "string" &&
          typeof source.title === "string" &&
          typeof source.section === "string" &&
          typeof source.snippet === "string" &&
          (source.pageStart === null || Number.isInteger(source.pageStart)) &&
          (source.pageEnd === null || Number.isInteger(source.pageEnd)) &&
          (source.courseDocumentId === undefined ||
            source.courseDocumentId === null ||
            isMaxId(source.courseDocumentId)),
      )
    ) {
      return Response.json(
        { error: "INVALID_REQUEST" },
        { status: 400, headers },
      );
    }
    const response = await submitMaxAiFeedback(identity, {
      courseId: body.courseId,
      eventId: body.eventId,
      question: body.question,
      result,
      reason: String(body.reason),
      comment: body.comment,
    });
    return Response.json(response, {
      status:
        "error" in response
          ? response.error === "FORBIDDEN"
            ? 403
            : 404
          : 200,
      headers,
    });
  } catch {
    return Response.json(
      { error: "TEMPORARILY_UNAVAILABLE" },
      { status: 503, headers },
    );
  }
}
