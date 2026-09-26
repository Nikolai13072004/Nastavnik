import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import { PrismaClient } from "@prisma/client";

async function main() {
  const secret = process.env.MAX_WEBHOOK_SECRET;
  const botUsername = process.env.MAX_BOT_USERNAME;
  if (!secret || !botUsername || !process.argv.includes("--send-pilot-help")) {
    throw new Error("Explicit pilot help delivery is required");
  }
  const endpoint = new URL("/api/max/webhook", process.env.APP_BASE_URL);
  if (endpoint.protocol !== "https:") throw new Error("HTTPS required");
  const db = new PrismaClient();
  try {
    const link = await db.maxAccountLink.findFirst({
      where: { organizationId: "max-pilot-demo-org", user: { status: "ACTIVE" } },
      select: { maxUserId: true },
    });
    if (!link) throw new Error("Pilot account missing");
    const userId = Number(link.maxUserId);
    assert.ok(Number.isSafeInteger(userId) && userId > 0);
    const timestamp = Date.now();
    const eventKey = createHash("sha256")
      .update(JSON.stringify([botUsername, "help", userId, Math.floor(timestamp / 30_000)]))
      .digest("hex");
    assert.equal(await db.maxBotDelivery.findUnique({ where: { eventKey } }), null,
      "Help already queued in this time window; do not repeat");
    const body = JSON.stringify({
      update_type: "message_created", timestamp,
      message: {
        sender: { user_id: userId, is_bot: false },
        recipient: { chat_type: "dialog" },
        body: { text: "Помощь" },
      },
    });
    for (let receipt = 0; receipt < 2; receipt++) {
      const response: Response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Max-Bot-Api-Secret": secret },
        body,
        signal: AbortSignal.timeout(15_000),
      });
      assert.equal(response.status, 200);
    }
    assert.equal(await db.maxBotDelivery.count({ where: { eventKey, kind: "HELP" } }), 1);
    for (let check = 0; check < 20; check++) {
      const job = await db.maxBotDelivery.findUnique({ where: { eventKey } });
      if (job?.status === "SENT") {
        console.log("pilot help: two synthetic receipts, one persisted job, one confirmed MAX delivery");
        return;
      }
      if (job?.status === "UNCERTAIN") throw new Error("Delivery uncertain; do not retry");
      await delay(1000);
    }
    throw new Error("Help delivery not confirmed; inspect queue without retrying");
  } finally {
    await db.$disconnect();
  }
}

main().catch(() => {
  console.error("Pilot help check failed. Inspect delivery states; no automatic retry and no secrets printed.");
  process.exitCode = 1;
});
