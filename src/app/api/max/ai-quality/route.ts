import {
  authenticateMaxRequest,
  isMaxId,
  maxPrivateHeaders as headers,
  readMaxJson,
} from "@/modules/max/infrastructure/max-request";
import {
  getMaxAiQuality,
  reviewMaxAiFeedback,
} from "@/modules/max/server/ai-quality";

export const runtime = "nodejs";

export async function GET(request: Request) {
  const identity = authenticateMaxRequest(request);
  if (!identity)
    return Response.json(
      { error: "SESSION_EXPIRED" },
      { status: 401, headers },
    );
  const courseId = new URL(request.url).searchParams.get("courseId");
  if (!isMaxId(courseId))
    return Response.json(
      { error: "INVALID_REQUEST" },
      { status: 400, headers },
    );
  try {
    const result = await getMaxAiQuality(identity, courseId);
    return Response.json(result, {
      status:
        "error" in result ? (result.error === "FORBIDDEN" ? 403 : 404) : 200,
      headers,
    });
  } catch {
    return Response.json(
      { error: "TEMPORARILY_UNAVAILABLE" },
      { status: 503, headers },
    );
  }
}

export async function POST(request: Request) {
  const identity = authenticateMaxRequest(request);
  if (!identity)
    return Response.json(
      { error: "SESSION_EXPIRED" },
      { status: 401, headers },
    );
  try {
    const body = await readMaxJson(request, 4096);
    if (
      !body ||
      !isMaxId(body.courseId) ||
      !isMaxId(body.eventId) ||
      !["CORRECT", "INCORRECT", "INCONCLUSIVE"].includes(
        String(body.verdict),
      ) ||
      typeof body.comment !== "string" ||
      body.comment.length > 1000
    ) {
      return Response.json(
        { error: "INVALID_REQUEST" },
        { status: 400, headers },
      );
    }
    const result = await reviewMaxAiFeedback(
      identity,
      body.courseId,
      body.eventId,
      body.verdict as "CORRECT" | "INCORRECT" | "INCONCLUSIVE",
      body.comment,
    );
    return Response.json(result, {
      status:
        "error" in result ? (result.error === "FORBIDDEN" ? 403 : 409) : 200,
      headers,
    });
  } catch {
    return Response.json(
      { error: "TEMPORARILY_UNAVAILABLE" },
      { status: 503, headers },
    );
  }
}
