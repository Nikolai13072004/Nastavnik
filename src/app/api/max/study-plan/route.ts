import {
  authenticateMaxRequest,
  isMaxId,
  maxPrivateHeaders as headers,
  readMaxJson,
} from "@/modules/max/infrastructure/max-request";
import { saveMaxStudyPlan } from "@/modules/enrollment/server/max-study-plan";

export const runtime = "nodejs";

export async function POST(request: Request) {
  const identity = authenticateMaxRequest(request);
  if (!identity)
    return Response.json(
      { error: "SESSION_EXPIRED" },
      { status: 401, headers },
    );
  try {
    const body = await readMaxJson(request);
    const dueAt =
      body?.dueAt === null
        ? null
        : typeof body?.dueAt === "string"
          ? new Date(body.dueAt)
          : undefined;
    if (
      !body ||
      !isMaxId(body.courseId) ||
      !isMaxId(body.learnerId) ||
      typeof body.remindersEnabled !== "boolean" ||
      dueAt === undefined ||
      (dueAt &&
        (!Number.isFinite(dueAt.getTime()) ||
          dueAt <= new Date() ||
          dueAt.getTime() > Date.now() + 366 * 86400000))
    ) {
      return Response.json(
        { error: "INVALID_REQUEST" },
        { status: 400, headers },
      );
    }
    const result = await saveMaxStudyPlan(identity, {
      courseId: body.courseId,
      learnerId: body.learnerId,
      dueAt,
      remindersEnabled: body.remindersEnabled,
    });
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
