ALTER TABLE "MaxBotDelivery" ADD COLUMN "studyPlanId" TEXT;

CREATE TABLE "MaxStudyPlan" (
  "id" TEXT NOT NULL,
  "organizationId" TEXT NOT NULL,
  "courseId" TEXT NOT NULL,
  "userId" TEXT NOT NULL,
  "dueAt" TIMESTAMP(3),
  "remindersEnabled" BOOLEAN NOT NULL DEFAULT false,
  "updatedAt" TIMESTAMP(3) NOT NULL,
  CONSTRAINT "MaxStudyPlan_pkey" PRIMARY KEY ("id")
);
CREATE UNIQUE INDEX "MaxStudyPlan_courseId_userId_key" ON "MaxStudyPlan"("courseId", "userId");
CREATE INDEX "MaxStudyPlan_organizationId_remindersEnabled_dueAt_idx" ON "MaxStudyPlan"("organizationId", "remindersEnabled", "dueAt");
ALTER TABLE "MaxStudyPlan" ADD CONSTRAINT "MaxStudyPlan_courseId_fkey" FOREIGN KEY ("courseId") REFERENCES "Course"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "MaxStudyPlan" ADD CONSTRAINT "MaxStudyPlan_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "MaxStudyPlan" ADD CONSTRAINT "MaxStudyPlan_organizationId_fkey" FOREIGN KEY ("organizationId") REFERENCES "Organization"("id") ON DELETE CASCADE ON UPDATE CASCADE;

CREATE TABLE "MaxAiEvent" (
  "id" TEXT NOT NULL,
  "organizationId" TEXT NOT NULL,
  "courseId" TEXT NOT NULL,
  "userId" TEXT NOT NULL,
  "outcome" TEXT NOT NULL,
  "durationMs" INTEGER NOT NULL,
  "sourceCount" INTEGER NOT NULL DEFAULT 0,
  "payloadHash" TEXT,
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "MaxAiEvent_pkey" PRIMARY KEY ("id")
);
CREATE INDEX "MaxAiEvent_organizationId_courseId_createdAt_idx" ON "MaxAiEvent"("organizationId", "courseId", "createdAt");
ALTER TABLE "MaxAiEvent" ADD CONSTRAINT "MaxAiEvent_courseId_fkey" FOREIGN KEY ("courseId") REFERENCES "Course"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "MaxAiEvent" ADD CONSTRAINT "MaxAiEvent_userId_fkey" FOREIGN KEY ("userId") REFERENCES "User"("id") ON DELETE CASCADE ON UPDATE CASCADE;
ALTER TABLE "MaxAiEvent" ADD CONSTRAINT "MaxAiEvent_organizationId_fkey" FOREIGN KEY ("organizationId") REFERENCES "Organization"("id") ON DELETE CASCADE ON UPDATE CASCADE;

CREATE TABLE "MaxAiFeedback" (
  "eventId" TEXT NOT NULL,
  "question" TEXT NOT NULL,
  "answer" TEXT NOT NULL,
  "sourcesJson" TEXT NOT NULL,
  "reason" TEXT NOT NULL,
  "comment" TEXT,
  "verdict" TEXT NOT NULL DEFAULT 'PENDING',
  "reviewComment" TEXT,
  "reviewedById" TEXT,
  "reviewedAt" TIMESTAMP(3),
  "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "MaxAiFeedback_pkey" PRIMARY KEY ("eventId")
);
ALTER TABLE "MaxAiFeedback" ADD CONSTRAINT "MaxAiFeedback_eventId_fkey" FOREIGN KEY ("eventId") REFERENCES "MaxAiEvent"("id") ON DELETE CASCADE ON UPDATE CASCADE;
