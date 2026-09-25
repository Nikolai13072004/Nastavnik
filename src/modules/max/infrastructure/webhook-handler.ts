import { createHash, timingSafeEqual } from "node:crypto";
import { parseBotStart, type BotDeliveryRepository } from "../application/bot-delivery";

type WebhookConfig = { secret?: string; botUsername?: string };

function json(status: number, error?: string) {
  return Response.json(error ? { error } : { ok: true }, {
    status, headers: { "Cache-Control": "no-store" },
  });
}

async function readEvent(request: Request): Promise<unknown> {
  if (!request.body) throw new SyntaxError();
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  let timedOut = false;
  const timeout = setTimeout(() => {
    timedOut = true;
    void reader.cancel().catch(() => undefined);
  }, 5000);
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 20_480) throw new RangeError();
      chunks.push(value);
    }
    if (timedOut) throw new SyntaxError();
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } finally {
    clearTimeout(timeout);
    void reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export async function handleMaxWebhook(
  request: Request,
  config: WebhookConfig,
  repository: Pick<BotDeliveryRepository, "enqueue">,
  now = Date.now(),
) {
  const { secret, botUsername } = config;
  if (!secret || !/^[a-zA-Z0-9_-]{32,256}$/.test(secret)
    || !botUsername || !/^[a-zA-Z0-9_]+$/.test(botUsername)) return json(503, "MAX_NOT_CONFIGURED");
  const supplied = request.headers.get("x-max-bot-api-secret") ?? "";
  const hash = (value: string) => createHash("sha256").update(value).digest();
  if (!timingSafeEqual(hash(supplied), hash(secret))) return json(401, "UNAUTHORIZED");
  if (request.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "application/json") {
    return json(415, "JSON_REQUIRED");
  }
  try {
    const event = parseBotStart(await readEvent(request), now);
    if (event === null) return json(400, "INVALID_EVENT");
    if (event !== "ignored") await repository.enqueue(botUsername, event);
    // Acknowledge only after durable persistence; no external send within the HTTP request.
    return json(200);
  } catch (error) {
    if (error instanceof RangeError) return json(413, "REQUEST_TOO_LARGE");
    if (error instanceof SyntaxError) return json(400, "INVALID_EVENT");
    return json(503, "TEMPORARILY_UNAVAILABLE");
  }
}
