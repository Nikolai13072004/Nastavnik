import { createMaxSessionCodec } from "@/modules/max/infrastructure/learner-session";
import { getMaxManagerReport } from "@/modules/max/server/manager-report";
import { assignMaxCourse } from "@/modules/enrollment/server/assign-max-course";

export const runtime = "nodejs";

const headers = { "Cache-Control": "no-store", "Vary": "Authorization" };

function authenticate(request: Request) {
  const botToken = process.env.MAX_BOT_TOKEN;
  const authorization = request.headers.get("authorization") ?? "";
  return botToken && authorization.startsWith("Bearer ")
    ? createMaxSessionCodec(botToken).verify(authorization.slice(7)) : null;
}

export async function GET(request: Request) {
  const identity = authenticate(request);
  if (!identity) return Response.json({ error: "SESSION_EXPIRED" }, { status: 401, headers });

  const courseId = new URL(request.url).searchParams.get("courseId");
  if (courseId !== null && (!courseId || courseId.length > 128)) {
    return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
  }
  try {
    const result = await getMaxManagerReport(identity, courseId);
    if ("error" in result) {
      return Response.json(result, { status: result.error === "NOT_FOUND" ? 404 : 403, headers });
    }
    return Response.json(result, { headers });
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
    const reader = request.body?.getReader();
    if (!reader) return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
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
    if (!body || typeof body !== "object" || !
        ("courseId" in body) || !("learnerId" in body) ||
        typeof body.courseId !== "string" || typeof body.learnerId !== "string" ||
        !/^[\w-]{1,128}$/.test(body.courseId) || !/^[\w-]{1,128}$/.test(body.learnerId)) {
      return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
    }

    const result = await assignMaxCourse(identity, body.courseId, body.learnerId);
    if (result.status === "FORBIDDEN") return Response.json(result, { status: 403, headers });
    if (result.status === "NOT_FOUND") return Response.json(result, { status: 404, headers });
    return Response.json(result, { headers });
  } catch (error) {
    if (error instanceof SyntaxError) return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
    return Response.json({ error: "TEMPORARILY_UNAVAILABLE" }, { status: 503, headers });
  }
}
