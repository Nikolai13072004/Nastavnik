import { createMaxSessionCodec } from "@/modules/max/infrastructure/learner-session";
import { createMaxEmployee, issueMaxEmployeeCode } from "@/modules/user/server/create-max-employee";

export const runtime = "nodejs";

const headers = { "Cache-Control": "no-store", "Vary": "Authorization" };

function authenticate(request: Request) {
  const botToken = process.env.MAX_BOT_TOKEN;
  const authorization = request.headers.get("authorization") ?? "";
  return botToken && authorization.startsWith("Bearer ")
    ? createMaxSessionCodec(botToken).verify(authorization.slice(7)) : null;
}

async function readBody(request: Request): Promise<{ body: unknown } | { error: string; status: number }> {
  const reader = request.body?.getReader();
  if (!reader) return { error: "INVALID_REQUEST", status: 400 };
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 1024) {
        await reader.cancel();
        return { error: "REQUEST_TOO_LARGE", status: 413 };
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  return { body: JSON.parse(Buffer.concat(chunks).toString("utf8")) };
}

function checkRequest(request: Request) {
  const identity = authenticate(request);
  if (!identity) return Response.json({ error: "SESSION_EXPIRED" }, { status: 401, headers });
  if (request.headers.get("content-type")?.split(";")[0].trim() !== "application/json") {
    return Response.json({ error: "JSON_REQUIRED" }, { status: 415, headers });
  }
  return identity;
}

export async function POST(request: Request) {
  const identity = checkRequest(request);
  if (identity instanceof Response) return identity;

  try {
    const payload = await readBody(request);
    if ("error" in payload) return Response.json({ error: payload.error }, { status: payload.status, headers });
    const body = payload.body;
    if (!body || typeof body !== "object" ||
        !("firstName" in body) || !("lastName" in body) || !("email" in body) ||
        typeof body.firstName !== "string" || typeof body.lastName !== "string" || typeof body.email !== "string") {
      return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
    }

    const result = await createMaxEmployee(identity, {
      firstName: body.firstName,
      lastName: body.lastName,
      email: body.email,
    });
    if (result.status === "FORBIDDEN") return Response.json(result, { status: 403, headers });
    if (result.status === "INVALID_INPUT") return Response.json(result, { status: 400, headers });
    if (result.status === "EMAIL_EXISTS") return Response.json(result, { status: 409, headers });
    return Response.json(result, { status: 201, headers });
  } catch (error) {
    if (error instanceof SyntaxError) return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
    return Response.json({ error: "TEMPORARILY_UNAVAILABLE" }, { status: 503, headers });
  }
}

export async function PUT(request: Request) {
  const identity = checkRequest(request);
  if (identity instanceof Response) return identity;

  try {
    const payload = await readBody(request);
    if ("error" in payload) return Response.json({ error: payload.error }, { status: payload.status, headers });
    const body = payload.body;
    if (!body || typeof body !== "object" || !("userId" in body) || typeof body.userId !== "string") {
      return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
    }

    const result = await issueMaxEmployeeCode(identity, body.userId);
    if (result.status === "FORBIDDEN") return Response.json(result, { status: 403, headers });
    if (result.status === "INVALID_INPUT") return Response.json(result, { status: 400, headers });
    if (result.status === "NOT_FOUND") return Response.json(result, { status: 404, headers });
    return Response.json(result, { headers });
  } catch (error) {
    if (error instanceof SyntaxError) return Response.json({ error: "INVALID_REQUEST" }, { status: 400, headers });
    return Response.json({ error: "TEMPORARILY_UNAVAILABLE" }, { status: 503, headers });
  }
}
