import { createMaxSessionCodec } from "@/modules/max/infrastructure/learner-session";
import { completeMaxMaterial, getMaxCourse } from "@/modules/max/server/course-learning";

export const runtime = "nodejs";

const headers = { "Cache-Control": "no-store", "Vary": "Authorization" };

function authenticate(request: Request) {
  const botToken = process.env.MAX_BOT_TOKEN;
  if (!botToken) return null;
  const authorization = request.headers.get("authorization") ?? "";
  if (!authorization.startsWith("Bearer ")) return null;
  return createMaxSessionCodec(botToken).verify(authorization.slice(7));
}

function errorResponse(error: string) {
  const status = error === "SESSION_REVOKED" ? 401 : error === "FORBIDDEN" ? 403 : error === "MATERIAL_NOT_FOUND" ? 404 : 503;
  return Response.json({ error }, { status, headers });
}

export async function GET(request: Request) {
  const identity = authenticate(request);
  if (!identity) return Response.json({ error: "SESSION_EXPIRED" }, { status: 401, headers });
  const courseId = new URL(request.url).searchParams.get("courseId");
  if (!courseId || courseId.length > 128) return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
  try {
    const result = await getMaxCourse(identity, courseId);
    return result.error ? errorResponse(result.error) : Response.json(result, { headers });
  } catch {
    return Response.json({ error: "TEMPORARILY_UNAVAILABLE" }, { status: 503, headers });
  }
}

export async function POST(request: Request) {
  const identity = authenticate(request);
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
    if (!body || typeof body !== "object" || !("courseId" in body) || !("materialId" in body) ||
        typeof body.courseId !== "string" || typeof body.materialId !== "string" ||
        body.courseId.length > 128 || body.materialId.length > 128) {
      return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
    }
    const result = await completeMaxMaterial(identity, body.courseId, body.materialId);
    return result.error ? errorResponse(result.error) : Response.json(result, { headers });
  } catch (error) {
    if (error instanceof SyntaxError) return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
    return Response.json({ error: "TEMPORARILY_UNAVAILABLE" }, { status: 503, headers });
  }
}
