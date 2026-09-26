import "server-only";
import { handleMaxWebhook } from "../infrastructure/webhook-handler";
import { prismaBotDeliveryRepository } from "../infrastructure/prisma-bot-delivery-repository";

export function receiveMaxEvent(request: Request) {
  return handleMaxWebhook(request, {
    secret: process.env.MAX_WEBHOOK_SECRET === process.env.MAX_BOT_TOKEN
      ? undefined
      : process.env.MAX_WEBHOOK_SECRET,
    botUsername: process.env.MAX_BOT_USERNAME,
    chatEnabled: process.env.MAX_CHAT_ENABLED === "true",
  }, prismaBotDeliveryRepository);
}
