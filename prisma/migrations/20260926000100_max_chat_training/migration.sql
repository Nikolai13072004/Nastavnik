ALTER TABLE "MaxBotDelivery" ADD COLUMN "chatInputCiphertext" TEXT;
ALTER TABLE "MaxBotDelivery" ADD COLUMN "chatInputExpiresAt" TIMESTAMP(3);
CREATE INDEX "MaxBotDelivery_botUsername_maxUserId_createdAt_idx"
  ON "MaxBotDelivery"("botUsername", "maxUserId", "createdAt");

CREATE TABLE "MaxChatSession" (
  "botUsername" TEXT NOT NULL,
  "maxUserId" TEXT NOT NULL,
  "userId" TEXT NOT NULL,
  "organizationId" TEXT NOT NULL,
  "linkedAt" TIMESTAMP(3) NOT NULL,
  "stateJson" TEXT NOT NULL,
  "expiresAt" TIMESTAMP(3) NOT NULL,
  "updatedAt" TIMESTAMP(3) NOT NULL,
  CONSTRAINT "MaxChatSession_pkey" PRIMARY KEY ("botUsername", "maxUserId"),
  CONSTRAINT "MaxChatSession_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX "MaxChatSession_expiresAt_idx" ON "MaxChatSession"("expiresAt");
