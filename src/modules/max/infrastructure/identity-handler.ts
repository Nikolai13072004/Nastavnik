import { verifyMaxInitData } from "./verify-init-data";
import { MaxLinkError, type LinkedEmployee } from "../application/link-account";

const MAX_BODY_BYTES = 20_480;

function json(body: object, status = 200) {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
}

/** Bounded body reading also covers chunked requests without Content-Length. */
async function readBody(request: Request): Promise<string | null> {
  if (!request.body) return "";
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BODY_BYTES) {
        await reader.cancel();
        return null;
      }
      chunks.push(value);
    }
    return Buffer.concat(chunks).toString("utf8");
  } finally {
    reader.releaseLock();
  }
}

type AccountLinking = {
  accept(token: string, maxUserId: string): Promise<void>;
  findEmployee(maxUserId: string): Promise<LinkedEmployee | null>;
  issueSession?(maxUserId: string): Promise<{ token: string; expiresAt: string } | null>;
  canViewReports?(maxUserId: string): Promise<boolean>;
};

export async function handleMaxIdentity(request: Request, botToken: string | undefined, linking?: AccountLinking) {
  // No cookies are issued: a valid launch is not an authenticated LMS session.
  if (!botToken) return json({ error: "MAX_NOT_CONFIGURED" }, 503);
  if (request.headers.get("content-type")?.split(";")[0].trim() !== "application/json") {
    return json({ error: "JSON_REQUIRED" }, 415);
  }
  try {
    const body = await readBody(request);
    if (body === null) return json({ error: "REQUEST_TOO_LARGE" }, 413);
    const payload: unknown = JSON.parse(body);
    if (!payload || typeof payload !== "object" || !("initData" in payload) || typeof payload.initData !== "string") {
      return json({ error: "INVALID_REQUEST" }, 400);
    }
    const identity = verifyMaxInitData(payload.initData, botToken);
    if (!identity) return json({ error: "INVALID_LAUNCH" }, 401);
    if ("inviteToken" in payload) {
      if (!linking || typeof payload.inviteToken !== "string") return json({ error: "INVALID_REQUEST" }, 400);
      await linking.accept(payload.inviteToken, identity.id);
    }
    if (linking) {
      const employee = await linking.findEmployee(identity.id);
      const session = employee && linking.issueSession ? await linking.issueSession(identity.id) : null;
      const managerAccess = Boolean(session && linking.canViewReports && await linking.canViewReports(identity.id));
      return json({ status: "identity_verified", firstName: identity.firstName, employeeAccess: Boolean(session), managerAccess, employee, session });
    }
    return json({ status: "identity_verified", firstName: identity.firstName, employeeAccess: false });
  } catch (error) {
    if (error instanceof MaxLinkError) return json({ error: error.code }, 409);
    if (error instanceof SyntaxError) return json({ error: "INVALID_REQUEST" }, 400);
    return json({ error: "TEMPORARILY_UNAVAILABLE" }, 503);
  }
}
