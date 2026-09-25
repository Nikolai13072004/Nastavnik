import { findCoursePassage } from "@/modules/max/application/find-course-passage";
import { createMaxSessionCodec } from "@/modules/max/infrastructure/learner-session";
import { loadMaxCourseSnapshot } from "@/modules/max/server/course-learning";

export const runtime = "nodejs";

const headers = { "Cache-Control": "no-store", "Vary": "Authorization" };

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
    if (!request.body) return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
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
          return Response.json({ error: "REQUEST_TOO_LARGE" }, { status: 413, headers });
        }
        chunks.push(value);
      }
    } finally {
      reader.releaseLock();
    }
    const body: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    if (!body || typeof body !== "object" || !("courseId" in body) || !("question" in body) ||
        typeof body.courseId !== "string" || typeof body.question !== "string" ||
        body.courseId.length > 128 || body.question.trim().length < 3 || body.question.length > 500) {
      return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
    }

    const loaded = await loadMaxCourseSnapshot(identity, body.courseId);
    if (loaded.kind === "error") {
      const status = loaded.error === "SESSION_REVOKED" ? 401 : loaded.error === "FORBIDDEN" ? 403 : 503;
      return Response.json({ error: loaded.error }, { status, headers });
    }
    const materials = loaded.snapshot.items.filter((item) => item.type === "TEXT")
      .map(({ id, title, content }) => ({ id, title, content }));
    const passage = findCoursePassage(body.question, materials);
    return Response.json({ passage }, { headers });
  } catch (error) {
    const code = error instanceof SyntaxError ? "INVALID_REQUEST" : "TEMPORARILY_UNAVAILABLE";
    return Response.json({ error: code }, { status: code === "INVALID_REQUEST" ? 400 : 503, headers });
  }
}
