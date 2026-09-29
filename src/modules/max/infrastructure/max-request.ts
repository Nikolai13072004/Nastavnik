import { createMaxSessionCodec } from "./learner-session";

export const maxPrivateHeaders = {
  "Cache-Control": "no-store",
  Vary: "Authorization",
};

export function authenticateMaxRequest(request: Request) {
  const token = process.env.MAX_BOT_TOKEN;
  const authorization = request.headers.get("authorization") ?? "";
  return token && authorization.startsWith("Bearer ")
    ? createMaxSessionCodec(token).verify(authorization.slice(7))
    : null;
}

export async function readMaxJson(
  request: Request,
  limit = 2048,
): Promise<Record<string, unknown> | null> {
  if (
    request.headers.get("content-type")?.split(";")[0].trim() !==
    "application/json"
  )
    return null;
  const reader = request.body?.getReader();
  if (!reader) return null;
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > limit) {
        await reader.cancel();
        return null;
      }
      chunks.push(value);
    }
    const body: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    return body && typeof body === "object" && !Array.isArray(body)
      ? (body as Record<string, unknown>)
      : null;
  } catch (error) {
    if (error instanceof SyntaxError) return null;
    throw error;
  } finally {
    reader.releaseLock();
  }
}

export function isMaxId(value: unknown): value is string {
  return typeof value === "string" && /^[\w-]{1,128}$/.test(value);
}
