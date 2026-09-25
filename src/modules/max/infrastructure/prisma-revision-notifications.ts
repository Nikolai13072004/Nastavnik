import { createHash } from "node:crypto";
import type { Prisma } from "@prisma/client";

export async function enqueueMaxRevisionNotifications(
  client: Prisma.TransactionClient,
  organizationId: string,
  documentId: string,
  userIds: string[],
) {
  const botUsername = process.env.MAX_BOT_USERNAME;
  if (!botUsername || userIds.length === 0) return;

  const links = await client.maxAccountLink.findMany({
    where: { organizationId, userId: { in: userIds } },
    select: { maxUserId: true },
  });
  if (links.length === 0) return;
  await client.maxBotDelivery.createMany({
    data: links.map(({ maxUserId }) => ({
      eventKey: createHash("sha256")
        .update(JSON.stringify([botUsername, "revision", documentId, maxUserId]))
        .digest("hex"),
      botUsername,
      maxUserId,
      kind: "DOCUMENT_REVISION",
      documentId,
    })),
    skipDuplicates: true,
  });
}
