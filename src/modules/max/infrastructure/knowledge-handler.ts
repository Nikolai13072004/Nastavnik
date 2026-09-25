import type { MaxLearnerIdentity } from "../application/list-courses";
import type { KnowledgeAnswer } from "../application/verify-knowledge-answer";
import { createMaxSessionCodec } from "./learner-session";

type AskKnowledge = (
  identity: MaxLearnerIdentity,
  courseId: string,
  question: string,
) => Promise<KnowledgeAnswer | null>;

const headers = { "Cache-Control": "no-store", "Vary": "Authorization" };

async function readQuestion(request: Request): Promise<{ courseId: string; question: string } | null> {
  if (!request.body) return null;
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 2048) {
        await reader.cancel();
        return null;
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const body: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  if (!body || typeof body !== "object" || !("courseId" in body) || !("question" in body) ||
      typeof body.courseId !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(body.courseId) ||
      typeof body.question !== "string" || body.question.trim().length < 3 || body.question.length > 500) {
    return null;
  }
  return { courseId: body.courseId, question: body.question.trim() };
}

export async function handleMaxKnowledgeRequest(
  request: Request,
  botToken: string | undefined,
  enabled: boolean,
  ask: AskKnowledge,
): Promise<Response> {
  if (!enabled) return Response.json({ error: "NOT_FOUND" }, { status: 404, headers });
  const authorization = request.headers.get("authorization") ?? "";
  const identity = botToken && authorization.startsWith("Bearer ")
    ? createMaxSessionCodec(botToken).verify(authorization.slice(7)) : null;
  if (!identity) return Response.json({ error: "SESSION_EXPIRED" }, { status: 401, headers });
  if (request.headers.get("content-type")?.split(";")[0].trim() !== "application/json") {
    return Response.json({ error: "JSON_REQUIRED" }, { status: 415, headers });
  }

  try {
    const input = await readQuestion(request);
    if (!input) return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
    const answer = await ask(identity, input.courseId, input.question);
    if (!answer) return Response.json({ error: "KNOWLEDGE_UNAVAILABLE" }, { status: 403, headers });
    return Response.json(answer, { headers });
  } catch (error) {
    const invalid = error instanceof SyntaxError;
    return Response.json(
      { error: invalid ? "INVALID_REQUEST" : "TEMPORARILY_UNAVAILABLE" },
      { status: invalid ? 400 : 503, headers },
    );
  }
}
