import { AssessmentApplicationError } from "@/modules/assessment/application/errors";
import { createMaxSessionCodec } from "@/modules/max/infrastructure/learner-session";
import { startMaxQuiz, submitMaxQuiz } from "@/modules/max/server/quiz-learning";

export const runtime = "nodejs";

const headers = { "Cache-Control": "no-store", "Vary": "Authorization" };

function respondError(error: string, status: number) {
  return Response.json({ error }, { status, headers });
}

async function readRequestBody(request: Request) {
  if (!request.body) return null;
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 4096) {
        await reader.cancel();
        return null;
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8")) as unknown;
}

export async function POST(request: Request) {
  const botToken = process.env.MAX_BOT_TOKEN;
  if (!botToken) return respondError("MAX_NOT_CONFIGURED", 503);
  const authorization = request.headers.get("authorization") ?? "";
  const identity = authorization.startsWith("Bearer ")
    ? createMaxSessionCodec(botToken).verify(authorization.slice(7)) : null;
  if (!identity) return respondError("SESSION_EXPIRED", 401);
  if (request.headers.get("content-type")?.split(";")[0].trim() !== "application/json") {
    return respondError("JSON_REQUIRED", 415);
  }

  try {
    const body = await readRequestBody(request);
    if (!body || typeof body !== "object" || !("action" in body) ||
        !("courseId" in body) || !("quizId" in body) ||
        typeof body.courseId !== "string" || !body.courseId || body.courseId.length > 128 ||
        typeof body.quizId !== "string" || !body.quizId || body.quizId.length > 128) {
      return respondError("INVALID_REQUEST", 400);
    }

    if (body.action === "start") {
      const result = await startMaxQuiz(identity, body.courseId, body.quizId);
      return result.error ? respondError(result.error, result.error === "SESSION_REVOKED" ? 401 : 403)
        : Response.json(result, { headers });
    }
    if (body.action === "submit" && "attemptId" in body && "answers" in body &&
        typeof body.attemptId === "string" && body.attemptId.length > 0 && body.attemptId.length <= 128) {
      const result = await submitMaxQuiz(identity, body.courseId, body.quizId, body.attemptId, body.answers);
      return result.error ? respondError(result.error, result.error === "SESSION_REVOKED" ? 401 : 409)
        : Response.json(result, { headers });
    }
    return respondError("INVALID_REQUEST", 400);
  } catch (error) {
    if (error instanceof AssessmentApplicationError) return respondError(error.code, 409);
    if (error instanceof SyntaxError) return respondError("INVALID_REQUEST", 400);
    return respondError("TEMPORARILY_UNAVAILABLE", 503);
  }
}
