CREATE TABLE "MaxAccountLink" (
    "maxUserId" TEXT NOT NULL,
    "userId" TEXT NOT NULL,
    "organizationId" TEXT NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "MaxAccountLink_pkey" PRIMARY KEY ("maxUserId")
);

CREATE TABLE "MaxLinkInvite" (
    "userId" TEXT NOT NULL,
    "tokenHash" TEXT NOT NULL,
    "organizationId" TEXT NOT NULL,
    "expiresAt" TIMESTAMP(3) NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "MaxLinkInvite_pkey" PRIMARY KEY ("userId")
);

CREATE UNIQUE INDEX "MaxAccountLink_userId_key" ON "MaxAccountLink"("userId");
CREATE UNIQUE INDEX "MaxLinkInvite_tokenHash_key" ON "MaxLinkInvite"("tokenHash");
ALTER TABLE "MaxAccountLink" ADD CONSTRAINT "MaxAccountLink_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "MaxLinkInvite" ADD CONSTRAINT "MaxLinkInvite_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;
