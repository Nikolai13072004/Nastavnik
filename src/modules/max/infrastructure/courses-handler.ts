import type { MaxCourse, MaxLearnerIdentity } from "../application/list-courses";
import { createMaxSessionCodec } from "./learner-session";

export async function handleMaxCourses(
  request: Request,
  botToken: string | undefined,
  listCourses: (identity: MaxLearnerIdentity) => Promise<MaxCourse[] | null>,
) {
  const headers = { "Cache-Control": "no-store", "Vary": "Authorization" };
  if (!botToken) return Response.json({ error: "MAX_NOT_CONFIGURED" }, { status: 503, headers });
  const authorization = request.headers.get("authorization") ?? "";
  const identity = authorization.startsWith("Bearer ")
    ? createMaxSessionCodec(botToken).verify(authorization.slice(7)) : null;
  if (!identity) return Response.json({ error: "SESSION_EXPIRED" }, { status: 401, headers });
  try {
    const courses = await listCourses(identity);
    if (!courses) return Response.json({ error: "ACCESS_REVOKED" }, { status: 401, headers });
    return Response.json({ courses }, { headers });
  } catch {
    return Response.json({ error: "TEMPORARILY_UNAVAILABLE" }, { status: 503, headers });
  }
}
