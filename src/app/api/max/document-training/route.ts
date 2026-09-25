import { createMaxSessionCodec } from "@/modules/max/infrastructure/learner-session";
import { recordMaxDocumentTraining } from "@/modules/max/server/document-training";

export const runtime = "nodejs";

const headers = { "Cache-Control": "no-store", "Vary": "Authorization" };

async function readBody(request: Request): Promise<unknown> {
  if (!request.body) throw new SyntaxError("Missing body");
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 2048) {
        await reader.cancel();
        throw new SyntaxError("Body too large");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

export async function POST(request: Request) {
  const botToken = process.env.MAX_BOT_TOKEN;
  const authorization = request.headers.get("authorization") ?? "";
  const identity = botToken && authorization.startsWith("Bearer ")
    ? createMaxSessionCodec(botToken).verify(authorization.slice(7)) : null;
  if (!identity) return Response.json({ error: "SESSION_EXPIRED" }, { status: 401, headers });
  if (request.headers.get("content-type")?.split(";")[0].trim() !== "application/json") {
    return Response.json({ error: "JSON_REQUIRED" }, { status: 415, headers });
  }
  try {
    const body = await readBody(request);
    if (!body || typeof body !== "object" || !("courseId" in body) || !("documentId" in body) ||
        !("action" in body) || typeof body.courseId !== "string" || typeof body.documentId !== "string" ||
        !/^[A-Za-z0-9_-]{1,128}$/.test(body.courseId) ||
        !/^[A-Za-z0-9_-]{1,128}$/.test(body.documentId) ||
        (body.action !== "view" && body.action !== "answer") ||
        (body.action === "answer" && (!("answerIndex" in body) ||
          !Number.isInteger(body.answerIndex) || (body.answerIndex as number) < 0 ||
          (body.answerIndex as number) > 2))) {
      return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
    }
    const result = await recordMaxDocumentTraining(
      identity,
      body.courseId,
      body.documentId,
      body.action,
      body.action === "answer" && "answerIndex" in body ? body.answerIndex as number : undefined,
    );
    const status = result.status === "SESSION_REVOKED" ? 401
      : result.status === "FORBIDDEN" || result.status === "NOT_ASSIGNED" ? 403
      : result.status === "NOT_FOUND" ? 404 : 200;
    return Response.json(result, { status, headers });
  } catch (error) {
    return Response.json(
      { error: error instanceof SyntaxError ? "INVALID_REQUEST" : "TEMPORARILY_UNAVAILABLE" },
      { status: error instanceof SyntaxError ? 400 : 503, headers },
    );
  }
}
