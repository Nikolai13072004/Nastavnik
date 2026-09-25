CREATE TABLE "MaxBotDelivery" (
    "eventKey" TEXT NOT NULL,
    "botUsername" TEXT NOT NULL,
    "maxUserId" TEXT NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'PENDING',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "startedAt" TIMESTAMP(3),
    "finishedAt" TIMESTAMP(3),
    "messageId" TEXT,
    "errorCode" TEXT,
    CONSTRAINT "MaxBotDelivery_pkey" PRIMARY KEY ("eventKey")
);
CREATE INDEX "MaxBotDelivery_botUsername_status_createdAt_idx" ON "MaxBotDelivery"("botUsername", "status", "createdAt");
CREATE INDEX "MaxBotDelivery_botUsername_finishedAt_idx" ON "MaxBotDelivery"("botUsername", "finishedAt");
