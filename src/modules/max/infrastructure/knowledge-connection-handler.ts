import type { KnowledgeConnectionResult } from "../application/connect-knowledge-document";
import type { MaxLearnerIdentity } from "../application/list-courses";
import { createMaxSessionCodec } from "./learner-session";

const headers = { "Cache-Control": "no-store", "Vary": "Authorization" };

async function readInput(request: Request) {
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
  if (!body || typeof body !== "object" || Array.isArray(body) ||
      Object.keys(body).some((key) => key !== "courseId" && key !== "documentId") ||
      !("courseId" in body) || !("documentId" in body) ||
      typeof body.courseId !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(body.courseId) ||
      typeof body.documentId !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(body.documentId)) return null;
  return { courseId: body.courseId, documentId: body.documentId };
}

export async function handleKnowledgeConnection(
  request: Request,
  botToken: string | undefined,
  enabled: boolean,
  connect: (identity: MaxLearnerIdentity, courseId: string, documentId: string) => Promise<KnowledgeConnectionResult>,
) {
  if (!enabled) return Response.json({ error: "NOT_FOUND" }, { status: 404, headers });
  const authorization = request.headers.get("authorization") ?? "";
  const identity = botToken && authorization.startsWith("Bearer ")
    ? createMaxSessionCodec(botToken).verify(authorization.slice(7)) : null;
  if (!identity) return Response.json({ error: "SESSION_EXPIRED" }, { status: 401, headers });
  if (request.headers.get("content-type")?.split(";")[0].trim() !== "application/json") {
    return Response.json({ error: "JSON_REQUIRED" }, { status: 415, headers });
  }
  try {
    const input = await readInput(request);
    if (!input) return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
    const result = await connect(identity, input.courseId, input.documentId);
    const status = result === "APPROVED" ? 200 : result === "FORBIDDEN" ? 403 : result === "NOT_FOUND" ? 404
      : result === "UNSUPPORTED_FORMAT" ? 400 : 409;
    return Response.json({ status: result }, { status, headers });
  } catch (error) {
    const status = error instanceof SyntaxError ? 400 : 503;
    return Response.json({ error: status === 400 ? "INVALID_REQUEST" : "TEMPORARILY_UNAVAILABLE" }, { status, headers });
  }
}
